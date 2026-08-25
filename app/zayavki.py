"""Обогащение карточек данными из выгрузки заявок с сайта 2018–2026.

Что это. Владелец 25.08 выгрузил все заявки с сайта за восемь лет: 3128
строк, 2219 уникальных телефонов. В них есть то, чего в CRM часто нет:
дата рождения ребёнка (452 заявки), email (1025), имя ребёнка отдельно
от имени родителя, интересующее направление.

Что делает модуль. Сопоставляет заявки с карточками по последним десяти
цифрам телефона и дописывает недостающее — НЕ ЗАТИРАЯ существующее:
дата рождения ставится, только если её нет; email — если пусто; имя
ребёнка идёт в комментарий, а не в поле имени (там своя чистка).

Телефонов, которых в CRM нет вовсе, — отдельный список: это люди,
оставлявшие заявку и потерявшиеся, по свежим из них имеет смысл звонить.

Запуск:
    python -m app.zayavki            — что найдено и что будет сделано
    python -m app.zayavki apply      — дописать в CRM
    python -m app.zayavki list       — собрать лист обзвона по лету 2026
"""

from __future__ import annotations

import csv
import logging
import re
import time
from datetime import date, datetime

from . import sync, taskguard
from .moyklass_client import MoyklassClient

log = logging.getLogger("kidsup.zayavki")

CSV = ("/root/.claude/uploads/f2c35386-c271-55ec-b217-3b85ac2d6607/"
       "5a2c8888-leads8481b83cb588886ed03c342005b394b2bd9b090ad2ba986f"
       "7662cd22cdf1d065.csv")
BIRTHDAY_ALIAS = "birthday"


def phone10(raw: str) -> str | None:
    d = "".join(c for c in (raw or "") if c.isdigit())[-10:]
    return d if len(d) == 10 and d[0] == "9" else None


def _date(raw: str) -> str | None:
    """«14.05.2020» → «2020-05-14». Отсекаем явную ерунду: год до 2005
    или в будущем — это не дата рождения ребёнка."""
    raw = (raw or "").strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            d = datetime.strptime(raw[:10], fmt).date()
        except ValueError:
            continue
        if 2005 <= d.year <= date.today().year:
            return d.isoformat()
    return None


def load() -> dict[str, dict]:
    """Заявки, свёрнутые по телефону: у одного человека их бывает пять,
    берём самое полное и самое свежее."""
    out: dict[str, dict] = {}
    with open(CSV, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f, delimiter=";"):
            p = phone10(r.get("Phone"))
            if not p:
                continue
            cur = out.setdefault(p, {"phone": p, "dates": [], "forms": [],
                                     "birthday": None, "email": None,
                                     "child": None, "parent": None,
                                     "interest": None, "note": None})
            cur["dates"].append((r.get("Date") or "")[:10])
            form = (r.get("formname") or r.get("Input") or "").strip()
            if form:
                cur["forms"].append(form)
            bd = _date(r.get("Дата_рождения_ребенка"))
            if bd and not cur["birthday"]:
                cur["birthday"] = bd
            em = (r.get("Email") or "").strip()
            if em and "@" in em and not cur["email"]:
                cur["email"] = em
            ch = (r.get("Name_2") or "").strip()
            if ch and not cur["child"]:
                cur["child"] = ch
            pa = (r.get("Name") or "").strip()
            if pa and not cur["parent"]:
                cur["parent"] = pa
            it = (r.get("Интересующее_занятие") or "").strip()
            if it and not cur["interest"]:
                cur["interest"] = it
            tx = (r.get("Textarea") or r.get("Дополнительные_комментарии") or "").strip()
            if tx and not cur["note"]:
                cur["note"] = tx[:180]
    for v in out.values():
        v["last"] = max(v["dates"]) if v["dates"] else ""
        v["first"] = min(v["dates"]) if v["dates"] else ""
        v["count"] = len(v["dates"])
    return out


def match(mk) -> tuple[list[dict], list[dict]]:
    """(что дописать в существующие карточки, кого в CRM нет вовсе)."""
    leads = load()
    users = taskguard.pull_all(mk, "/v1/company/users", "users", cache_hours=2)
    idx: dict[str, dict] = {}
    for u in users:
        p = phone10(u.get("phone"))
        if p:
            idx.setdefault(p, u)
    to_fill, missing = [], []
    for p, lead in leads.items():
        u = idx.get(p)
        if not u:
            missing.append(lead)
            continue
        have_bd = any(a.get("attributeAlias") == BIRTHDAY_ALIAS and a.get("value")
                      for a in (u.get("attributes") or []))
        need = {}
        if lead["birthday"] and not have_bd:
            need["birthday"] = lead["birthday"]
        if lead["email"] and not (u.get("email") or "").strip():
            need["email"] = lead["email"]
        if need or lead["child"] or lead["interest"]:
            to_fill.append({"uid": u["id"], "user": u, "lead": lead, "need": need})
    return to_fill, missing


def _bd_attr_id(mk) -> int | None:
    try:
        r = mk.get("/v1/company/userAttributes")
        items = (r.get("userAttributes") if isinstance(r, dict) else r) or []
        for a in items:
            if a.get("alias") == BIRTHDAY_ALIAS:
                return a["id"]
    except Exception:
        pass
    return None


def apply(dry: bool = True, limit: int = 0) -> dict:
    mk = MoyklassClient(sync.get_api_key())
    stat = {"карточек": 0, "др": 0, "email": 0, "комментариев": 0, "ошибок": 0}
    try:
        to_fill, missing = match(mk)
        # Уже обогащённые пропускаем: match() каждый раз возвращает всех, у
        # кого есть что добавить, и без этой проверки повторный запуск
        # дописывал бы тот же комментарий второй раз.
        done = set()
        try:
            cm = mk.get("/v1/company/userComments", {"limit": 500})
            for x in ((cm.get("userComments") if isinstance(cm, dict) else cm) or []):
                if str(x.get("comment") or "").startswith("Заявки с сайта ("):
                    done.add(x.get("userId"))
        except Exception:
            pass
        to_fill = [x for x in to_fill if x["uid"] not in done]
        stat["карточек"] = len(to_fill)
        stat["нет в CRM"] = len(missing)
        stat["уже было"] = len(done)
        if dry:
            return stat
        bd_id = _bd_attr_id(mk)
        for it in (to_fill[:limit] if limit else to_fill):
            uid, lead, need = it["uid"], it["lead"], it["need"]
            try:
                fields = {}
                if need.get("email"):
                    fields["email"] = need["email"]
                if need.get("birthday") and bd_id:
                    fields["attributes"] = [{"attributeId": bd_id,
                                             "value": need["birthday"]}]
                if fields:
                    mk.safe_update_user(uid, **fields)
                    stat["др"] += 1 if need.get("birthday") else 0
                    stat["email"] += 1 if need.get("email") else 0
                bits = []
                if lead["child"]:
                    bits.append(f"ребёнок: {lead['child']}")
                if lead["parent"]:
                    bits.append(f"родитель: {lead['parent']}")
                if lead["interest"]:
                    bits.append(f"интерес: {lead['interest']}")
                if lead["forms"]:
                    bits.append("формы: " + ", ".join(dict.fromkeys(lead["forms"]))[:120])
                if lead["note"]:
                    bits.append(f"комментарий: {lead['note']}")
                if bits:
                    mk.post("/v1/company/userComments", {
                        "userId": uid, "showToUser": False,
                        "comment": (f"Заявки с сайта ({lead['count']} шт., "
                                    f"{lead['first']} — {lead['last']}). "
                                    + "; ".join(bits))[:1000]})
                    stat["комментариев"] += 1
            except Exception as e:
                stat["ошибок"] += 1
                log.warning("uid=%s: %s", uid, str(e)[:80])
            time.sleep(0.3)
    finally:
        mk.close()
    return stat


CSS = """
body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:12px;color:#222}
h1{font-size:19px;margin:0 0 3px} .sub{color:#666;font-size:12px;margin-bottom:9px}
table{border-collapse:collapse;width:100%} thead{display:table-header-group}
th{background:#312783;color:#fff;font-size:11px;padding:5px 4px;text-align:left}
td{border-bottom:1px solid #ddd;padding:6px 4px;font-size:11pt;vertical-align:top}
.ph{font-size:12.5pt;font-weight:600;white-space:nowrap}
.res{width:170px;border-bottom:1px solid #999} .new{color:#B26F00;font-weight:600}
@media print{body{margin:6px}}
"""


def build_list(since: str = "2026-06-01") -> int:
    """Лист обзвона: заявки этого лета, где до сих пор нет записи на год."""
    import html as _html
    from pathlib import Path
    mk = MoyklassClient(sync.get_api_key())
    try:
        leads = {p: v for p, v in load().items() if v["last"] >= since}
        users = taskguard.pull_all(mk, "/v1/company/users", "users", cache_hours=2)
        joins = taskguard.pull_all(mk, "/v1/company/joins", "joins")
        rc = mk.get("/v1/company/classes", {"limit": 500})
        cls = {c["id"]: (c.get("name") or "")
               for c in (rc.get("classes") if isinstance(rc, dict) else rc)}
    finally:
        mk.close()
    idx = {}
    for u in users:
        p = phone10(u.get("phone"))
        if p:
            idx.setdefault(p, u)
    booked = {j["userId"] for j in joins
              if cls.get(j.get("classId"), "").startswith("2627")
              and j.get("statusId") in {2, 50509, 58131, 58132, 83760}}
    rows = []
    for p, lead in sorted(leads.items(), key=lambda x: -len(x[1]["dates"])):
        u = idx.get(p)
        if u and (u["id"] in booked or u.get("clientStateId") in
                  (146328, 125954, 125957)):
            continue
        forms = ", ".join(dict.fromkeys(lead["forms"]))[:60]
        rows.append(
            f"<tr><td>{_html.escape((lead['child'] or lead['parent'] or '')[:28])}"
            f"{'' if u else ' <span class=new>нет в CRM</span>'}</td>"
            f"<td class=ph>+7{p}</td>"
            f"<td>{_html.escape(forms)}</td><td>{lead['last']}</td>"
            f"<td>{lead['count']}</td><td class=res></td></tr>")
    body = (f"<style>{CSS}</style><h1>Заявки с сайта этим летом — обзвон</h1>"
            f"<div class=sub>{len(rows)} человек: оставляли заявку с "
            f"{since}, на 2026/27 не записаны. Кто «нет в CRM» — карточки "
            f"не существует, завести при разговоре. Печатать в альбомной.</div>"
            "<table><thead><tr><th>Кто</th><th>Телефон</th><th>Что оставляли</th>"
            "<th>Последняя заявка</th><th>Заявок</th><th>Итог разговора</th>"
            "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>")
    p = Path(__file__).resolve().parent.parent / "docs" / "zayavki_leto.html"
    p.write_text(body, encoding="utf-8")
    log.info("%s: %d строк", p, len(rows))
    return len(rows)


def build_fresh(since: str = "2026-08-01") -> int:
    """Свежие заявки с сайта, по которым мы так и не поговорили.

    25.08 владелец попросил проверить, как отрабатываются заявки, и картина
    оказалась плохой: из шестнадцати августовских одиннадцать никто не
    набрал, а у пяти человек даже карточки в CRM нет. Причина системная —
    заявки с сайта в CRM не попадали вовсе (в интеграцию Roistat приходят
    только звонки), и увидеть их можно было лишь в выгрузке Тильды руками.

    «Отработана» = есть запись на новый сезон ИЛИ состоялся разговор
    дольше двадцати секунд. Недозвон отработкой не считается: человек
    оставил заявку и остался без ответа.

    Лагерь исключаем — сезон кончился, звать туда уже некуда."""
    import html as _html
    from datetime import date as _date, datetime as _dt, timedelta as _td
    from pathlib import Path
    from . import mango
    mk = MoyklassClient(sync.get_api_key())
    try:
        leads = {p: v for p, v in load().items() if v["last"] >= since}
        users = taskguard.pull_all(mk, "/v1/company/users", "users", cache_hours=2)
        joins = taskguard.pull_all(mk, "/v1/company/joins", "joins")
        rc = mk.get("/v1/company/classes", {"limit": 500})
        cls = {c["id"]: (c.get("name") or "")
               for c in (rc.get("classes") if isinstance(rc, dict) else rc)}
    finally:
        mk.close()
    booked = {j["userId"] for j in joins
              if cls.get(j.get("classId"), "").startswith("2627")
              and j.get("statusId") in {2, 50509, 58131, 58132, 83760}
              and "аявк" not in cls.get(j.get("classId"), "").lower()}
    idx = {}
    for u in users:
        p = phone10(u.get("phone"))
        if p:
            idx.setdefault(p, u)
    talked = set()
    for dd in range(0, 25):
        day = _date.today() - _td(days=dd)
        try:
            rows = mango.calls(_dt.combine(day, _dt.min.time()),
                               _dt.combine(day, _dt.max.time()))
        except Exception:
            continue
        for r in rows:
            n = (r.get("to_num") if r.get("from_ext") else r.get("from_num")) or ""
            d = "".join(c for c in str(n) if c.isdigit())[-10:]
            dur = (r["finish"] - r["answer"]) if r.get("answer") else 0
            if len(d) == 10 and dur >= 20:
                talked.add(d)
    rows_out = []
    for p, lead in sorted(leads.items(), key=lambda x: x[1]["last"], reverse=True):
        forms = ", ".join(dict.fromkeys(lead["forms"]))
        if "агер" in forms.lower():
            continue                       # лагерь кончился
        if any(t in forms.lower() for t in ("тест", "nест", "текст")):
            continue                       # проверочные отправки формы
        u = idx.get(p)
        if u and (u["id"] in booked or u.get("clientStateId") in (146328, 125954, 125957)):
            continue
        if p in talked:
            continue
        kid = lead["child"] or ""
        who = (kid or lead["parent"] or (u.get("name") if u else "") or "")[:26]
        bd = lead["birthday"] or ""
        age = ""
        if bd:
            try:
                age = "%g" % round((_date(2026, 9, 1)
                                    - _date.fromisoformat(bd)).days / 365.25, 1)
                age = age.replace(".", ",")
            except ValueError:
                pass
        rows_out.append(
            f"<tr><td>{_html.escape(who) or '—'}"
            f"{'' if u else ' <span class=new>нет карточки</span>'}</td>"
            f"<td class=ag>{age or '—'}</td>"
            f"<td class=ph>+7{p}</td>"
            f"<td>{_html.escape(forms[:46])}</td>"
            f"<td>{lead['last'][8:10]}.{lead['last'][5:7]}</td>"
            f"<td>{_html.escape((lead['interest'] or lead['note'] or '')[:44])}</td>"
            f"<td class=res></td></tr>")
    body = (f"<style>{CSS}</style>"
            f"<h1>Заявки с сайта, по которым мы не поговорили</h1>"
            f"<div class=sub>{len(rows_out)} человек оставили заявку с "
            f"{since[8:10]}.{since[5:7]} и до сих пор не записаны, а разговора "
            f"с ними не было — только недозвоны или вообще ничего. Лагерь "
            f"и тестовые отправки формы исключены. Свежие сверху: заявке "
            f"вчерашнего дня цена выше, чем трёхнедельной. У кого «нет "
            f"карточки» — завести при разговоре. Печатать в альбомной.</div>"
            "<table><thead><tr><th>Кто</th><th>Возраст<br>на 1.09</th>"
            "<th>Телефон</th><th>Форма на сайте</th><th>Дата</th>"
            "<th>Что просили</th><th>Итог разговора</th></tr></thead><tbody>"
            + "".join(rows_out) + "</tbody></table>")
    p_out = Path(__file__).resolve().parent.parent / "docs" / "zayavki_svezhie.html"
    p_out.write_text(body, encoding="utf-8")
    log.info("%s: %d строк", p_out, len(rows_out))
    return len(rows_out)


def main():
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if "apply" in sys.argv:
        print(apply(dry=False))
    elif "fresh" in sys.argv:
        print("строк:", build_fresh())
    elif "list" in sys.argv:
        print("строк в листе:", build_list())
    else:
        print(apply(dry=True))


if __name__ == "__main__":
    main()
