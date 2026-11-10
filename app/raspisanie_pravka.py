"""Правка расписания МойКласса по утверждённому плану: удалить и создать занятия.

Решения владельца 10.11.2026: новогодний перерыв 31.12–07.01 и праздники — без
занятий; 10.05.2027 рабочий день; у пустых групп удалить занятия, группы
оставить. План собирается и проверяется отдельно (только чтение), сюда
приходит готовый список операций. Здесь — только исполнение с заслонами.

Удаление {"op": "delete", "lesson_id", "class_id", "date", "begin", "records",
"record_ids"?}:
• живое занятие должно совпасть с планом: группа, дата, время, статус
  «не проведено», тот же состав записей (число, а если в плане есть
  record_ids — и сами записи). Ни одной отметки «был», уважительной причины,
  пробной, бесплатной записи или отработки. Не совпало — не трогаем;
• если в ответе нет списка records или поля status (параметр includeRecords
  не сработал) — не трогаем: «нет ключа» не значит «ноль записей»;
• у занятия «без записей» состав проверяется ещё раз отдельным запросом
  GET /lessonRecords?lessonId — удаляем, только если оба пусты;
• прошедшие и сегодняшние даты не трогаем никогда;
• перед DELETE полный снимок занятия ложится в журнал запуска на диск
  (JSONL, flush + fsync) — после перезапуска сервера посреди прогона копия
  уже удалённого есть;
• DELETE отвечает пустым 204, а MoyklassClient разбирает тело как JSON —
  поэтому удаление отдельным запросом. Ответ потерялся — проверяем GET: 404
  значит удалено.

Создание {"op": "create", "body": {...POST /lessons...}, "records": [userId],
"template_lessons": [id, id]}:
• у группы в этот день уже есть любое занятие: в то же время — берём его
  («уже было»), в другое — не создаём («пропущено»);
• MoyklassClient.post повторяет запрос, и повтор создания дал бы дубль —
  поэтому POST без повторов; ответ потерялся — несколько раз ищем занятие,
  не нашли — «неизвестно, создано ли», руками;
• детей записываем только тех, кто из плана и сейчас записан на оба
  соседних понедельника (template_lessons): ушедших не пишем, пришедших
  показываем.

По умолчанию dry_run: ничего не пишет. Запуск в фоне; итог и прерванные
запуски — GET /api/raspisanie/pravka; остановка — POST …/stop.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from . import config

log = logging.getLogger("kidsup.raspisanie_pravka")

DIR = config.DATA_DIR / "raspisanie_pravka"
MSK = ZoneInfo("Europe/Moscow")
PAUSE = 0.35
MAX_OPS = 400
POLL = (6, 5.0)   # после потерянного ответа на POST: сколько раз и с какой паузой искать

_lock = threading.Lock()
_state: dict = {"running": False}

NORM = ("будет удалено", "удалено", "будет создано", "создано", "уже было", "уже есть",
        "нет в МК (уже удалено)")


def _today() -> str:
    return datetime.now(MSK).date().isoformat()


def _client():
    from .moyklass_client import MoyklassClient
    from . import sync
    mk = MoyklassClient(sync.get_api_key())
    mk.authenticate()
    return mk


def _raw(mk, method: str, path: str, params: dict | None = None, body: dict | None = None):
    """Один запрос без повторов (на 401 — переавторизация). Возвращает (код, json|None)."""
    time.sleep(PAUSE)
    mk._throttle()
    r = mk._client.request(method, f"{mk.base_url}{path}", params=params, json=body,
                           headers={"x-access-token": mk._token}, timeout=60)
    if r.status_code == 401:
        mk.authenticate()
        time.sleep(PAUSE)
        r = mk._client.request(method, f"{mk.base_url}{path}", params=params, json=body,
                               headers={"x-access-token": mk._token}, timeout=60)
    data = None
    if r.text:
        try:
            data = r.json()
        except ValueError:
            data = None
    return r.status_code, data


def _get(mk, path: str, params: dict | None = None, tries: int = 4):
    """GET с повторами на сбой сети, 429 и 5xx (чтение повторять безопасно)."""
    last = None
    for i in range(tries):
        try:
            code, data = _raw(mk, "GET", path, params)
        except httpx.HTTPError as e:
            last = f"сеть: {type(e).__name__}"
            time.sleep(2 ** i)
            continue
        if code == 429 or code >= 500:
            last = f"HTTP {code}"
            time.sleep(2 ** i)
            continue
        return code, data
    raise RuntimeError(f"GET {path}: {last}")


def _lesson(mk, lesson_id: int):
    code, data = _get(mk, f"/v1/company/lessons/{int(lesson_id)}",
                      {"includeRecords": "true", "includeWorkOffs": "true",
                       "includeParams": "true", "includeTasks": "true", "includeMarks": "true"})
    if code == 404:
        return None
    if code != 200 or not isinstance(data, dict):
        raise RuntimeError(f"GET занятия {lesson_id}: HTTP {code}")
    return data


def _same_day(mk, class_id: int, day: str) -> list[dict]:
    code, data = _get(mk, "/v1/company/lessons",
                      {"date": day, "classId": int(class_id), "includeRecords": "true"})
    if code != 200:
        raise RuntimeError(f"GET занятий {class_id} {day}: HTTP {code}")
    items = data.get("lessons") if isinstance(data, dict) else data
    return [x for x in (items or []) if int(x.get("classId") or 0) == int(class_id)
            and str(x.get("date") or "")[:10] == day]


def _records_by_filter(mk, lesson_id: int) -> list | None:
    """Второй, независимый взгляд на состав: GET /lessonRecords?lessonId. None — не удалось."""
    try:
        code, data = _get(mk, "/v1/company/lessonRecords", {"lessonId": int(lesson_id), "limit": 100})
    except RuntimeError:
        return None
    if code != 200:
        return None
    items = data.get("lessonRecords") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return None
    return [x for x in items if int(x.get("lessonId") or lesson_id) == int(lesson_id)]


OSOBYE = ("visit", "goodReason", "test", "free", "missedLessonRecordId", "workOffRecord",
          "missedRecord", "workOffLessonRecordId")


def _check_delete(op: dict, L: dict) -> str:
    """Пусто — занятие совпадает с планом; иначе причина, почему не трогаем."""
    if not isinstance(L.get("records"), list):
        return "в ответе нет списка записей (includeRecords не сработал)"
    if "status" not in L:
        return "в ответе нет статуса занятия"
    recs = L["records"]
    why = []
    if int(L.get("classId") or 0) != int(op["class_id"]):
        why.append(f"группа {L.get('classId')} ≠ {op['class_id']}")
    if str(L.get("date") or "")[:10] != op["date"]:
        why.append(f"дата {L.get('date')} ≠ {op['date']}")
    if op.get("begin") and str(L.get("beginTime") or "")[:5] != op["begin"]:
        why.append(f"время {L.get('beginTime')} ≠ {op['begin']}")
    if int(L.get("status") or 0) != 0:
        why.append(f"статус {L.get('status')} (занятие проведено)")
    if len(recs) != int(op.get("records") or 0):
        why.append(f"записей {len(recs)}, в плане {op.get('records') or 0}")
    elif op.get("record_ids") is not None:
        live = sorted(int(r.get("id") or 0) for r in recs)
        if live != sorted(int(x) for x in op["record_ids"]):
            why.append("состав записей не тот, что в плане")
    bad = sorted({k for r in recs for k in OSOBYE if r.get(k)})
    if bad:
        why.append("у записей есть " + ", ".join(bad))
    if L.get("homeTask") or L.get("lessonTask"):
        why.append("к занятию привязано задание")
    return "; ".join(why)


class Zhurnal:
    """Журнал запуска на диске: строка на событие, flush + fsync до следующего шага."""

    def __init__(self, path):
        self.path = path
        DIR.mkdir(parents=True, exist_ok=True)
        self.f = open(path, "a", encoding="utf-8")

    def write(self, obj: dict) -> None:
        self.f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self.f.flush()
        os.fsync(self.f.fileno())

    def close(self) -> None:
        try:
            self.f.close()
        except Exception:  # noqa: BLE001
            pass


def _delete(mk, op: dict, dry: bool, res: dict, zh: Zhurnal) -> dict:
    lid = int(op["lesson_id"])
    if op["date"] <= _today():
        return {"итог": "пропущено", "почему": "прошедшая или сегодняшняя дата"}
    L = _lesson(mk, lid)
    if L is None:
        return {"итог": "нет в МК (уже удалено)"}
    why = _check_delete(op, L)
    if why:
        return {"итог": "пропущено", "почему": why}
    if not L["records"]:
        second = _records_by_filter(mk, lid)
        if second is None:
            return {"итог": "пропущено", "почему": "не удалось второй раз проверить, что записей нет"}
        if second:
            return {"итог": "пропущено", "почему": f"по второй проверке записей {len(second)}"}
    zh.write({"снимок": L, "op": op})          # копия на диске до удаления
    res["снимков"] += 1
    if dry:
        return {"итог": "будет удалено", "записей": len(L["records"])}
    try:
        code, _ = _raw(mk, "DELETE", f"/v1/company/lessons/{lid}")
    except httpx.HTTPError as e:
        code = f"сеть: {type(e).__name__}"
    if code not in (200, 204):
        try:
            still = _lesson(mk, lid)
        except RuntimeError as e:
            return {"итог": "неизвестно, удалено ли", "почему": f"DELETE: {code}; проверка: {e}"}
        if still is not None:
            return {"итог": "ошибка", "почему": f"DELETE: {code}"}
    out = {"итог": "удалено", "записей": len(L["records"])}
    recs = L["records"]
    if recs and not res.get("записи_проверены"):
        # Один раз: исчезают ли записи вместе с занятием (в спецификации не сказано)
        try:
            left, unclear = [], []
            for r in recs:
                rid = r.get("id")
                if not rid:
                    continue
                c, _ = _get(mk, f"/v1/company/lessonRecords/{int(rid)}")
                if c == 200:
                    left.append(rid)
                elif c != 404:
                    unclear.append(f"{rid}: HTTP {c}")
            res["записи_проверены"] = True
            if left:
                res["стоп"] = f"после удаления занятия {lid} остались записи {left} — дальше не идём"
                out["итог"] = "удалено, записи остались"
            elif unclear:
                res["стоп"] = f"не удалось проверить записи занятия {lid}: {unclear}"
                out["итог"] = "удалено, записи не проверены"
        except Exception as e:  # noqa: BLE001
            res["стоп"] = f"не удалось проверить записи занятия {lid}: {type(e).__name__}: {str(e)[:120]}"
            out["итог"] = "удалено, записи не проверены"
    return out


def _live_kids(mk, template_ids: list) -> set[int] | None:
    """Кто записан на оба соседних понедельника сейчас. None — шаблон не найден."""
    sets = []
    for tid in template_ids or []:
        L = _lesson(mk, int(tid))
        if L is None or not isinstance(L.get("records"), list):
            return None
        sets.append({int(r.get("userId") or 0) for r in L["records"]})
    if not sets:
        return None
    return set.intersection(*sets)


def _create(mk, op: dict, dry: bool, res: dict, zh: Zhurnal) -> dict:
    b = dict(op["body"])
    day, cid, beg = b["date"], int(b["classId"]), b["beginTime"]
    if day <= _today():
        return {"итог": "пропущено", "почему": "прошедшая или сегодняшняя дата"}
    plan_kids = [int(u) for u in op.get("records") or []]
    live = _live_kids(mk, op.get("template_lessons") or [])
    if live is None:
        return {"итог": "пропущено", "почему": "соседние понедельники (шаблоны) не найдены"}
    want = [u for u in plan_kids if u in live]
    ushli = [u for u in plan_kids if u not in live]
    prishli = sorted(live - set(plan_kids))
    info = {"детей_в_плане": len(plan_kids), "записать": len(want), "ушли": ushli, "пришли": prishli}
    existing = _same_day(mk, cid, day)
    same = [x for x in existing if str(x.get("beginTime") or "")[:5] == beg]
    if len(same) > 1:
        return {"итог": "дубль", "почему": f"уже {len(same)} занятия: {[x.get('id') for x in same]}", **info}
    if existing and not same:
        return {"итог": "пропущено", "почему": "в группе уже есть занятие в этот день: "
                + ", ".join(f"{str(x.get('beginTime'))[:5]} (id {x.get('id')})" for x in existing), **info}
    if dry:
        return {"итог": "уже есть" if same else "будет создано", **info}
    if same:
        L = same[0]
        out = {"итог": "уже было", "lesson_id": L.get("id"), **info}
    else:
        zh.write({"создаю": b})
        try:
            code, data = _raw(mk, "POST", "/v1/company/lessons", body=b)
        except httpx.HTTPError as e:
            code, data = f"сеть: {type(e).__name__}", None
        if code in (200, 201) and isinstance(data, dict) and data.get("id"):
            L = data
        else:
            found = []
            for _ in range(POLL[0]):
                try:
                    found = [x for x in _same_day(mk, cid, day) if str(x.get("beginTime") or "")[:5] == beg]
                except RuntimeError:
                    found = []
                if found:
                    break
                time.sleep(POLL[1])
            if not found:
                return {"итог": "неизвестно, создано ли", "почему": f"POST: {code} {str(data)[:160]}; "
                        "не повторять — проверить в МойКлассе руками", **info}
            if len(found) > 1:
                return {"итог": "дубль", "почему": f"после POST {len(found)} занятия: "
                        f"{[x.get('id') for x in found]}", **info}
            L = found[0]
        out = {"итог": "создано", "lesson_id": L.get("id"), **info}
    zh.write({"занятие": out.get("lesson_id"), "итог": out["итог"]})
    lid = int(L["id"])
    dobavleno, oshibki = [], []
    try:
        full = _lesson(mk, lid) or L
        est = {int(r.get("userId") or 0) for r in full.get("records") or []}
        out["сами_записались"] = len(est & set(want))
        for uid in want:
            if uid in est:
                continue
            try:
                code, data = _raw(mk, "POST", "/v1/company/lessonRecords",
                                  body={"lessonId": lid, "userId": uid, "free": False, "test": False})
            except httpx.HTTPError as e:
                code, data = f"сеть: {type(e).__name__}", None
            if code in (200, 201):
                dobavleno.append(uid)
                continue
            again = _lesson(mk, lid) or {}
            if uid in {int(r.get("userId") or 0) for r in again.get("records") or []}:
                dobavleno.append(uid)
            else:
                oshibki.append(f"{uid}: {code} {str(data)[:120]}")
    except Exception as e:  # noqa: BLE001
        oshibki.append(f"запись детей прервана: {type(e).__name__}: {str(e)[:160]}")
    out.update({"дописано": len(dobavleno), "не_записаны": oshibki})
    if oshibki:
        out["итог"] += ", не все дети записаны"
    return out


def _worker(ops: list[dict], dry: bool, label: str, zh: Zhurnal, res: dict) -> None:
    mk = None
    try:
        mk = _client()
        for op in ops:
            if res.get("стоп") or _state.get("stop"):
                if _state.get("stop") and not res.get("стоп"):
                    res["стоп"] = "остановлено вручную"
                break
            try:
                if op.get("op") == "delete":
                    r = _delete(mk, op, dry, res, zh)
                else:
                    r = _create(mk, op, dry, res, zh)
            except Exception as e:  # noqa: BLE001
                r = {"итог": "ошибка", "почему": f"{type(e).__name__}: {str(e)[:200]}"}
            r = {"op": op.get("op"),
                 "lesson_id": r.pop("lesson_id", None) or op.get("lesson_id"),
                 "class_id": op.get("class_id") or (op.get("body") or {}).get("classId"),
                 "date": op.get("date") or (op.get("body") or {}).get("date"), **r}
            res["операции"].append(r)
            res["сделано"] += 1
            res["итоги"][r["итог"]] = res["итоги"].get(r["итог"], 0) + 1
            res["шаг"] = time.strftime("%Y-%m-%d %H:%M:%S")
            try:
                zh.write({"операция": r})
            except Exception as e:  # noqa: BLE001
                res["стоп"] = f"журнал не пишется: {e}"
    except Exception as e:  # noqa: BLE001
        res["стоп"] = f"{type(e).__name__}: {str(e)[:300]}"
    finally:
        if mk is not None:
            try:
                mk.close()
            except Exception:  # noqa: BLE001
                pass
        res["конец"] = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            zh.write({"конец": res["конец"], "итоги": res["итоги"], "стоп": res.get("стоп")})
        except Exception:  # noqa: BLE001
            pass
        zh.close()
        _state["running"] = False


def start(ops: list[dict], dry_run: bool = True, label: str = "") -> dict:
    if not isinstance(ops, list) or not ops:
        raise ValueError("нужен непустой список ops")
    if len(ops) > MAX_OPS:
        raise ValueError(f"не больше {MAX_OPS} операций за запуск")
    today = _today()
    for op in ops:
        if op.get("op") == "delete":
            if not op.get("lesson_id") or not op.get("class_id") or not op.get("date"):
                raise ValueError("удаление: нужны lesson_id, class_id, date (и begin, records)")
            if str(op["date"]) <= today:
                raise ValueError(f"удаление на прошедшую/сегодняшнюю дату: {op['lesson_id']} {op['date']}")
        elif op.get("op") == "create":
            b = op.get("body") or {}
            need = ("classId", "date", "beginTime", "endTime", "filialId", "roomId")
            if any(not b.get(k) for k in need):
                raise ValueError(f"создание: в body нужны {need}")
            if str(b["date"]) <= today:
                raise ValueError(f"создание на прошедшую/сегодняшнюю дату: {b['date']}")
            if not op.get("template_lessons"):
                raise ValueError("создание: нужны template_lessons (соседние понедельники)")
        else:
            raise ValueError("op — delete или create")
    dry = bool(dry_run)
    with _lock:
        if _state.get("running"):
            raise ValueError("предыдущий запуск ещё идёт")
        _state.update({"running": True, "stop": False})
    try:
        path = DIR / f"{time.strftime('%Y%m%d_%H%M%S')}_{int(time.time() * 1000) % 1000:03d}_{'dry' if dry else 'run'}.jsonl"
        zh = Zhurnal(path)
        res = {"начало": time.strftime("%Y-%m-%d %H:%M:%S"), "dry_run": dry, "метка": label,
               "всего": len(ops), "сделано": 0, "итоги": {}, "операции": [], "снимков": 0,
               "файл": str(path)}
        zh.write({"запуск": {k: res[k] for k in ("начало", "dry_run", "метка", "всего")}})
        _state["res"] = res
        threading.Thread(target=_worker, args=(ops, dry, label, zh, res), daemon=True).start()
    except BaseException:
        with _lock:
            _state["running"] = False
        raise
    return {"ok": True, "запущено": len(ops), "dry_run": dry, "файл": str(path)}


def stop() -> dict:
    if not _state.get("running"):
        return {"ok": False, "почему": "ничего не идёт"}
    _state["stop"] = True
    return {"ok": True, "остановится": "после текущей операции"}


def _from_disk() -> dict:
    """Последний запуск по журналу на диске — после перезапуска сервера."""
    files = sorted(DIR.glob("*.jsonl")) if DIR.exists() else []
    if not files:
        return {}
    f = files[-1]
    head, ops, end, snaps = {}, [], None, 0
    for line in f.read_text(encoding="utf-8").splitlines():
        try:
            x = json.loads(line)
        except ValueError:
            continue
        if "запуск" in x:
            head = x["запуск"]
        elif "операция" in x:
            ops.append(x["операция"])
        elif "снимок" in x:
            snaps += 1
        elif "конец" in x:
            end = x
    itogi: dict = {}
    for o in ops:
        itogi[o.get("итог")] = itogi.get(o.get("итог"), 0) + 1
    out = {**head, "файл": str(f), "сделано": len(ops), "итоги": itogi, "снимков": snaps,
           "операции": ops}
    if end:
        out.update({"конец": end.get("конец"), "стоп": end.get("стоп")})
    else:
        out["прерван"] = "конец не записан — сервер перезапускался посреди прогона"
    return out


def status(full: bool = False) -> dict:
    running = bool(_state.get("running"))
    res = _state.get("res") or _from_disk()
    out = {"running": running, **{k: v for k, v in res.items() if k != "операции"}}
    ops = res.get("операции") or []
    out["последние"] = ops[-5:]
    out["не_по_плану"] = [o for o in ops if o.get("итог") not in NORM][:80]
    kids_plan = sum(int(o.get("детей_в_плане") or 0) for o in ops)
    if kids_plan:
        out["дети"] = {"в_плане": kids_plan,
                       "записано": sum(int(o.get("сами_записались") or 0) + int(o.get("дописано") or 0)
                                       for o in ops),
                       "не_записаны": [(o.get("class_id"), o.get("lesson_id"), x)
                                       for o in ops for x in (o.get("не_записаны") or [])],
                       "ушли": sum(len(o.get("ушли") or []) for o in ops),
                       "пришли": [(o.get("class_id"), o.get("пришли")) for o in ops if o.get("пришли")]}
    if full:
        out["операции"] = ops
    return out
