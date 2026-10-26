"""График промоутеров: кто и когда готов выйти (просьба Бориса 26.10.2026).

Промоутеры пишут администраторам в ВК или Telegram, а админ отмечает здесь:
на какой день человек готов, подтверждён ли выход, вышел или не вышел.
Страница app.kidsup.ru/promo/grafik под логином администратора; данные —
таблицы promo_lyudi и promo_dni в базе сервера, в код имена не попадают.

Клетка дня хранит статус (готов / подтверждён / вышел / не вышел), время
(«16:00–19:00») и заметку. Клик по клетке переключает статус по кругу,
время и заметку правят через карандаш.
"""

from __future__ import annotations

import html as _html
import json
from datetime import date, datetime, timedelta, timezone

from . import db

MSK = timezone(timedelta(hours=3))
DNI = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
STATUSY = ("готов", "подтверждён", "вышел", "не вышел")
KANALY = ("ВК", "Telegram", "WhatsApp", "MAX", "телефон")
VREMYA_PO_UMOLCHANIYU = "16:00–19:00"


def _init(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS promo_lyudi (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, kanal TEXT, kontakt TEXT,
        phone TEXT, note TEXT, active INTEGER DEFAULT 1, ts TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS promo_dni (
        id INTEGER PRIMARY KEY AUTOINCREMENT, lid INTEGER, day TEXT, status TEXT,
        vremya TEXT, note TEXT, ts TEXT, UNIQUE(lid, day))""")


def _now() -> str:
    return datetime.now(MSK).isoformat(timespec="seconds")


def _today() -> date:
    return datetime.now(MSK).date()


def _chist(s, n: int = 200) -> str:
    return str(s or "").strip()[:n]


def save_chelovek(p: dict) -> dict:
    """Добавить или поправить промоутера: {id?, name, kanal, kontakt, phone, note, active}."""
    name = _chist(p.get("name"), 80)
    if not name:
        raise ValueError("имя обязательно")
    kanal = _chist(p.get("kanal"), 20) or "ВК"
    kontakt = _chist(p.get("kontakt"), 120)
    phone = "".join(ch for ch in str(p.get("phone") or "") if ch.isdigit())[:12]
    note = _chist(p.get("note"), 300)
    active = 0 if str(p.get("active", 1)) in ("0", "false", "False") else 1
    with db.get_conn() as conn:
        _init(conn)
        lid = int(p.get("id") or 0)
        if lid:
            conn.execute("UPDATE promo_lyudi SET name=?, kanal=?, kontakt=?, phone=?, note=?, active=?, ts=? WHERE id=?",
                         (name, kanal, kontakt, phone, note, active, _now(), lid))
        else:
            cur = conn.execute("INSERT INTO promo_lyudi (name, kanal, kontakt, phone, note, active, ts) VALUES (?,?,?,?,?,?,?)",
                               (name, kanal, kontakt, phone, note, active, _now()))
            lid = cur.lastrowid
    return {"ok": True, "id": lid}


def set_den(p: dict) -> dict:
    """Клетка дня: {lid, day, status, vremya?, note?}. Пустой статус стирает клетку."""
    lid = int(p.get("lid") or 0)
    day = _chist(p.get("day"), 10)
    try:
        date.fromisoformat(day)
    except ValueError:
        raise ValueError("day — дата ГГГГ-ММ-ДД")
    status = _chist(p.get("status"), 20)
    if status and status not in STATUSY:
        raise ValueError("status — один из: " + ", ".join(STATUSY))
    with db.get_conn() as conn:
        _init(conn)
        if not conn.execute("SELECT 1 FROM promo_lyudi WHERE id=?", (lid,)).fetchone():
            raise ValueError("нет такого промоутера")
        row = conn.execute("SELECT vremya, note FROM promo_dni WHERE lid=? AND day=?", (lid, day)).fetchone()
        if not status:
            conn.execute("DELETE FROM promo_dni WHERE lid=? AND day=?", (lid, day))
            return {"ok": True, "cleared": True}
        vremya = _chist(p.get("vremya"), 40) if "vremya" in p else (row[0] if row else "")
        note = _chist(p.get("note"), 300) if "note" in p else (row[1] if row else "")
        if not vremya:
            vremya = VREMYA_PO_UMOLCHANIYU
        conn.execute("""INSERT INTO promo_dni (lid, day, status, vremya, note, ts) VALUES (?,?,?,?,?,?)
                        ON CONFLICT(lid, day) DO UPDATE SET status=excluded.status, vremya=excluded.vremya,
                        note=excluded.note, ts=excluded.ts""", (lid, day, status, vremya, note, _now()))
    return {"ok": True}


def dannye(dney: int = 14, nazad: int = 2) -> dict:
    """Люди, клетки на окно дней (2 назад и 14 вперёд) и сводка на сегодня/завтра."""
    t = _today()
    d0, d1 = t - timedelta(days=nazad), t + timedelta(days=dney - 1)
    with db.get_conn() as conn:
        _init(conn)
        lyudi = [dict(zip(("id", "name", "kanal", "kontakt", "phone", "note", "active"), r)) for r in conn.execute(
            "SELECT id, name, kanal, kontakt, phone, note, active FROM promo_lyudi ORDER BY active DESC, name")]
        kletki = {}
        for lid, day, status, vremya, note in conn.execute(
                "SELECT lid, day, status, vremya, note FROM promo_dni WHERE day BETWEEN ? AND ?",
                (d0.isoformat(), d1.isoformat())):
            kletki[f"{lid}|{day}"] = {"status": status, "vremya": vremya or "", "note": note or ""}
        itogi = {}
        for status, n in conn.execute("SELECT status, COUNT(*) FROM promo_dni WHERE day < ? GROUP BY status",
                                      (t.isoformat(),)):
            itogi[status] = n
    dni = []
    d = d0
    while d <= d1:
        dni.append({"day": d.isoformat(), "dow": DNI[d.weekday()], "label": d.strftime("%d.%m"),
                    "segodnya": d == t, "proshlo": d < t, "vyhodnoy": d.weekday() >= 5})
        d += timedelta(days=1)
    imena = {x["id"]: x["name"] for x in lyudi}

    def spisok(day: date) -> list[dict]:
        out = []
        for key, k in kletki.items():
            lid, dd = key.split("|")
            if dd == day.isoformat() and k["status"] in ("готов", "подтверждён", "вышел"):
                out.append({"name": imena.get(int(lid), "?"), "status": k["status"], "vremya": k["vremya"]})
        return sorted(out, key=lambda x: x["vremya"])

    return {"segodnya": t.isoformat(), "dni": dni, "lyudi": lyudi, "kletki": kletki,
            "statusy": list(STATUSY), "kanaly": list(KANALY), "vremya_default": VREMYA_PO_UMOLCHANIYU,
            "svodka": {"segodnya": spisok(t), "zavtra": spisok(t + timedelta(days=1))}, "itogi": itogi}


HEAD = """<!doctype html><html lang=ru><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>График промоутеров — KidsUP</title>
<style>
:root{--indigo:#312783;--blue:#1DA7E0;--green:#7DB928;--amber:#F59C00;--red:#E30613;--ink:#221F3B;
      --muted:#6A6F87;--line:#E4E8F3;--soft:#F5F8FD}
*{box-sizing:border-box;margin:0}
body{background:#FBFCFE;color:var(--ink);font:15px/1.5 -apple-system,"Segoe UI",Roboto,Inter,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:16px 12px 60px}
header{display:flex;align-items:center;gap:12px;margin-bottom:12px;flex-wrap:wrap}
header img{height:34px}
header b{font-size:18px;color:var(--indigo)}
header a{color:var(--blue);text-decoration:none;font-size:14px}
h1{font-size:22px;color:var(--indigo);margin:6px 0 4px}
p.lead{color:var(--muted);margin-bottom:12px;font-size:14px}
.svodka{display:grid;gap:10px;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));margin-bottom:14px}
.card{background:#fff;border:1px solid var(--line);border-radius:14px;padding:12px 14px}
.card h3{font-size:14px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em;margin-bottom:6px}
.card .n{font-size:15px}
.grid{overflow-x:auto;background:#fff;border:1px solid var(--line);border-radius:14px}
table{border-collapse:collapse;min-width:900px;width:100%}
th,td{border-bottom:1px solid var(--line);padding:6px 6px;text-align:center;font-size:13px;vertical-align:top}
th{background:var(--soft);font-weight:600;color:var(--muted);position:sticky;top:0}
th.seg,td.seg{background:#FFF7E6}
th.vyh{color:var(--red)}
td.who,th.who{text-align:left;position:sticky;left:0;background:#fff;min-width:170px;z-index:1;border-right:1px solid var(--line)}
td.who b{display:block;font-size:14px}
td.who .k{color:var(--muted);font-size:12px}
td.who a{color:var(--blue);text-decoration:none}
.cell{display:block;width:100%;min-height:44px;border:1px dashed var(--line);border-radius:9px;background:#fff;cursor:pointer;
      font:12px/1.3 inherit;padding:4px 3px;color:var(--ink)}
.cell:hover{border-color:var(--blue)}
.cell.proshlo{opacity:.65}
.s-готов{background:#FFF1CC;border-style:solid;border-color:#F3D27A}
.s-подтверждён{background:#DCF1FB;border-style:solid;border-color:#9CD6F0}
.s-вышел{background:#E3F3CC;border-style:solid;border-color:#B6DD7A}
.s-не\\ вышел{background:#FBDADA;border-style:solid;border-color:#F0A0A0}
.cell .v{display:block;font-weight:600}
.cell .nt{display:block;color:var(--muted);font-size:11px;white-space:normal}
.pen{font-size:11px;color:var(--muted);cursor:pointer;margin-top:2px;display:block}
.pen:hover{color:var(--blue)}
.leg{display:flex;gap:10px;flex-wrap:wrap;margin:10px 0;font-size:13px}
.leg span{padding:3px 10px;border-radius:20px;border:1px solid var(--line)}
form.add{display:grid;gap:8px;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));align-items:end;margin-top:16px}
form.add label{font-size:12px;color:var(--muted);display:block}
form.add input,form.add select{width:100%;padding:8px 10px;border:1px solid var(--line);border-radius:9px;font:14px inherit}
button.b{padding:9px 14px;border:0;border-radius:9px;background:var(--indigo);color:#fff;font:600 14px inherit;cursor:pointer}
button.b:hover{background:#3b2f9b}
.muted{color:var(--muted);font-size:13px}
.off{opacity:.5}
.how{margin-top:16px;background:var(--soft);border:1px solid #DCE6F5;border-radius:14px;padding:12px 14px;font-size:13.5px;color:#444}
.how b{color:var(--indigo)}
@media (max-width:640px){h1{font-size:19px} td.who,th.who{min-width:130px}}
</style></head><body><div class=wrap>
<header><img src="/static/logo_color.png" alt="KidsUP"><b>KidsUP</b>
<a href="/promo">← Эффективность промоутеров</a> · <a href="/pult">Пульт</a></header>"""

FOOT = "</div></body></html>"


def page() -> str:
    d = dannye()
    data = json.dumps(d, ensure_ascii=False).replace("</", "<\\/")
    return f"""{HEAD}
<h1>График промоутеров</h1>
<p class=lead>Промоутеры пишут в ВК или Telegram — админ отмечает здесь, кто и когда готов выйти.
Клик по клетке переключает статус: готов → подтверждён → вышел → не вышел → пусто. Карандаш — время и заметка.</p>
<div class=svodka>
 <div class=card><h3>Сегодня</h3><div class=n id=sv-today></div></div>
 <div class=card><h3>Завтра</h3><div class=n id=sv-tomorrow></div></div>
 <div class=card><h3>Итоги прошлых дней</h3><div class=n id=sv-itogi></div></div>
</div>
<div class=leg><span class="s-готов">готов</span><span class="s-подтверждён">подтверждён</span>
<span class="s-вышел">вышел</span><span class="s-не вышел">не вышел</span></div>
<div class=grid><table id=t></table></div>
<form class=add id=f onsubmit="return addP(event)">
 <div><label>Имя промоутера</label><input name=name required placeholder="Как в переписке"></div>
 <div><label>Где пишет</label><select name=kanal><option>ВК</option><option>Telegram</option><option>WhatsApp</option><option>MAX</option><option>телефон</option></select></div>
 <div><label>Ссылка или @ник</label><input name=kontakt placeholder="vk.com/id… или @ник"></div>
 <div><label>Телефон</label><input name=phone placeholder="7…"></div>
 <div><label>Заметка</label><input name=note placeholder="район, опыт, оплата"></div>
 <div><button class=b type=submit>Добавить</button></div>
</form>
<div class=how><b>Как пользоваться.</b> Промоутер написал «могу в среду» — найдите его строку, нажмите клетку среды: станет «готов».
Договорились окончательно — ещё клик: «подтверждён». После смены отметьте «вышел» или «не вышел» — по этим отметкам
считаем надёжность и оплату. Время по умолчанию {VREMYA_PO_UMOLCHANIYU}, меняется карандашом. Человека, который больше не работает,
переключите в «не активен» (карандаш у имени) — строка уйдёт вниз.</div>
<script>
const D = {data};
const S = D.statusy;
function esc(s){{return String(s==null?'':s).replace(/[&<>"]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}})[c]);}}
async function post(url, body){{
  const r = await fetch(url, {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify(body)}});
  const j = await r.json().catch(()=>({{}}));
  if(!r.ok){{alert(j.detail || 'ошибка'); throw new Error('bad');}}
  return j;
}}
function kontaktHtml(p){{
  const k = p.kontakt || '';
  let href = '';
  if(/^https?:\\/\\//.test(k)) href = k;
  else if(/^@/.test(k)) href = (p.kanal==='Telegram' ? 'https://t.me/' : 'https://vk.com/') + k.slice(1);
  else if(/^vk\\.com|^t\\.me/.test(k)) href = 'https://' + k;
  const kk = href ? `<a href="${{esc(href)}}" target=_blank rel=noopener>${{esc(k)}}</a>` : esc(k);
  return `<span class=k>${{esc(p.kanal)}} ${{kk}}${{p.phone?' · '+esc(p.phone):''}}</span>`;
}}
function render(){{
  const t = document.getElementById('t');
  let h = '<tr><th class=who>Промоутер</th>' + D.dni.map(d=>`<th class="${{d.segodnya?'seg':''}} ${{d.vyhodnoy?'vyh':''}}">${{d.dow}}<br>${{d.label}}</th>`).join('') + '</tr>';
  for(const p of D.lyudi){{
    h += `<tr class="${{p.active?'':'off'}}"><td class=who><b>${{esc(p.name)}}</b>${{kontaktHtml(p)}}${{p.note?'<div class=k>'+esc(p.note)+'</div>':''}}<span class=pen onclick="editP(${{p.id}})">✎ править</span></td>`;
    for(const d of D.dni){{
      const k = D.kletki[p.id+'|'+d.day] || {{status:'',vremya:'',note:''}};
      const cls = 'cell' + (k.status?' s-'+k.status:'') + (d.proshlo?' proshlo':'');
      h += `<td class="${{d.segodnya?'seg':''}}"><button type=button class="${{cls}}" onclick="cycle(${{p.id}},'${{d.day}}')">` +
           (k.status ? `${{esc(k.status)}}<span class=v>${{esc(k.vremya)}}</span>` : '·') +
           (k.note ? `<span class=nt>${{esc(k.note)}}</span>` : '') + `</button>` +
           (k.status ? `<span class=pen onclick="editCell(${{p.id}},'${{d.day}}')">✎</span>` : '') + `</td>`;
    }}
    h += '</tr>';
  }}
  if(!D.lyudi.length) h += `<tr><td colspan="${{D.dni.length+1}}" class=muted style="text-align:left;padding:14px">Промоутеров пока нет — добавьте первого в форме ниже.</td></tr>`;
  t.innerHTML = h;
  const sv = (arr)=> arr.length ? arr.map(x=>`<div>${{esc(x.name)}} — <b>${{esc(x.vremya)}}</b> <span class=muted>(${{esc(x.status)}})</span></div>`).join('') : '<span class=muted>никто не отмечен</span>';
  document.getElementById('sv-today').innerHTML = sv(D.svodka.segodnya);
  document.getElementById('sv-tomorrow').innerHTML = sv(D.svodka.zavtra);
  const it = D.itogi || {{}};
  document.getElementById('sv-itogi').innerHTML = `вышли: <b>${{it['вышел']||0}}</b> · не вышли: <b>${{it['не вышел']||0}}</b> · подтверждали: <b>${{it['подтверждён']||0}}</b>`;
}}
async function reload(){{ const r = await fetch('/api/promo/grafik'); Object.assign(D, await r.json()); render(); }}
async function cycle(lid, day){{
  const k = D.kletki[lid+'|'+day] || {{status:''}};
  const i = S.indexOf(k.status);
  const next = (i < 0) ? S[0] : (i === S.length-1 ? '' : S[i+1]);
  await post('/api/promo/grafik/den', {{lid, day, status: next}});
  await reload();
}}
async function editCell(lid, day){{
  const k = D.kletki[lid+'|'+day] || {{status:'готов',vremya:'',note:''}};
  const vremya = prompt('Время выхода (например 16:00–19:00):', k.vremya || D.vremya_default);
  if(vremya === null) return;
  const note = prompt('Заметка (точка, листовки, оплата):', k.note || '');
  if(note === null) return;
  await post('/api/promo/grafik/den', {{lid, day, status: k.status || 'готов', vremya, note}});
  await reload();
}}
async function editP(id){{
  const p = D.lyudi.find(x=>x.id===id); if(!p) return;
  const name = prompt('Имя:', p.name); if(name === null) return;
  const kanal = prompt('Где пишет (ВК / Telegram / WhatsApp / MAX / телефон):', p.kanal); if(kanal === null) return;
  const kontakt = prompt('Ссылка или @ник:', p.kontakt || ''); if(kontakt === null) return;
  const phone = prompt('Телефон:', p.phone || ''); if(phone === null) return;
  const note = prompt('Заметка:', p.note || ''); if(note === null) return;
  const active = confirm('Человек активен? ОК — да, Отмена — больше не работает') ? 1 : 0;
  await post('/api/promo/grafik/chelovek', {{id, name, kanal, kontakt, phone, note, active}});
  await reload();
}}
async function addP(e){{
  e.preventDefault();
  const f = e.target, b = {{}};
  for(const el of f.elements) if(el.name) b[el.name] = el.value;
  await post('/api/promo/grafik/chelovek', b);
  f.reset(); await reload(); return false;
}}
render();
</script>
{FOOT}"""
