"""Личная страница администратора: только её собственный расчёт бонуса.

Решение от 11.09 (Ира, дословно): «Решать с каждым отдельно, чтобы мы не видели
сравнение друг с другом и результаты другого человека… Деньги всегда к конфликтам
приводят». Возражение принято: общая страница со сравнением убрана, вместо неё —
личная ссылка на человека. Ссылка со случайным токеном, потому что общий админский
пароль знают все, а зарплата — не общая информация.

Август считался вручную (docs/bonusy_avgust_razbor.html), сентябрь берётся из
последнего расчёта bonus_tarif — data/bonus_tarif.json.
"""

from __future__ import annotations

import html as _html
import json
import secrets
from pathlib import Path

from . import db

DATA = Path(__file__).resolve().parent.parent / "data"
TOKENS = DATA / "lk_tokens.json"
BONUS = DATA / "bonus_tarif.json"

# Август 17–31.08: ставки 300/800/500/400, гарантия 1 900 ₽ за смену.
AUG = {
    "Аня": {
        "shifts": 10,
        "days": "17, 19, 20, 22, 23, 24, 26, 28, 29, 30 августа",
        "rows": [
            ("Состоявшиеся пробные", 8, 300,
             "все 31.08 — мини-сад, нулевой класс и шесть по английскому"),
            ("Новый ученик купил абонемент", 1, 800, "Дубинина Варвара, 17.08"),
            ("Возврат «спящего»", 0, 500, ""),
            ("Второй предмет", 3, 400,
             "Гетман Василиса — английский (дважды), Параскив Мария — подготовка"),
        ],
    },
    "Ира": {
        "shifts": 9,
        "days": "18, 19, 20, 21, 24, 25, 27, 28, 31 августа",
        "rows": [
            ("Состоявшиеся пробные", 13, 300,
             "все 31.08 — раннее развитие, подготовка, мини-сад, английский"),
            ("Новые ученики купили абонемент", 5, 800,
             "Самчук Егор, Шведова Василиса, Бердюгин Фёдор, Романова Софья, "
             "Красильников Ярослав"),
            ("Возврат «спящих»", 2, 500, "Шальнев Дмитрий, Гетман Василиса"),
            ("Второй предмет", 1, 400, "Романова Софья — ИЗО"),
        ],
    },
}
RATE_SHIFT = 1900


def tokens() -> dict:
    """Токены личных страниц. Создаются один раз и живут в data/."""
    if TOKENS.exists():
        data = json.loads(TOKENS.read_text(encoding="utf-8"))
    else:
        data = {}
    changed = False
    for who in ("Аня", "Ира", "Лена", "Лиза"):
        if who not in data:
            data[who] = secrets.token_urlsafe(18)
            changed = True
    if changed:
        DATA.mkdir(parents=True, exist_ok=True)
        TOKENS.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


def whoami(token: str) -> str | None:
    for who, t in tokens().items():
        if secrets.compare_digest(t, token):
            return who
    return None


def _sept(who: str) -> dict | None:
    if not BONUS.exists():
        return None
    d = json.loads(BONUS.read_text(encoding="utf-8"))
    p = (d.get("people") or {}).get(who)
    if not p:
        return None
    p["period"] = d.get("period")
    return p


def _e(x) -> str:
    return _html.escape(str(x or ""))


def _money(n: int) -> str:
    return f"{n:,}".replace(",", " ") + " ₽"


def page(who: str) -> str:
    aug = AUG.get(who)
    sep = _sept(who)

    head = """<!doctype html><html lang=ru><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>Мой бонус — KidsUP</title>
<link rel=preconnect href="https://fonts.googleapis.com">
<link rel=preconnect href="https://fonts.gstatic.com" crossorigin>
<link rel=stylesheet href="https://fonts.googleapis.com/css2?family=Rubik:wght@500;600;700&family=Inter:wght@400;500;600&display=swap">
<style>
:root{--indigo:#312783;--blue:#1DA7E0;--green:#7DB928;--amber:#F59C00;
      --ink:#221F3B;--muted:#676C85;--line:#E4E8F3;--soft:#F5F8FD;--r:16px}
*{box-sizing:border-box;margin:0}
body{background:#FBFCFE;color:var(--ink);font:16px/1.65 Inter,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:720px;margin:0 auto;padding:24px 16px 64px}
.hero{background:var(--indigo);color:#fff;border-radius:var(--r);padding:24px;margin-bottom:20px}
.hero h1{font:700 25px/1.2 Rubik,sans-serif;margin-bottom:6px;text-wrap:balance}
.hero p{color:#CFD3EE;font-size:15px}
h2{font:700 19px/1.3 Rubik,sans-serif;color:var(--indigo);margin:28px 0 10px}
h3{font:600 16px/1.35 Rubik,sans-serif;margin:0 0 5px}
.card{background:#fff;border:1px solid var(--line);border-radius:var(--r);padding:18px 20px;margin:13px 0}
.card.soft{background:var(--soft);border-color:#DCE6F5}
.sum{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin:4px 0 2px}
.sum b{font:700 32px/1 Rubik,sans-serif;color:var(--indigo);font-variant-numeric:tabular-nums}
.sum span{color:var(--muted);font-size:14px}
.scroll{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:15px;min-width:380px}
th,td{padding:9px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{font-size:12.5px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
td.n,th.n{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
tr.tot td{font-weight:700;border-top:2px solid var(--indigo);border-bottom:none;padding-top:11px}
.small{font-size:13.5px;color:var(--muted)}
ul{margin:8px 0 0 19px}li{margin:6px 0}
.end{margin-top:28px;padding-top:16px;border-top:1px solid var(--line);color:var(--muted);font-size:13.5px}
</style></head><body><div class=wrap>"""

    out = [head]
    out.append(f"""<div class=hero><h1>{_e(who)}, ваш бонус</h1>
<p>Здесь только ваш расчёт: что засчиталось, по каким детям и как из этого
складывается сумма. Чужих цифр на этой странице нет — ссылка личная.</p></div>""")

    # ---------- август ----------
    if aug:
        sdel = sum(n * r for _, n, r, _ in aug["rows"])
        guar = aug["shifts"] * RATE_SHIFT
        pay = max(sdel, guar)
        out.append("<h2>Август, 17–31 числа</h2>")
        out.append('<div class=card>')
        out.append(f'<div class=sum><b>{_money(pay)}</b>'
                   f'<span>{aug["shifts"]} смен: {_e(aug["days"])}</span></div>')
        out.append('<div class=scroll><table>'
                   '<tr><th>За что</th><th class=n>Кол-во</th><th class=n>Ставка</th>'
                   '<th class=n>Сумма</th></tr>')
        for title, n, rate, note in aug["rows"]:
            note_html = f'<br><span class=small>{_e(note)}</span>' if note else ""
            out.append(f'<tr><td>{_e(title)}{note_html}</td><td class=n>{n}</td>'
                       f'<td class=n>{rate} ₽</td><td class=n>{_money(n * rate)}</td></tr>')
        out.append(f'<tr><td><b>Сдельная</b></td><td class=n></td><td class=n></td>'
                   f'<td class=n><b>{_money(sdel)}</b></td></tr>')
        out.append(f'<tr><td>Гарантия за смены</td><td class=n>{aug["shifts"]}</td>'
                   f'<td class=n>{RATE_SHIFT} ₽</td><td class=n>{_money(guar)}</td></tr>')
        out.append(f'<tr class=tot><td>К выплате</td><td class=n></td><td class=n></td>'
                   f'<td class=n>{_money(pay)}</td></tr>')
        out.append('</table></div></div>')
        out.append("""<div class="card soft"><h3>Почему сдельная не прибавилась к гарантии</h3>
<p>По тарифу платится то, что больше: сдельная или гарантия за смены. В августе
занятия начались только 31-го, поэтому состоявшихся пробных почти не было ни у кого
и сдельная у всех вышла ниже гарантии. Получилось, что в этом конкретном месяце
работа сверх смены не добавляла к сумме — и это претензия справедливая: правило
писалось для обычного месяца, а август обычным не был. Мы это увидели постфактум,
решение по доплате за август владелец принимает отдельно.</p>
<p>Важное про август: он не пропал. Дети, записанные в августе, пошли на занятия
в сентябре — их пробные и абонементы считаются уже в сентябрьском бонусе ниже.</p></div>""")

    # ---------- сентябрь ----------
    if sep:
        p0, p1 = (sep.get("period") or ["", ""])[:2]
        out.append(f"<h2>Сентябрь, {_e(p0[8:10])}–{_e(p1[8:10])} числа — идёт сейчас</h2>")
        out.append('<div class=card>')
        out.append(f'<div class=sum><b>{_money(sep["to_pay"])}</b>'
                   f'<span>на {_e(p1[8:10])}.{_e(p1[5:7])}, до конца месяца вырастет</span></div>')
        rows = [("Состоявшиеся пробные", sep["trials"], 300, "subject"),
                ("Новые ученики купили абонемент", sep["new"], 800, "name"),
                ("Возврат «спящих»", sep["back"], 500, "name"),
                ("Второй предмет", sep["second"], 400, "subject")]
        out.append('<div class=scroll><table>'
                   '<tr><th>За что</th><th class=n>Кол-во</th><th class=n>Ставка</th>'
                   '<th class=n>Сумма</th></tr>')
        for title, items, rate, kind in rows:
            n = len(items)
            if kind == "subject":
                note = ", ".join(f'{i.get("name")} ({i.get("subject")})' for i in items)
            else:
                note = ", ".join(str(i.get("name")) for i in items)
            note_html = f'<br><span class=small>{_e(note)}</span>' if note else ""
            out.append(f'<tr><td>{_e(title)}{note_html}</td><td class=n>{n}</td>'
                       f'<td class=n>{rate} ₽</td><td class=n>{_money(n * rate)}</td></tr>')
        out.append(f'<tr><td><b>Сдельная</b></td><td class=n></td><td class=n></td>'
                   f'<td class=n><b>{_money(sep["sdelnaya"])}</b></td></tr>')
        out.append(f'<tr><td>Гарантия за смены</td><td class=n>{sep["shifts_plan"]}</td>'
                   f'<td class=n>{RATE_SHIFT} ₽</td><td class=n>{_money(sep["guarantee"])}</td></tr>')
        out.append(f'<tr class=tot><td>Пока набежало</td><td class=n></td><td class=n></td>'
                   f'<td class=n>{_money(sep["to_pay"])}</td></tr>')
        out.append('</table></div>')
        if sep["sdelnaya"] > sep["guarantee"]:
            out.append('<p class=small style="margin-top:10px">Сдельная уже обогнала '
                       'гарантию — дальше каждая запись и каждый абонемент прибавляют '
                       'к сумме напрямую.</p>')
        else:
            need = (sep["guarantee"] - sep["sdelnaya"] + 799) // 800
            out.append(f'<p class=small style="margin-top:10px">До гарантии осталось '
                       f'{_money(sep["guarantee"] - sep["sdelnaya"])} — примерно '
                       f'{need} абонемент(а). Дальше всё идёт сверх.</p>')
        out.append('</div>')

    out.append("""<h2>Что мы поменяли</h2><div class=card><ul>
<li><b>Бонус за разговоры дольше минуты отменён.</b> Проверка показала, что журнал
телефонии показывает, за каким аппаратом сидел человек, а не с кем он говорил:
были смены с одиннадцатью записями и нулём разговоров на своём добавочном.</li>
<li><b>Входящие звонки настраиваем на всех, кто в смене.</b> Сейчас они приходят не
на все добавочные, и это перекашивает любые сравнения.</li>
<li><b>Каждый работает под своей учётной записью.</b> CRM подписывает запись и оплату
той учёткой, из которой их сделали, — если сели за чужой компьютер, работа уходит
другому, и задним числом это не восстановить.</li>
<li><b>Разбираемся, кому засчитывать абонемент.</b> Сейчас 800 ₽ получает тот, кто
провёл оплату, а не тот, кто привёл семью, — и это часто разные люди. Правило
поменяем так, чтобы деньги шли за работу.</li></ul></div>""")

    out.append("""<div class=end>Ссылка личная — здесь только ваши цифры.
Любую строку можно развернуть до дат и фамилий: спросите, покажем.
Если видите ошибку в расчёте — скажите, пересчитаем, спорное трактуем в вашу пользу.
</div></div></body></html>""")
    return "".join(out)
