# -*- coding: utf-8 -*-
"""Страница педагога: три строки и явка по каждому ребёнку с первого занятия.

У педагогов нет входа в МойКласс и нет админского пароля, поэтому вход —
по ссылке с ключом (настройка pedagog_key), которую Ира рассылает в чат
педагогов. Педагог выбирает себя, ребёнка, отмечает «был / не был» и пишет
три строки: что умеет, куда идём, с чего начнём. Запись уходит:
  • в таблицу karta_notes — оттуда /karta подставляет строки администратору,
  • комментарием в карточку ребёнка в МойКлассе,
  • пунктом в инбокс дежурного: «карта готова — отправить маме».
Явку педагог тоже отмечает здесь: сервер ставит visit/skip на записи занятия."""
from __future__ import annotations
import json, html as H, datetime as dt, logging
from . import db

log = logging.getLogger("kidsup.pedagog")
EXAMPLES = {
    "Подготовка к школе": ("Знает все буквы, читает слоги и короткие слова, считает до 20, хорошо держит карандаш.",
                           "ПШ2 читающие, пн-чт 18:00: скорость чтения и пересказ.",
                           "Слоговые таблицы и печатные буквы; дома 10 минут в день читать вывески."),
    "Английский язык": ("Понимает простые инструкции, знает цвета и счёт до 10, охотно повторяет за педагогом.",
                        "Pre-A1 Starters, вт-чт 17:00: говорим с первого занятия, тема «семья и дом».",
                        "Приветствие и знакомство по-английски; дома — 3 фразы в день из карточки."),
    "Логопед": ("Речь фразовая, звуки С и З чистые, Р и Л пока заменяет; артикуляционные упражнения выполняет с интересом.",
                "Индивидуально 2 раза в неделю, начинаем с постановки Р.",
                "Артикуляционная гимнастика 5 минут утром и вечером по карточке, которую даём на первом занятии."),
    "Раннее развитие": ("Держит внимание 5–7 минут, повторяет движения, отзывается на музыку, к концу занятия включилась в круг.",
                        "«Первая школа» вт-чт 12:00, группа 2–3 года.",
                        "Ритуалы начала и конца занятия; дома — пальчиковая игра из занятия."),
    "Ментальная арифметика": ("Считает до 20, быстро понял принцип абакуса, удерживает внимание всё занятие.",
                              "Начинающие, вс 12:30, 90 минут.",
                              "Счёт на абакусе до 10 двумя руками; дома 5 минут в день на тренажёре."),
    "Шахматы": ("Знает ходы всех фигур, понимает шах, партию доигрывает до конца.",
                "Продолжающие, пт 18:00 + вс 11:00: разбор партий и мини-турниры.",
                "Тактика «вилка» и «связка»; дома 2 задачи в день из тетради."),
    "ИЗО-студия": ("Уверенно смешивает цвета, держит кисть правильно, доводит работу до конца.",
                   "Группа 3 пн-ср 18:00, живопись.",
                   "Композиция и передний план; дома — дорисовать фон работы."),
    "Робототехника": ("Собрал модель по схеме сам, понял, как меняется скорость мотора, задаёт вопросы «почему».",
                      "Младшая группа пт 16:00 + вс 14:30.",
                      "Датчики и первая программа; дома — придумать, что робот должен уметь."),
}


def _init(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS karta_notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, day TEXT, uid INTEGER, child TEXT, phone TEXT,
        course TEXT, grp TEXT, teacher TEXT, visit INTEGER, can TEXT, next_group TEXT, start TEXT, lesson_record_id INTEGER, sent INTEGER DEFAULT 0)""")


def notes_for(uid: int | None = None, days: int = 3) -> list[dict]:
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    with db.get_conn() as conn:
        _init(conn)
        q = "SELECT * FROM karta_notes WHERE day >= ?" + (" AND uid = ?" if uid else "") + " ORDER BY ts DESC"
        rows = conn.execute(q, (since, uid) if uid else (since,)).fetchall()
    return [dict(r) for r in rows]


def key_ok(k: str) -> bool:
    want = db.get_setting("pedagog_key", "")
    return bool(want) and k == want


def page(k: str) -> str:
    from . import karta
    if not key_ok(k):
        return "<h2 style='font-family:sans-serif;margin:2rem'>Ссылка недействительна. Попросите новую у администратора.</h2>"
    data = karta._kids(days=2)
    kids = data.get("kids", []) if isinstance(data, dict) else []
    teachers = data.get("teachers", []) if isinstance(data, dict) else []
    done = {(n["uid"]) for n in notes_for(days=2)}
    for kd in kids:
        kd["done"] = kd["uid"] in done
    cfg = {"kids": kids, "teachers": teachers, "examples": EXAMPLES, "k": k}
    return TEMPLATE.replace("__CFG__", json.dumps(cfg, ensure_ascii=False).replace("</", "<\\/"))


def save(payload: dict) -> dict:
    if not key_ok(str(payload.get("k") or "")):
        return {"ok": False, "error": "ключ не подходит"}
    uid = payload.get("uid")
    can, nxt, start = (str(payload.get(x) or "").strip() for x in ("can", "next_group", "start"))
    visit = payload.get("visit")  # True / False / None
    teacher = str(payload.get("teacher") or "").strip()
    child = str(payload.get("child") or "").strip()
    if not uid or not teacher:
        return {"ok": False, "error": "нужны ребёнок и педагог"}
    if visit is not False and not (can and nxt):
        return {"ok": False, "error": "заполните хотя бы «что умеет» и «куда идём»"}
    now = dt.datetime.now()
    with db.get_conn() as conn:
        _init(conn)
        conn.execute("INSERT INTO karta_notes (ts, day, uid, child, phone, course, grp, teacher, visit, can, next_group, start) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     (now.isoformat(timespec="minutes"), now.date().isoformat(), int(uid), child, str(payload.get("phone") or ""),
                      str(payload.get("course") or ""), str(payload.get("group") or ""), teacher,
                      None if visit is None else int(bool(visit)), can, nxt, start))
    out = {"ok": True, "crm": "", "visit": "", "inbox": ""}
    # МойКласс: комментарий в карточку и отметка явки на записи занятия
    try:
        from .moyklass_client import MoyklassClient
        from . import sync
        mk = MoyklassClient(sync.get_api_key())
        try:
            txt = (f"Педагог {teacher}, первое занятие {payload.get('course') or ''} {payload.get('group') or ''}: "
                   + ("НЕ ПРИШЁЛ. " if visit is False else "")
                   + (f"Умеет: {can} | Куда идём: {nxt} | Начнём: {start}" if can or nxt else ""))
            mk.post("/v1/company/userComments", {"userId": int(uid), "comment": txt[:1000], "showToUser": False})
            out["crm"] = "комментарий записан"
            if visit is not None and payload.get("date"):
                recs = mk.get("/v1/company/lessonRecords", {"userId": int(uid), "date": [payload["date"], payload["date"]], "includeLessons": "true", "limit": 10})
                recs = (recs.get("lessonRecords") if isinstance(recs, dict) else recs) or []
                for r in recs:
                    if (r.get("lesson") or {}).get("beginTime", "")[:5] == str(payload.get("time") or "")[:5] or len(recs) == 1:
                        try:
                            mk.post(f"/v1/company/lessonRecords/{r['id']}", {"visit": bool(visit), "skip": not bool(visit), "test": bool(r.get("test"))})
                            out["visit"] = "явка отмечена в CRM"
                        except Exception as e:
                            out["visit"] = f"явку отметить не удалось ({str(e)[:60]}) — отметит администратор"
                        break
        finally:
            mk.close()
    except Exception as e:
        out["crm"] = f"CRM недоступна: {str(e)[:80]}"
    # инбокс дежурному
    try:
        from . import autopilot
        names = {202856: "Лена", 232805: "Аня", 232763: "Ира", 154181: "Лиза"}
        duty = autopilot._admins_today() or []
        who = names.get((duty[0] or {}).get("managerId"), "Аня") if duty else "Аня"
        with db.get_conn() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS plan_inbox (id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, ts TEXT,
                            who TEXT, text TEXT, phone TEXT, source TEXT, done INTEGER DEFAULT 0)""")
            if visit is False:
                text = f"{child}: педагог {teacher} отметил — НЕ пришёл на первое занятие ({payload.get('group') or ''}). Позвонить сегодня, предложить новую дату."
            else:
                text = (f"{child}: педагог {teacher} заполнил карту развития — открыть /karta, выбрать ребёнка (строки уже подставлены), "
                        f"вставить ссылку на оплату и отправить маме в течение часа. Куда идём: {nxt[:80]}")
            conn.execute("INSERT INTO plan_inbox (day, ts, who, text, phone, source) VALUES (?,?,?,?,?,?)",
                         (now.date().isoformat(), now.isoformat(timespec="minutes"), who, text[:400], str(payload.get("phone") or "")[:20], f"педагог {now.strftime('%H:%M')}"))
        out["inbox"] = f"пункт поставлен: {who}"
    except Exception as e:
        out["inbox"] = f"инбокс: {str(e)[:60]}"
    return out


TEMPLATE = r"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Первое занятие — заметки педагога</title>
<style>
:root{--ink:#15132e;--muted:#6c6a86;--line:#e4e2f0;--bg:#f8f7fc;--card:#fff;--indigo:#312783;--blue:#1DA7E0;--green:#7DB928;--amber:#F59C00;--red:#E30613}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 -apple-system,"Segoe UI",Roboto,Arial,sans-serif}
.wrap{max-width:720px;margin:0 auto;padding:16px 14px 60px}
h1{font-size:22px;margin:0 0 4px;color:var(--indigo)} .sub{color:var(--muted);margin:0 0 12px;font-size:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;margin-bottom:12px}
label{display:block;font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin:10px 0 4px}
input,select,textarea{width:100%;font:inherit;padding:10px;border:1px solid var(--line);border-radius:10px;background:#fff;color:var(--ink)}
textarea{min-height:70px}
.kid{display:flex;justify-content:space-between;gap:8px;padding:10px;border:1px solid var(--line);border-radius:10px;margin:6px 0;cursor:pointer}.kid>span:first-child{min-width:0;overflow-wrap:anywhere}
.kid.sel{border-color:var(--indigo);background:#f1effb}.kid.done{opacity:.55}.kid .t{color:var(--muted);white-space:nowrap;font-size:14px}
.seg{display:flex;gap:8px;margin-top:6px}.seg button{flex:1;padding:10px;border:1px solid var(--line);border-radius:10px;background:#fff;font:inherit;cursor:pointer}
.seg button.on-yes{background:#eef7e0;border-color:var(--green)}.seg button.on-no{background:#fce8e9;border-color:var(--red)}
.btn{width:100%;border:0;border-radius:12px;padding:14px;font:inherit;font-weight:700;font-size:17px;background:var(--green);color:#fff;cursor:pointer;margin-top:14px}
.ex{font-size:13px;color:var(--muted);margin-top:4px}.ex b{color:var(--indigo);cursor:pointer}
.ok{color:#4d7511}.err{color:var(--red)}
.rules{font-size:14px;color:var(--muted)} .rules li{margin:4px 0}
</style></head><body><div class="wrap">
<h1>Первое занятие — заметки педагога</h1>
<p class="sub">Три строки и явка по каждому ребёнку, который был у вас на первом занятии. Заполняйте в течение 15 минут после занятия: администратор соберёт из этого карту развития и отправит маме, пока впечатление живое.</p>
<div class="card">
<label>Я — педагог</label><select id="teacher"><option value="">выберите себя</option></select>
<label>Ребёнок (первые занятия сегодня и вчера)</label><div id="kids"></div>
<label>Был на занятии?</label><div class="seg"><button id="yes">Был</button><button id="no">Не пришёл</button></div>
<div id="form">
<label>1 · Что уже умеет <span class="ex">— только то, что видели сами, 1–2 предложения, без «не умеет»</span></label><textarea id="can"></textarea>
<div class="ex" id="ex1"></div>
<label>2 · Куда идём <span class="ex">— группа из расписания и над чем в ней работаем</span></label><textarea id="next_group"></textarea>
<div class="ex" id="ex2"></div>
<label>3 · С чего начнём <span class="ex">— первые две недели и одна вещь для дома</span></label><textarea id="start"></textarea>
<div class="ex" id="ex3"></div>
</div>
<button class="btn" id="save">Сохранить</button>
<div id="status" style="margin-top:10px;font-size:14px"></div>
</div>
<div class="card rules"><b>Правила трёх строк</b><ul>
<li>Пишем, что ребёнок <b>умеет</b>, а не чего не умеет. Без диагнозов и оценок: для этого есть разговор с мамой.</li>
<li>Простыми словами, как сказали бы маме у двери. Без методических терминов.</li>
<li>Во второй строке всегда название группы из расписания и время: это и есть предложение остаться.</li>
<li>В третьей — одна вещь для дома. Мама должна уйти с делом, а не с оценкой.</li>
<li>Не пришёл — нажмите «Не пришёл», строки не нужны, администратор перезвонит.</li></ul></div>
</div>
<script>
const CFG=__CFG__; const $=id=>document.getElementById(id); const esc=s=>String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
$('teacher').innerHTML+=CFG.teachers.map(t=>`<option>${esc(t)}</option>`).join('');
try{ const t=localStorage.getItem('pedagog_name'); if(t) $('teacher').value=t; }catch(e){}
$('teacher').addEventListener('change',()=>{try{localStorage.setItem('pedagog_name',$('teacher').value)}catch(e){}});
let cur=null, visit=null;
function firstName(n){const w=String(n||'').trim().split(/\s+/);return w.length>=2?w[1]:(w[0]||'');}
$('kids').innerHTML = CFG.kids.length? CFG.kids.map((k,i)=>`<div class="kid ${k.done?'done':''}" data-i="${i}"><span><b>${esc(k.name)}</b><br><span class="t">${esc(k.group)}</span></span><span class="t">${esc(k.date.slice(5).replace('-','.'))} ${esc(k.time)}${k.done?' · есть':''}</span></div>`).join('') : '<div class="ex">первых занятий за два дня в расписании нет</div>';
function examples(course){ const e=CFG.examples[course]||CFG.examples[Object.keys(CFG.examples).find(c=>course&&c.startsWith(course.slice(0,6)))]||null; ['ex1','ex2','ex3'].forEach((id,i)=>{$(id).innerHTML=e?`пример: <b data-i="${i}">${esc(e[i])}</b>`:'';}); document.querySelectorAll('.ex b').forEach(b=>b.onclick=()=>{[$('can'),$('next_group'),$('start')][+b.dataset.i].value=b.textContent;}); }
document.querySelectorAll('.kid').forEach(d=>d.onclick=()=>{document.querySelectorAll('.kid').forEach(x=>x.classList.remove('sel')); d.classList.add('sel'); cur=CFG.kids[+d.dataset.i]; examples(cur.course); $('status').textContent='';});
$('yes').onclick=()=>{visit=true;$('yes').className='on-yes';$('no').className='';$('form').style.display='';};
$('no').onclick=()=>{visit=false;$('no').className='on-no';$('yes').className='';$('form').style.display='none';};
$('save').onclick=()=>{ if(!$('teacher').value){alert('Выберите себя в списке педагогов');return;} if(!cur){alert('Выберите ребёнка');return;} if(visit===null){alert('Отметьте, был ли ребёнок');return;}
 $('status').textContent='Сохраняю…';
 fetch('/api/pedagog/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({k:CFG.k,uid:cur.uid,child:firstName(cur.name),phone:cur.phone,course:cur.course,group:cur.group,date:cur.date,time:cur.time,teacher:$('teacher').value,visit:visit,can:$('can').value,next_group:$('next_group').value,start:$('start').value})})
 .then(r=>r.json()).then(j=>{ if(j.ok){ $('status').innerHTML='<span class=ok>Сохранено. '+esc(j.crm)+'. '+esc(j.visit)+'. '+esc(j.inbox)+'</span>'; ['can','next_group','start'].forEach(id=>$(id).value=''); visit=null; $('yes').className='';$('no').className=''; document.querySelector('.kid.sel')?.classList.add('done'); } else $('status').innerHTML='<span class=err>'+esc(j.error||'ошибка')+'</span>'; })
 .catch(e=>{$('status').innerHTML='<span class=err>Ошибка сети: '+esc(e)+'</span>';}); };
</script></body></html>"""
