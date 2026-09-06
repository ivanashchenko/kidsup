"""Список обзвона «Английский 4–5 лет, группа Ильи» (запрос Иры 06.09).

Источник: выгрузка карточек docs/rabota/backup/users_2026-09-06.json.gz + записи
в группы сезона (joins_aya.json из API). Только чтение, ничего не отправляет.
Результат: docs/obzvon_aya_4_5.html (страница /base/obzvon_aya_4_5).
"""
import gzip, json, html, datetime, re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCR = Path("/tmp/claude-0/-home-user-kidsup/f2c35386-c271-55ec-b217-3b85ac2d6607/scratchpad")
TODAY = datetime.date(2026, 9, 6)
LIVE = {2, 58132, 83760, 58131}
BORN_FROM, BORN_TO = datetime.date(2020, 9, 1), datetime.date(2022, 12, 31)   # 3 г 8 мес … 6 лет
NO_CALL_STATES = {146328}          # «не писать»
REFUSED = {125957}
STATE = {125951: "новый лид", 345768: "недозвон", 146950: "думает", 125952: "записался", 125955: "учится",
         125957: "отказался", 345759: "архив набора", 146328: "не писать", 125953: "клиент", 125956: "ушёл"}

users = json.load(gzip.open(ROOT / "docs/rabota/backup/users_2026-09-06.json.gz"))
jd = json.load(open(SCR / "joins_aya.json"))
classes = {int(k): v for k, v in jd["classes"].items()}
joins = {int(k): v for k, v in jd["joins"].items()}

by_user: dict[int, list] = {}
for cid, js in joins.items():
    for j in js:
        by_user.setdefault(j["u"], []).append((cid, j["st"], j["at"] or ""))


def bday(u):
    for a in u.get("attributes") or []:
        if a.get("attributeAlias") == "birthday" and a.get("value"):
            try:
                return datetime.date.fromisoformat(a["value"][:10])
            except ValueError:
                return None
    return None


def age_str(b):
    m = (TODAY.year - b.year) * 12 + TODAY.month - b.month - (TODAY.day < b.day)
    return f"{m // 12} г {m % 12} мес"


def is_aya(cid):
    return "АЯ" in classes[cid]["name"]


def short(cid):
    n = classes[cid]["name"]
    n = re.sub(r"^2627_", "", n)
    n = re.sub(r"_\d+-\d+ лет_", " ", n)
    return n[:48]


rows = []
for u in users:
    if not u.get("phone") or u.get("clientStateId") in NO_CALL_STATES:
        continue
    if u["phone"].startswith("7777777") or "дубликат" in (u.get("name") or "").lower():
        continue
    b = bday(u)
    if not b or not (BORN_FROM <= b <= BORN_TO):
        continue
    js = by_user.get(u["id"], [])
    aya_live = [c for c, st, _ in js if is_aya(c) and "Заявк" not in classes[c]["name"]
                and classes[c]["name"].startswith("2627_") and st in LIVE]
    if aya_live:
        continue                                   # уже в английском этого сезона
    aya_zayavka = [c for c, st, _ in js if is_aya(c) and "Заявк" in classes[c]["name"] and st in (50509, 58132, 58131)]
    aya_refused = [c for c, st, _ in js if is_aya(c) and classes[c]["name"].startswith("2627_") and st == 1]
    other_live = [c for c, st, _ in js if not is_aya(c) and "Заявк" not in classes[c]["name"]
                  and classes[c]["name"].startswith("2627_") and st in LIVE]
    aya_last = [c for c, st, _ in js if classes[c]["name"].startswith("АЯ_") and "Заявк" not in classes[c]["name"]]
    other_zayavka = [c for c, st, _ in js if not is_aya(c) and "Заявк" in classes[c]["name"] and st == 50509
                     and classes[c]["name"].startswith("2627_")]
    st = u.get("clientStateId")
    if aya_zayavka:
        seg = "A"
    elif other_live:
        seg = "B"
    elif aya_last:
        seg = "C"
    elif st in REFUSED and aya_refused:
        continue                                   # отказались именно от АЯ в этом сезоне — не трогаем
    elif other_zayavka or st in (125951, 345768, 146950, 125952):
        seg = "D"
    else:
        continue
    rows.append({
        "seg": seg, "id": u["id"], "name": u.get("name") or "", "phone": u["phone"], "born": b,
        "age": age_str(b), "state": STATE.get(st, str(st)),
        "now": sorted(set(short(c) for c in other_live)),
        "last": sorted(set(short(c) for c in aya_last)),
        "zay": sorted(set(short(c) for c in (aya_zayavka + other_zayavka))),
        "touched": (u.get("stateChangedAt") or u.get("updatedAt") or "")[:10],
        "refused_aya": bool(aya_refused),
    })

# одна строка на телефон (братья-сёстры), дети через запятую
byphone: dict[str, list] = {}
for r in rows:
    byphone.setdefault(r["phone"], []).append(r)
merged = []
for p, rs in byphone.items():
    rs.sort(key=lambda r: "ABCD".index(r["seg"]))
    head = dict(rs[0])
    head["kids"] = [f"{r['name']} ({r['age']})" for r in rs]
    head["ids"] = [r["id"] for r in rs]
    merged.append(head)
merged.sort(key=lambda r: ("ABCD".index(r["seg"]), r["touched"]), reverse=False)
for r in merged:
    pass

SEG = {
    "A": ("Заявка на английский без записи", "Сами интересовались английским, но до пробного не дошли. Звонок: «вы спрашивали про английский — у Ильи вт-чт 18:00 группа 4–5 лет, есть 2 места, первое занятие условно-бесплатное, запишу на вторник 08.09 или четверг 10.09?»"),
    "B": ("Наши клиенты 4–5 лет, ходят на другое", "Уже доверяют нам, знают адрес. Звонок: «Илья набирает английский для 4–5 лет вт-чт 18:00; второй предмет −10%, первое занятие условно-бесплатное. У вас как раз возраст, когда язык ложится на слух»."),
    "C": ("Ходили на английский в прошлом сезоне, не продолжили", "Уже занимались, знают формат. Звонок: «в этом году английский 4–5 лет ведёт Илья Ярославцев (учитель первой категории, Cambridge). Вернётесь? Первое занятие бесплатно для своих»."),
    "D": ("Лиды 4–5 лет из базы (заявки на другое / новые)", "Холоднее, звонить после сегментов A–C. Предлагать английский как второй вариант к тому, что спрашивали."),
}


def td(r):
    kids = html.escape(", ".join(r["kids"]))
    now = html.escape("; ".join(r["now"])) or "—"
    extra = []
    if r["last"]:
        extra.append("был: " + "; ".join(r["last"]))
    if r["zay"]:
        extra.append("заявка: " + "; ".join(r["zay"]))
    link = f"https://app.moyklass.com/user/{r['id']}"
    return (f"<tr><td><a href='{link}' target='_blank'>{kids}</a></td>"
            f"<td class='num' style='white-space:nowrap'><a href='tel:+{r['phone']}'>+{r['phone']}</a></td>"
            f"<td style='font-size:12.5px'>{now}</td>"
            f"<td style='font-size:12.5px;color:#6c6a86'>{html.escape(' · '.join(extra)) or '—'}</td>"
            f"<td style='font-size:12.5px'>{html.escape(r['state'])}<br><span style='color:#6c6a86'>{r['touched']}</span></td>"
            f"<td style='font-size:12.5px'><input type='checkbox'> </td></tr>")


parts = []
for s in "ABCD":
    rs = [r for r in merged if r["seg"] == s]
    if not rs:
        continue
    title, script = SEG[s]
    parts.append(f"<h2>{s}. {html.escape(title)} — {len(rs)}</h2><p style='font-size:14px'>{html.escape(script)}</p>"
                 f"<div class='scroll'><table><tr><th>Ребёнок (возраст)</th><th>Телефон</th><th>Сейчас ходит</th><th>История</th><th>Статус</th><th>✓</th></tr>"
                 + "".join(td(r) for r in rs) + "</table></div>")

total = len(merged)
counts = {s: len([r for r in merged if r["seg"] == s]) for s in "ABCD"}
page = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Обзвон: английский 4–5 лет, группа Ильи</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:#f6f6fb;color:#1c1b2e;font-size:15px;line-height:1.45}}
.wrap{{max-width:1180px;margin:0 auto;padding:22px 16px 60px}}
h1{{color:#312783;font-size:26px;margin:0 0 6px}} h2{{color:#312783;font-size:19px;margin:26px 0 6px}}
.card{{background:#fff;border-radius:12px;padding:14px 18px;margin:12px 0;box-shadow:0 1px 3px rgba(49,39,131,.08)}}
table{{border-collapse:collapse;width:100%;background:#fff;font-size:13.5px}} th,td{{padding:6px 8px;border-bottom:1px solid #e7e6f2;text-align:left;vertical-align:top}}
th{{background:#eef3ff;color:#312783;font-size:12px;text-transform:uppercase;letter-spacing:.03em}} .num{{font-variant-numeric:tabular-nums}}
.scroll{{overflow-x:auto}} a{{color:#1DA7E0}} .kpi{{display:flex;gap:14px;flex-wrap:wrap}} .kpi div{{background:#fff;border-radius:10px;padding:10px 14px;min-width:120px}}
.kpi b{{display:block;font-size:24px;color:#312783}} .warn{{border-left:4px solid #F59C00}} .ok{{border-left:4px solid #7DB928}}
</style></head><body><div class="wrap">
<h1>Обзвон: английский 4–5 лет, группа Ильи</h1>
<div style="color:#6c6a86;font-size:13px">Собрано 06.09.2026 15:50 по базе МойКласса. Возраст 3 г 8 мес – 6 лет на сегодня. Исключены: кто уже в английском этого сезона, статус «не писать», отказавшиеся именно от английского в этом сезоне. Одна строка на семью.</div>
<div class="kpi" style="margin:14px 0"><div><b>{total}</b>семей в списке</div><div><b>{counts['A']}</b>A · заявки на АЯ</div><div><b>{counts['B']}</b>B · наши клиенты</div><div><b>{counts['C']}</b>C · были на АЯ</div><div><b>{counts['D']}</b>D · лиды</div></div>
<div class="card ok"><b>Куда записываем.</b> Илья: <b>Гр7 вт-чт 18:00, 3–5 лет, Pre-A1 Starters</b> — живых записей 6 из 8, реально ходят меньше, поэтому звоним до заполнения списка, а не до «2 мест». Ближайшие первые занятия: вт 08.09 и чт 10.09 в 18:00.
Если время вт-чт 18:00 не подходит — <b>Гр2 пн-ср 17:00, 3–5 лет</b> (4 из 8, Мария). Набрали больше 8 на вт-чт — это повод открыть Илье вторую группу 4–5 (например вт-чт 17:00 после слияния), список ожидания вести здесь же: ставьте ✓ и пишите «лист» в комментарий карточки.</div>
<div class="card warn"><b>Порядок.</b> Сначала A (тёплые, сами спрашивали), потом B (свои клиенты — предлагаем −10% второй предмет), затем C, D — в свободное время. Каждый разговор — комментарий в карточку и статус (записался / думает / отказ с причиной). Первое занятие — «условно-бесплатное», не «бесплатное пробное». Скидка −15% на первый абонемент действует в день первого занятия. Звонить с 10:00 до 20:00; в выходной — не раньше 11:00.</div>
{''.join(parts)}
<p style="color:#6c6a86;font-size:12.5px;margin-top:24px">Страница статичная (срез на 06.09). Кто записался после звонка — исчезнет из списка при следующей пересборке; пока ставьте галочку.</p>
</div></body></html>"""
(ROOT / "docs/obzvon_aya_4_5.html").write_text(page, encoding="utf-8")
print("итого семей", total, counts)
