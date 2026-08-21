"""Страница обзвона по ПШ для владельца: скрипт + лист на два дня.

Скрипт написан после разбора двух критиков — «мамы шестилетки» и практика
продаж. Оба забраковали первую версию, и обе претензии стоит помнить:

  · Первая версия четыре раза подряд объясняла, что педагог — сменная
    деталь: «чтение не держится на личном таланте», «педагог не изобретает
    свой путь», «гарантию даёт центр, а не фамилия в расписании». Мама
    услышала «нам всё равно, кто ведёт», хотя каждая фраза по отдельности
    верна. Вывод: про методику говорим ОДИН раз и только после того, как
    сказали что-то про её ребёнка.
  · «Проверено на тысячах детей» — «мне не нужны тысячи, мой один не читает».
  · «Ребёнок ничего не теряет» — теряет, у него школа через десять дней.
  · «Имя педагога назову, когда закрепим» — звучит как «педагога у нас нет».
    Поэтому имена названы прямо: Татьяна и Елена.
  · «Расписание то же» — обещание, которое вскроется, если группы сократят.
    Говорим «время подберём», а не «всё как было».

Запуск:
    python -m app.pshpage      — собрать docs/obzvon_psh.html
"""

from __future__ import annotations

import html
import logging
from datetime import date

from .pshlist import ranked

log = logging.getLogger("kidsup.pshpage")
OUT = "docs/obzvon_psh.html"

TEACHERS = "Татьяна и Елена"


def _short(name: str) -> str:
    parts = [w for w in (name or "").split("(")[0].split() if w]
    if len(parts) >= 2 and parts[1][:1].isupper():
        return parts[1]
    return parts[0] if parts else "ребёнок"


def _when(last: str) -> str:
    if not last:
        return "визитов не отмечено"
    M = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
         "августа", "сентября", "октября", "ноября", "декабря"]
    try:
        d = date.fromisoformat(last)
    except ValueError:
        return last
    y = "" if d.year == 2026 else f" {d.year}"
    return f"был у нас {d.day} {M[d.month - 1]}{y}"


def _hint(c: dict) -> str:
    """Первая фраза — про её ребёнка, а не про нас."""
    nm = _short(c.get("name") or "")
    was = ", ".join(c.get("was") or []) or "подготовку к школе"
    age = c.get("age")
    a = f"{age:g}".replace(".", ",") if age else "?"
    return (f"«Помню {nm}, {'он' if not _fem(nm) else 'она'} ходил"
            f"{'а' if _fem(nm) else ''} к нам на {was}» — {_when(c.get('last') or '')}. "
            f"Сейчас {a} — как раз наш возраст для ПШ.")


_FEM_EXC = {"никита", "илья", "данила", "савва", "лука", "миша", "паша",
            "саша", "женя", "слава", "лёва", "сеня"}


def _fem(name: str) -> bool:
    n = (name or "").strip().lower()
    return bool(n) and n not in _FEM_EXC and n.endswith(("а", "я"))


def _row(c: dict, i: int) -> str:
    nm = html.escape(c.get("name") or "?")
    ph = c.get("phone") or ""
    age = c.get("age")
    a = f"{age:g}".replace(".", ",") if age else "?"
    was = html.escape(", ".join(c.get("was") or []) or "—")
    busy = ('<span class="tag busy">уже есть задача у админа</span>'
            if c.get("busy") else "")
    return f"""<div class="card" data-uid="{c['uid']}">
  <div class="top"><b>{i}. {nm}</b><span class="age">{a} лет</span></div>
  <div class="meta">{was} · {html.escape(_when(c.get('last') or ''))}</div>
  {busy}
  <a class="tel" href="tel:+7{ph}">+7 {ph}</a>
  <div class="say">{html.escape(_hint(c))}</div>
  <div class="marks">
    <button data-m="записал">записал</button>
    <button data-m="подумает">подумает</button>
    <button data-m="не берёт">не берёт</button>
    <button data-m="отказ">отказ</button>
  </div>
</div>"""


SCRIPT = """
<section>
<div class="seclabel">Скрипт</div>
<h2>Что говорить</h2>

<p class="sub">Правило одно: сказали — замолчали. Пауза после вопроса работает
лучше любого текста.</p>

<h3>1. Начните с её ребёнка, а не с нас</h3>
<div class="say big">Здравствуйте, [имя]! Это Борис, владелец детского центра
KidsUP на Рокоссовского. Звоню лично.<br>
Помню [имя ребёнка], [он ходил] к нам на [предмет], [был у нас в мае].
Сейчас [ему 6,2] — это как раз наш возраст для подготовки к школе,
и я не хочу, чтобы мы вас потеряли.</div>
<p>Дальше — <b>вопрос и молчание</b>: «Скажите, как у вас сейчас с чтением?
В школу ведь в этом году?»</p>

<h3>2. Если спросят про Екатерину или Ингу — отвечайте сразу и прямо</h3>
<div class="say big">Да, правда. Екатерина и Инга у нас больше не работают,
причины у каждой свои, личные. Понимаю, что вы шли к конкретному человеку —
это нормально, к хорошему педагогу и привязываются.<br>
Подготовку к школе в этом году ведут <b>Татьяна и Елена</b>. Познакомиться
с ними можно 30 августа на Дне открытых дверей или на первом занятии —
оно условно-бесплатное, не понравится, платить не нужно.</div>
<p class="warn-inline">Один раз — и всё. Не объясняйте по три круга, что
методика важнее педагога: мама услышит «нам всё равно, кто ведёт».</p>

<h3>3. Про методику — коротко и один раз</h3>
<div class="say big">У нас чтение идёт по технологии Бураковой: пошагово,
в определённом порядке, с понятными точками, где ребёнок обычно спотыкается.
Поэтому мы и можем дать гарантию: <b>ребёнок с нуля читает трёхбуквенные
слова за три месяца, иначе занимается бесплатно, пока не зачитает.</b>
Условия честные: диагностика на первом занятии, посещаемость от 80%
и домашние задания.</div>

<h3>4. Следующий шаг — выбор из двух</h3>
<div class="say big">Давайте так: либо 30 августа в воскресенье приходите
на День открытых дверей — посмотрите педагога, зададите вопросы. Либо сразу
записываю на первое занятие: педагог сделает диагностику и скажет, что
у ребёнка уже хорошо, что подтянуть и как занятия с этим помогут.<br>
Как вам удобнее — в воскресенье или сразу на занятие?</div>

<h3>Чего не говорить</h3>
<ul class="plain">
  <li><b>«Расписание то же».</b> Расписание может измениться — обещайте
    «подберём удобное время», а не «всё как было». Иначе человек приедет
    и не найдёт своё время, поверх уже случившегося ухода педагогов.</li>
  <li><b>«Проверено на тысячах детей».</b> Родителю не нужны тысячи —
    у него не читает один конкретный ребёнок.</li>
  <li><b>«Ребёнок ничего не теряет».</b> Теряет: школа через десять дней.
    Честнее — «успеваем, если начнём сейчас».</li>
  <li><b>«Места разбирают».</b> В группах ноль записей. Не врать.</li>
  <li><b>«Обучение по методике простое».</b> Это довод для нас, не для
    родителя: звучит как «вести может кто угодно».</li>
</ul>

<h3>Если просят вернуть деньги или злятся</h3>
<p>Не спорить и не задавать риторических вопросов. «Понимаю. Давайте так:
приходите 30-го, посмотрите на педагога сами. Не понравится — вопрос
закрыт, я лично прослежу.» Спокойно и без «никто вас не держит».</p>
</section>
"""


def build() -> str:
    data = ranked()
    d1 = [c for c in data if c["seg"] == "ПШ сейчас (5,5-7)"][:60]
    rest = [c for c in data if c["seg"] == "ПШ сейчас (5,5-7)"][60:]
    d2 = rest + [c for c in data if c["seg"] == "младшая ПШ1 (4-5,5)"]
    later = [c for c in data if c["seg"] == "возраст неизвестен"]

    rows1 = "\n".join(_row(c, i) for i, c in enumerate(d1, 1))
    rows2 = "\n".join(_row(c, i) for i, c in enumerate(d2, 1))
    rows3 = "\n".join(_row(c, i) for i, c in enumerate(later, 1))

    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Обзвон ПШ · лист владельца</title>
<style>
:root{{--paper:#FCFBF7;--ink:#1B1D2B;--muted:#5F6478;--line:#E7E4DA;--card:#FFF;
  --indigo:#312783;--sky:#1DA7E0;--green:#5C8C1E;--amber:#B26F00;--red:#B4131C;
  --sky-soft:#E4F2FB;--green-soft:#EDF4E1;--amber-soft:#FBF0DC;--fill:#F3F1E9}}
@media (prefers-color-scheme:dark){{:root{{--paper:#14151D;--ink:#E7E6EC;--muted:#9A9EAE;
  --line:#292B37;--card:#1B1D26;--indigo:#A79EEE;--sky:#5EC0EC;--green:#9FD055;
  --amber:#E5A63F;--red:#EC7C7C;--sky-soft:#13252F;--green-soft:#1C2416;
  --amber-soft:#2C2313;--fill:#20222C}}}}
*{{box-sizing:border-box}}
body{{background:var(--paper);color:var(--ink);margin:0;
  font:16px/1.6 -apple-system,"Segoe UI",Roboto,Arial,sans-serif}}
.wrap{{max-width:52rem;margin:0 auto;padding:1.6rem 1rem 4rem}}
h1{{font-size:1.8rem;font-weight:800;letter-spacing:-.02em;margin:.3rem 0 .4rem}}
h2{{font-size:1.3rem;font-weight:760;margin:0 0 .3rem}}
h3{{font-size:1rem;font-weight:730;margin:1.3rem 0 .4rem}}
p{{margin:.5rem 0}} .sub{{color:var(--muted)}}
.kicker{{font-size:.72rem;font-weight:750;letter-spacing:.1em;text-transform:uppercase;
  color:var(--indigo)}}
section{{margin-top:2rem;padding-top:1.4rem;border-top:1px solid var(--line)}}
.seclabel{{font-size:.7rem;font-weight:750;letter-spacing:.09em;text-transform:uppercase;
  color:var(--muted);margin-bottom:.35rem}}
.nums{{display:grid;gap:.6rem;grid-template-columns:repeat(auto-fit,minmax(9rem,1fr));margin:1rem 0}}
.num{{background:var(--card);border:1px solid var(--line);border-radius:11px;padding:.75rem .9rem}}
.num b{{display:block;font-size:1.6rem;font-weight:800;color:var(--indigo);
  font-variant-numeric:tabular-nums;line-height:1.05}}
.num span{{display:block;font-size:.8rem;color:var(--muted);margin-top:.15rem}}
.say{{background:var(--sky-soft);border-left:3px solid var(--sky);border-radius:0 8px 8px 0;
  padding:.6rem .85rem;margin:.5rem 0;font-size:.93rem}}
.say.big{{font-size:1rem;line-height:1.55}}
.warn-inline{{color:var(--red);font-size:.9rem}}
ul.plain{{padding-left:1.2rem}} ul.plain li{{margin:.35rem 0}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;
  padding:.8rem .9rem;margin:.6rem 0}}
.card.done{{opacity:.45}}
.top{{display:flex;justify-content:space-between;gap:.6rem;align-items:baseline}}
.top b{{font-weight:730;font-size:1.02rem}}
.age{{color:var(--muted);font-size:.85rem;white-space:nowrap;font-variant-numeric:tabular-nums}}
.meta{{color:var(--muted);font-size:.85rem;margin-top:.15rem}}
.tag{{display:inline-block;font-size:.72rem;font-weight:700;border-radius:5px;
  padding:.1rem .4rem;margin-top:.3rem}}
.tag.busy{{background:var(--amber-soft);color:var(--amber)}}
.tel{{display:inline-block;margin:.5rem 0 .2rem;font-size:1.15rem;font-weight:780;
  color:var(--green);text-decoration:none;font-variant-numeric:tabular-nums}}
.marks{{display:flex;flex-wrap:wrap;gap:.35rem;margin-top:.45rem}}
.marks button{{font:inherit;font-size:.8rem;padding:.28rem .6rem;border-radius:99px;
  border:1px solid var(--line);background:var(--fill);color:var(--ink);cursor:pointer}}
.marks button.on{{background:var(--green);border-color:var(--green);color:#fff}}
.foot{{margin-top:2.5rem;padding-top:1.1rem;border-top:1px solid var(--line);
  color:var(--muted);font-size:.85rem}}
</style></head>
<body><div class="wrap">

<div class="kicker">Только для владельца · 22–23 августа</div>
<h1>Обзвон по подготовке к школе</h1>
<p class="sub">Семьи, которые уже занимались у нас в ПШ или нулевом классе
и на новый сезон никуда не записаны. Идите сверху вниз — список отсортирован
по свежести: наверху те, кто был у нас недавно.</p>

<div class="nums">
  <div class="num"><b>{len(d1)}</b><span>суббота 22.08<br>дети 5,5–7 лет</span></div>
  <div class="num"><b>{len(d2)}</b><span>воскресенье 23.08<br>остальные + 4–5,5</span></div>
  <div class="num"><b>0</b><span>записей сейчас<br>в 14 группах ПШ</span></div>
  <div class="num"><b>112</b><span>свободных мест<br>в новом сезоне</span></div>
</div>

{SCRIPT}

<section>
<div class="seclabel">Суббота 22 августа</div>
<h2>Дети 5,5–7 лет — прямое попадание</h2>
<p class="sub">Этим ПШ нужен прямо сейчас: в школу через год или в этом году.
Начните с первых пятнадцати — они были у нас этой весной и летом.</p>
{rows1}
</section>

<section>
<div class="seclabel">Воскресенье 23 августа</div>
<h2>Остальные целевые и младшая группа 4–5,5</h2>
{rows2}
</section>

<section>
<div class="seclabel">Резерв</div>
<h2>Возраст не заполнен — уточнить в разговоре</h2>
<p class="sub">В карточке нет даты рождения. Спросите возраст первым делом:
от него зависит всё предложение.</p>
{rows3}
</section>

<div class="foot">🤖 Клод, ИИ-сотрудник KidsUP · собрано {date.today().strftime('%d.%m.%Y')}.
Отметки сохраняются в этом браузере. Педагоги ПШ нового сезона — {TEACHERS}.</div>

</div>
<script>
(function(){{
  var KEY='psh_marks_v1', st={{}};
  try{{ st=JSON.parse(localStorage.getItem(KEY)||'{{}}'); }}catch(e){{ st={{}}; }}
  function paint(card,uid){{
    var m=st[uid];
    card.classList.toggle('done', !!m);
    card.querySelectorAll('.marks button').forEach(function(b){{
      b.classList.toggle('on', b.dataset.m===m);
    }});
  }}
  document.querySelectorAll('.card').forEach(function(card){{
    var uid=card.dataset.uid;
    paint(card,uid);
    card.querySelectorAll('.marks button').forEach(function(b){{
      b.addEventListener('click',function(){{
        st[uid] = (st[uid]===b.dataset.m) ? null : b.dataset.m;
        if(!st[uid]) delete st[uid];
        try{{ localStorage.setItem(KEY, JSON.stringify(st)); }}catch(e){{}}
        paint(card,uid);
      }});
    }});
  }});
}})();
</script>
</body></html>"""


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    html_text = build()
    open(OUT, "w").write(html_text)
    print(f"{OUT}: {len(html_text)} байт")


if __name__ == "__main__":
    main()
