"""Правка расписания МойКласса по утверждённому плану: удалить и создать занятия.

Решения владельца 10.11.2026: новогодний перерыв 31.12–07.01 и праздники — без
занятий; 10.05.2027 рабочий день; у пустых групп удалить занятия, группы
оставить. План собирается и проверяется отдельно (только чтение), сюда
приходит готовый список операций. Здесь — только исполнение с заслонами:

• каждое удаление сверяется с живым занятием: та же группа, дата, время,
  статус «не проведено», ровно столько записей, сколько в плане, и ни у одной
  нет отметки «был». Что-то не совпало — занятие не трогаем, пишем почему;
• перед удалением полный снимок занятия с записями ложится в журнал запуска
  (data/raspisanie_pravka/), из него занятие можно восстановить;
• DELETE возвращает пустой ответ 204, а MoyklassClient разбирает тело как
  JSON — поэтому удаление отдельным запросом. Ответ потерялся — проверяем GET:
  404 значит удалено;
• MoyklassClient.post повторяет запрос при сбое сети, и повтор создания
  занятия дал бы дубль. Поэтому перед созданием ищем такое же занятие
  (группа + дата + время), создаём без повторов, при ошибке ищем снова;
• записи детей в новое занятие — тоже с проверкой «уже записан?»;
• на первом удалённом занятии с записями проверяем, что записи исчезли
  вместе с ним (GET записи → 404). Если запись осталась — останавливаемся.

По умолчанию dry_run: ничего не пишет, только сверяет и показывает, что
будет сделано. Запуск в фоне, итог — GET /api/raspisanie/pravka.
"""
from __future__ import annotations

import json
import logging
import threading
import time

import httpx

from . import config

log = logging.getLogger("kidsup.raspisanie_pravka")

DIR = config.DATA_DIR / "raspisanie_pravka"
PAUSE = 0.35
MAX_OPS = 400

_lock = threading.Lock()
_state: dict = {"running": False}


def _client():
    from .moyklass_client import MoyklassClient
    from . import sync
    mk = MoyklassClient(sync.get_api_key())
    mk.authenticate()
    return mk


def _raw(mk, method: str, path: str, params: dict | None = None, body: dict | None = None):
    """Один запрос без повторов. Возвращает (код, json|None)."""
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


def _get(mk, path: str, params: dict | None = None, tries: int = 3):
    """GET с повторами (чтение безопасно повторять)."""
    last = None
    for i in range(tries):
        try:
            return _raw(mk, "GET", path, params)
        except httpx.HTTPError as e:
            last = e
            time.sleep(2 ** i)
    raise RuntimeError(f"GET {path}: {last}")


def _lesson(mk, lesson_id: int):
    code, data = _get(mk, f"/v1/company/lessons/{int(lesson_id)}", {"includeRecords": "true"})
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


def _check_delete(op: dict, L: dict) -> str:
    """Пусто — занятие совпадает с планом; иначе причина, почему не трогаем."""
    recs = L.get("records") or []
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
    if any(r.get("visit") for r in recs):
        why.append("есть отметка «был»")
    return "; ".join(why)


def _delete(mk, op: dict, dry: bool, res: dict) -> dict:
    lid = int(op["lesson_id"])
    L = _lesson(mk, lid)
    if L is None:
        return {"итог": "нет в МК (уже удалено)"}
    why = _check_delete(op, L)
    if why:
        return {"итог": "пропущено", "почему": why}
    res["снимки"].append(L)
    if dry:
        return {"итог": "будет удалено", "записей": len(L.get("records") or [])}
    try:
        code, _ = _raw(mk, "DELETE", f"/v1/company/lessons/{lid}")
    except httpx.HTTPError as e:
        code = f"сеть: {type(e).__name__}"
    if code not in (200, 204):
        # ответ мог потеряться, а занятие — удалиться; смотрим, есть ли оно
        if _lesson(mk, lid) is not None:
            return {"итог": "ошибка", "почему": f"DELETE: {code}"}
    out = {"итог": "удалено", "записей": len(L.get("records") or [])}
    recs = L.get("records") or []
    if recs and not res.get("записи_проверены"):
        left = []
        for r in recs:
            rid = r.get("id")
            if rid:
                c, _ = _get(mk, f"/v1/company/lessonRecords/{int(rid)}")
                if c != 404:
                    left.append(rid)
        res["записи_проверены"] = True
        if left:
            res["стоп"] = f"после удаления занятия {lid} остались записи {left} — дальше не идём"
            out["итог"] = "удалено, записи остались"
    return out


def _create(mk, op: dict, dry: bool, res: dict) -> dict:
    b = dict(op["body"])
    day, cid, beg = b["date"], int(b["classId"]), b["beginTime"]
    want = [int(u) for u in op.get("records") or []]
    have = [x for x in _same_day(mk, cid, day) if str(x.get("beginTime") or "")[:5] == beg]
    if dry:
        return {"итог": "уже есть" if have else "будет создано", "детей_в_плане": len(want)}
    if have:
        L = have[0]
        out = {"итог": "уже было", "lesson_id": L.get("id")}
    else:
        try:
            code, data = _raw(mk, "POST", "/v1/company/lessons", body=b)
        except httpx.HTTPError as e:
            code, data = f"сеть: {type(e).__name__}", None
        if code in (200, 201) and isinstance(data, dict) and data.get("id"):
            L = data
        else:
            again = [x for x in _same_day(mk, cid, day) if str(x.get("beginTime") or "")[:5] == beg]
            if not again:
                return {"итог": "ошибка", "почему": f"POST: {code} {str(data)[:200]}"}
            L = again[0]
        out = {"итог": "создано", "lesson_id": L.get("id")}
    lid = int(L["id"])
    full = _lesson(mk, lid) or L
    est = {int(r.get("userId") or 0) for r in full.get("records") or []}
    dobavleno, oshibki = [], []
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
        full = _lesson(mk, lid) or {}
        if uid in {int(r.get("userId") or 0) for r in full.get("records") or []}:
            dobavleno.append(uid)
        else:
            oshibki.append(f"{uid}: {code} {str(data)[:120]}")
    out.update({"сами_записались": len(est & set(want)), "дописано": len(dobavleno),
                "не_записаны": oshibki})
    return out


def _worker(ops: list[dict], dry: bool, label: str) -> None:
    res = {"начало": time.strftime("%Y-%m-%d %H:%M:%S"), "dry_run": dry, "метка": label,
           "всего": len(ops), "сделано": 0, "итоги": {}, "операции": [], "снимки": []}
    _state.update({"running": True, "res": res})
    mk = None
    try:
        mk = _client()
        for op in ops:
            if res.get("стоп"):
                break
            try:
                if op.get("op") == "delete":
                    r = _delete(mk, op, dry, res)
                elif op.get("op") == "create":
                    r = _create(mk, op, dry, res)
                else:
                    r = {"итог": "неизвестная операция"}
            except Exception as e:  # noqa: BLE001
                r = {"итог": "ошибка", "почему": f"{type(e).__name__}: {str(e)[:200]}"}
            r = {"op": op.get("op"), "lesson_id": op.get("lesson_id"), "class_id": op.get("class_id")
                 or (op.get("body") or {}).get("classId"), "date": op.get("date")
                 or (op.get("body") or {}).get("date"), **r}
            res["операции"].append(r)
            res["сделано"] += 1
            res["итоги"][r["итог"]] = res["итоги"].get(r["итог"], 0) + 1
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
            DIR.mkdir(parents=True, exist_ok=True)
            f = DIR / f"{time.strftime('%Y%m%d_%H%M%S')}_{'dry' if dry else 'run'}.json"
            f.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
            res["файл"] = str(f)
        except Exception as e:  # noqa: BLE001
            log.warning("журнал правки не записан: %s", e)
        _state["running"] = False


def start(ops: list[dict], dry_run: bool = True, label: str = "") -> dict:
    if not isinstance(ops, list) or not ops:
        raise ValueError("нужен непустой список ops")
    if len(ops) > MAX_OPS:
        raise ValueError(f"не больше {MAX_OPS} операций за запуск")
    for op in ops:
        if op.get("op") == "delete":
            if not op.get("lesson_id") or not op.get("class_id") or not op.get("date"):
                raise ValueError("удаление: нужны lesson_id, class_id, date (и begin, records)")
        elif op.get("op") == "create":
            b = op.get("body") or {}
            need = ("classId", "date", "beginTime", "endTime", "filialId", "roomId")
            if any(not b.get(k) for k in need):
                raise ValueError(f"создание: в body нужны {need}")
        else:
            raise ValueError("op — delete или create")
    with _lock:
        if _state.get("running"):
            raise ValueError("предыдущий запуск ещё идёт")
        _state["running"] = True
    threading.Thread(target=_worker, args=(ops, bool(dry_run), label), daemon=True).start()
    return {"ok": True, "запущено": len(ops), "dry_run": bool(dry_run)}


def status(full: bool = False) -> dict:
    res = _state.get("res") or {}
    out = {"running": bool(_state.get("running")),
           **{k: v for k, v in res.items() if k not in ("операции", "снимки")}}
    ops = res.get("операции") or []
    out["последние"] = ops[-5:]
    out["не_по_плану"] = [o for o in ops if o.get("итог") not in
                          ("будет удалено", "удалено", "будет создано", "создано", "уже было",
                           "уже есть", "нет в МК (уже удалено)")][:50]
    if full:
        out["операции"] = ops
    return out
