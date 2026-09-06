# -*- coding: utf-8 -*-
"""«Карта развития» — одностраничный документ после первого занятия.

Решение владельца 06.09: обещание «карта развития по каждому ребёнку» должно
стать реальным документом, иначе оно из той же серии, что «педагог заболел».
Педагог даёт три строки (что умеет, куда идём, с чего начнём), администратор
за минуту собирает карту на /karta и отправляет маме в WhatsApp вместе со
ссылкой на оплату — в тот же час, пока впечатление от занятия живое.

Картинка собирается в браузере администратора (SVG → canvas → PNG): на сервере
нет графических библиотек, а браузер и так есть. Сервер только сохраняет PNG
в /static/karty и отдаёт его Wazzup ссылкой (contentUri)."""
from __future__ import annotations
import base64, json, html as H, re, time, datetime as dt, pathlib, logging
from . import db

log = logging.getLogger("kidsup.karta")
BASE = pathlib.Path(__file__).resolve().parent
KARTY = BASE / "static" / "karty"
PUBLIC = "https://app.kidsup.ru"

COURSE_HINT = {
    "Подготовка к школе": "ПШ1 — нечитающие, начинаем с букв и слогов; ПШ2 — читающие, скорость, пересказ, письмо.",
    "Английский язык": "Уровни Cambridge: Pre-A1 Starters, A1 Movers, A2 Flyers. Дети говорят с первого занятия.",
    "Логопед": "Индивидуальный маршрут: постановка звуков, запуск речи, домашние приёмы.",
    "Раннее развитие": "«Музыка и речь» 1–3 года с мамой, «Первая школа» 1,3–3 года, «Лицей для малышей» 3–4 года.",
    "Ментальная арифметика": "Счёт на абакусе, затем в уме; память, концентрация, скорость мышления.",
    "Шахматы": "Начинающие — через игру, продолжающие — разбор партий и турниры.",
    "ИЗО-студия": "Живопись, лепка, «Шедевры великих художников» — ставим руку к письму.",
    "Робототехника": "Собирает и программирует сам: цикл «попробовал — сломалось — понял — починил».",
}


def _b64(p: pathlib.Path, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode()


def _prices(prices: dict) -> dict:
    out = {}
    for course, pr in (prices or {}).items():
        lines = pr.get("lines") or []
        if not lines:
            continue
        name, _old, new = lines[0]
        out[course] = f"{new:,} ₽ · {name}".replace(",", " ")
    return out


def _kids(days: int = 2) -> list[dict]:
    """Дети на первых занятиях за сегодня и вчера: имя, телефон, группа, время."""
    try:
        from .moyklass_client import MoyklassClient
        from . import sync
        mk = MoyklassClient(sync.get_api_key())
    except Exception as e:
        log.warning("karta: CRM недоступна: %s", e)
        return []
    out = []
    try:
        today = dt.date.today()
        d0 = (today - dt.timedelta(days=days - 1)).isoformat()
        r = mk.get("/v1/company/lessons", {"date": [d0, today.isoformat()], "includeRecords": "true", "limit": 500})
        lessons = (r.get("lessons") if isinstance(r, dict) else r) or []
        rc = mk.get("/v1/company/classes", {"limit": 500})
        classes = {c["id"]: c for c in ((rc.get("classes") if isinstance(rc, dict) else rc) or [])}
        rco = mk.get("/v1/company/courses")
        courses = {c["id"]: c.get("name") for c in (rco if isinstance(rco, list) else rco.get("courses", []))}
        seen = set()
        for l in sorted(lessons, key=lambda x: (x.get("date"), x.get("beginTime") or "")):
            for rec in l.get("records") or []:
                if not rec.get("test") or rec["userId"] in seen:
                    continue
                seen.add(rec["userId"])
                try:
                    u = mk.get(f"/v1/company/users/{rec['userId']}")
                except Exception:
                    continue
                cls = classes.get(l.get("classId")) or {}
                out.append({"uid": rec["userId"], "name": u.get("name") or "", "phone": u.get("phone") or "",
                            "group": (cls.get("name") or "").replace("2627_", ""),
                            "course": courses.get(cls.get("courseId")) or "", "date": l.get("date"),
                            "time": (l.get("beginTime") or "")[:5], "visit": bool(rec.get("visit"))})
        try:
            ms = mk.get("/v1/company/managers", {"limit": 200})
            ms = ms if isinstance(ms, list) else ms.get("managers", [])
            teachers = sorted(m["name"] for m in ms if 63600 in (m.get("roles") or []) and m.get("isWork", True))
        except Exception:
            teachers = []
    finally:
        mk.close()
    return {"kids": out, "teachers": teachers}


def page(prices: dict) -> str:
    data = _kids()
    kids = data["kids"] if isinstance(data, dict) else []
    teachers = data.get("teachers", []) if isinstance(data, dict) else []
    fonts = {"bold": _b64(BASE / "static" / "fonts" / "Montserrat-Bold.ttf", "font/ttf"),
             "medium": _b64(BASE / "static" / "fonts" / "Montserrat-Medium.ttf", "font/ttf")}
    logo = _b64(BASE / "static" / "logo_white.png", "image/png")
    cfg = {"kids": kids, "teachers": teachers, "prices": _prices(prices), "hints": COURSE_HINT,
           "courses": list(_prices(prices).keys()), "fonts": fonts, "logo": logo,
           "dry": db.get_setting("wazzup_dry_run", "1") == "1"}
    return TEMPLATE.replace("__CFG__", json.dumps(cfg, ensure_ascii=False).replace("</", "<\\/"))


def send(payload: dict) -> dict:
    """Сохранить PNG, отправить маме картинку и текст, записать комментарий в CRM."""
    from . import wazzup
    phone = "".join(ch for ch in str(payload.get("phone") or "") if ch.isdigit())
    if len(phone) == 10:
        phone = "7" + phone
    if len(phone) != 11:
        return {"ok": False, "error": "телефон должен быть из 11 цифр"}
    png = payload.get("png") or ""
    if "," in png:
        png = png.split(",", 1)[1]
    try:
        raw = base64.b64decode(png)
    except Exception:
        return {"ok": False, "error": "картинка не декодируется"}
    if len(raw) < 10_000:
        return {"ok": False, "error": "картинка пустая — проверьте предпросмотр"}
    KARTY.mkdir(parents=True, exist_ok=True)
    child = re.sub(r"[^\wА-Яа-яЁё-]+", "_", str(payload.get("child") or "karta"))[:40]
    fn = f"{dt.date.today().isoformat()}_{phone[-4:]}_{child}_{int(time.time())}.png"
    (KARTY / fn).write_bytes(raw)
    url = f"{PUBLIC}/static/karty/{fn}"
    dry = db.get_setting("wazzup_dry_run", "1") == "1"
    text = str(payload.get("text") or "").strip()
    log_lines = []
    try:
        log_lines += wazzup.send_media(phone, url, dry_run=dry, kind="karta")
        if text:
            log_lines += wazzup.send(phone, text, mode="cascade", dry_run=dry, kind="karta")
    except Exception as e:
        log_lines.append(f"ошибка отправки: {e}")
    sent = any("HTTP 20" in l or "[dry-run]" in l for l in log_lines)
    uid = payload.get("uid")
    if uid:
        try:
            from .moyklass_client import MoyklassClient
            from . import sync
            mk = MoyklassClient(sync.get_api_key())
            try:
                mk.post("/v1/company/userComments", {
                    "userId": int(uid), "showToUser": False,
                    "comment": (f"Карта развития после первого занятия ({payload.get('course') or ''}, педагог "
                                f"{payload.get('teacher') or '—'}) отправлена маме в WhatsApp {dt.datetime.now().strftime('%d.%m %H:%M')}. "
                                f"Умеет: {payload.get('can') or ''} | Куда: {payload.get('next_group') or ''} | Начнём: {payload.get('start') or ''} | {url}")[:1000]})
            finally:
                mk.close()
        except Exception as e:
            log_lines.append(f"комментарий в CRM не записан: {e}")
    return {"ok": sent, "url": url, "log": log_lines, "dry_run": dry}


TEMPLATE = r"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Карта развития</title>
<style>
:root{--ink:#15132e;--muted:#6c6a86;--line:#e4e2f0;--bg:#f8f7fc;--card:#fff;--indigo:#312783;--blue:#1DA7E0;--green:#7DB928;--amber:#F59C00;--red:#E30613}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,"Segoe UI",Roboto,Arial,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:18px 16px 60px}
h1{font-size:24px;margin:0 0 4px;color:var(--indigo)} .sub{color:var(--muted);margin:0 0 14px}
.grid{display:grid;grid-template-columns:minmax(320px,460px) 1fr;gap:18px} @media(max-width:900px){.grid{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px}
label{display:block;font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin:10px 0 4px}
input,select,textarea{width:100%;font:inherit;padding:8px 10px;border:1px solid var(--line);border-radius:9px;background:#fff;color:var(--ink)}
textarea{min-height:64px;resize:vertical}
.btn{display:inline-block;border:0;border-radius:10px;padding:11px 18px;font:inherit;font-weight:700;cursor:pointer;margin:12px 8px 0 0}
.b-green{background:var(--green);color:#fff}.b-gray{background:#eeedf5;color:var(--ink)}
.small{font-size:13px;color:var(--muted)} .ok{color:#4d7511}.err{color:var(--red)}
#preview svg{width:100%;height:auto;border-radius:12px;box-shadow:0 10px 30px rgba(49,39,131,.15)}
.kidrow{padding:8px 10px;border:1px solid var(--line);border-radius:9px;margin:6px 0;cursor:pointer;display:flex;justify-content:space-between;gap:8px}
.kidrow:hover{border-color:var(--blue)} .kidrow b{font-weight:700} .kidrow .t{color:var(--muted);white-space:nowrap}
</style></head><body><div class="wrap">
<h1>Карта развития — после первого занятия</h1>
<p class="sub">Педагог даёт три строки, администратор собирает карту и отправляет маме в WhatsApp вместе со ссылкой на оплату. В течение часа после занятия, пока впечатление живое.</p>
<div class="grid">
<div>
<div class="card">
  <b>Кто был на первом занятии сегодня и вчера</b>
  <div id="kids"></div>
  <label>Ребёнок (как обращаемся)</label><input id="child" placeholder="Алиса">
  <label>Телефон мамы</label><input id="phone" placeholder="79161234567">
  <label>Направление</label><select id="course"></select>
  <label>Группа (как в расписании)</label><input id="group" placeholder="ПШ · пн-чт 18:00 · ПШ2 читающие">
  <label>Педагог</label><select id="teacher"></select>
  <label>Дата первого занятия</label><input id="date" type="date">
  <label>Что уже умеет (глазами педагога)</label><textarea id="can" placeholder="Знает все буквы, читает слоги, уверенно считает до 20, хорошо держит карандаш."></textarea>
  <label>Куда идём — рекомендация группы</label><textarea id="next_group" placeholder="ПШ2 читающие, пн-чт 18:00: работаем над скоростью чтения и пересказом."></textarea>
  <label>С чего начнём</label><textarea id="start" placeholder="Первые две недели — слоговые таблицы и печатные буквы, дома по 10 минут в день."></textarea>
  <label>Следующее занятие</label><input id="next" placeholder="понедельник 8 сентября, 18:00">
  <label>Абонемент</label><input id="price">
  <label>Ссылка на оплату (из МойКласса)</label><input id="pay" placeholder="https://pay.tvoyklass.com/key/...">
  <label>Текст сообщения маме</label><textarea id="text" style="min-height:150px"></textarea>
  <button class="btn b-green" id="send">Отправить маме в WhatsApp</button>
  <button class="btn b-gray" id="dl">Скачать PNG</button>
  <div id="status" class="small" style="margin-top:10px"></div>
</div>
</div>
<div><div class="card" id="preview"></div></div>
</div></div>
<script>
const CFG = __CFG__;
const $ = id => document.getElementById(id);
const esc = s => String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
function firstName(n){ const w=String(n||'').trim().split(/\s+/); return w.length>=2 ? w[1] : (w[0]||''); }
function fmtDate(iso){ if(!iso) return ''; const d=new Date(iso+'T00:00:00'); const M=['января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря']; return d.getDate()+' '+M[d.getMonth()]+' '+d.getFullYear(); }
// формы
$('course').innerHTML = CFG.courses.map(c=>`<option>${esc(c)}</option>`).join('');
$('teacher').innerHTML = '<option value="">—</option>'+CFG.teachers.map(t=>`<option>${esc(t)}</option>`).join('');
$('date').value = new Date().toISOString().slice(0,10);
$('kids').innerHTML = CFG.kids.length ? CFG.kids.map((k,i)=>`<div class="kidrow" data-i="${i}"><span><b>${esc(k.name)}</b> <span class="small">${esc(k.group)}</span></span><span class="t">${esc(k.date.slice(5).replace('-','.'))} ${esc(k.time)}</span></div>`).join('') : '<div class="small">первых занятий за два дня в CRM нет — заполните вручную</div>';
let cur = {};
document.querySelectorAll('.kidrow').forEach(r=>r.addEventListener('click',()=>{ const k=CFG.kids[+r.dataset.i]; cur=k; $('child').value=firstName(k.name); $('phone').value=k.phone; $('group').value=k.group; $('date').value=k.date;
  const opt=[...$('course').options].find(o=>k.course && (o.value===k.course || k.course.startsWith(o.value.slice(0,8)))); if(opt) $('course').value=opt.value; refresh(true); }));
function priceFor(){ return CFG.prices[$('course').value] || ''; }
function composeText(){
  const c=$('child').value||'ребёнок', t=$('teacher').value, g=$('next_group').value.trim(), n=$('next').value.trim(), p=$('price').value.trim(), pay=$('pay').value.trim(), can=$('can').value.trim();
  return `Здравствуйте! Сегодня ${c} был(а) у нас на первом занятии — ${$('course').value.toLowerCase()}. ${t? 'Педагог '+t+' составил(а)':'Педагог составил(а)'} карту развития, она во вложении 🌿\n`+
    (can? `Коротко: ${can}\n`:'') + (g? `Рекомендуем: ${g}\n`:'') + (n? `Следующее занятие — ${n}.\n`:'') +
    (p? `Абонемент — ${p}; при оплате в день первого занятия −10%, само занятие входит в абонемент.\n`:'') + (pay? `Оплатить: ${pay}\n`:'') + `Если есть вопросы — просто ответьте здесь.`;
}
function wrap(text, max){ const out=[]; for(const para of String(text||'').split(/\n+/)){ let line=''; for(const w of para.split(/\s+/)){ if(!w) continue; if((line+' '+w).trim().length>max){ if(line) out.push(line); line=w; } else line=(line+' '+w).trim(); } if(line) out.push(line); } return out; }
function tspans(lines, x, y, lh, cls){ return lines.map((l,i)=>`<text x="${x}" y="${y+i*lh}" class="${cls}">${esc(l)}</text>`).join(''); }
function svg(){
  const child=$('child').value||'Имя', course=$('course').value, date=fmtDate($('date').value), teacher=$('teacher').value, group=$('group').value;
  const blocks=[['ЧТО УЖЕ УМЕЕТ',$('can').value,'#1DA7E0'],['КУДА ИДЁМ',$('next_group').value,'#7DB928'],['С ЧЕГО НАЧНЁМ',$('start').value,'#F59C00']];
  const subLines=wrap(`Первое занятие · ${course}${group? ' · '+group:''}${teacher? ' · педагог '+teacher:''}`,62).slice(0,3);
  let y=368+subLines.length*34+62, body='';
  const avail=1090-y, cap=avail>=700?4:3;
  for(const [title,txt,color] of blocks){ const lines=wrap(txt||'—',50).slice(0,cap); body+=`<rect x="80" y="${y-28}" width="10" height="${32+lines.length*38}" rx="5" fill="${color}"/><text x="112" y="${y}" class="lbl" fill="${color}">${title}</text>`+tspans(lines,112,y+44,38,'body'); y+=44+lines.length*38+44; }
  const next=$('next').value, price=$('price').value;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1350" viewBox="0 0 1080 1350"><defs><style>
@font-face{font-family:M;font-weight:700;src:url(${CFG.fonts.bold}) format('truetype')}@font-face{font-family:M;font-weight:500;src:url(${CFG.fonts.medium}) format('truetype')}
.h{font:700 44px M,Arial,sans-serif;fill:#fff;letter-spacing:.06em}.d{font:500 26px M,Arial,sans-serif;fill:#cfd9ff}.name{font:700 66px M,Arial,sans-serif;fill:#312783}.sub{font:500 27px M,Arial,sans-serif;fill:#6c6a86}
.lbl{font:700 24px M,Arial,sans-serif;letter-spacing:.12em}.body{font:500 29px M,Arial,sans-serif;fill:#15132e}.f1{font:700 30px M,Arial,sans-serif;fill:#312783}.f2{font:500 25px M,Arial,sans-serif;fill:#15132e}.f3{font:500 21px M,Arial,sans-serif;fill:#6c6a86}</style></defs>
<rect width="1080" height="1350" fill="#ffffff"/><rect width="1080" height="210" fill="#312783"/>
<image href="${CFG.logo}" x="70" y="48" height="116"/>
<text x="1010" y="110" text-anchor="end" class="h">КАРТА РАЗВИТИЯ</text><text x="1010" y="156" text-anchor="end" class="d">${esc(date)}</text>
<text x="80" y="320" class="name">${esc(child)}</text>
${tspans(subLines,80,368,34,'sub')}
${body}
<rect x="0" y="1130" width="1080" height="220" fill="#EAE8F5"/>
<text x="80" y="1195" class="f1">${esc(next? 'Следующее занятие: '+next : 'Ждём на следующем занятии')}</text>
${tspans(wrap(price? 'Абонемент: '+price+' · при оплате в день первого занятия −10%, первое занятие входит' : '',70),80,1240,32,'f2')}
<text x="80" y="1312" class="f3">KidsUP · б-р Маршала Рокоссовского, 6к1В · +7 (495) 120-90-24 · kidsup.ru</text></svg>`;
}
function refresh(reprice){ if(reprice||!$('price').value) $('price').value=priceFor(); $('preview').innerHTML=svg(); if(!$('text').dataset.touched) $('text').value=composeText(); }
['child','phone','course','group','teacher','date','can','next_group','start','next','price','pay'].forEach(id=>$(id).addEventListener('input',()=>refresh(id==='course')));
$('text').addEventListener('input',()=>{$('text').dataset.touched='1'});
function toPng(cb){ const s=new XMLSerializer().serializeToString($('preview').querySelector('svg')); const img=new Image(); img.onload=()=>{ const c=document.createElement('canvas'); c.width=1080; c.height=1350; c.getContext('2d').drawImage(img,0,0); cb(c.toDataURL('image/png')); }; img.onerror=()=>alert('Не удалось собрать картинку'); img.src='data:image/svg+xml;charset=utf-8,'+encodeURIComponent(s); }
$('dl').onclick=()=>toPng(u=>{ const a=document.createElement('a'); a.href=u; a.download='karta_'+($('child').value||'razvitiya')+'.png'; a.click(); });
$('send').onclick=()=>{ if(!$('phone').value){alert('Телефон мамы пустой');return;} if(!$('can').value||!$('next_group').value){ if(!confirm('Карта без строк педагога — отправить всё равно?')) return; }
  $('status').textContent='Собираю картинку…'; toPng(u=>{ $('status').textContent='Отправляю…';
  fetch('/api/karta/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone:$('phone').value,child:$('child').value,uid:cur.uid||null,course:$('course').value,teacher:$('teacher').value,can:$('can').value,next_group:$('next_group').value,start:$('start').value,text:$('text').value,png:u})})
  .then(r=>r.json()).then(j=>{ $('status').innerHTML=(j.ok?'<span class=ok>Отправлено'+(j.dry_run?' (режим проверки, реальной отправки нет)':'')+'.</span> ':'<span class=err>Не отправлено.</span> ')+(j.error?esc(j.error)+' ':'')+(j.url?`<a href="${j.url}" target="_blank">картинка</a> `:'')+'<br>'+(j.log||[]).map(esc).join('<br>'); })
  .catch(e=>{$('status').innerHTML='<span class=err>Ошибка: '+esc(e)+'</span>';}); }); };
refresh(true);
</script></body></html>"""
