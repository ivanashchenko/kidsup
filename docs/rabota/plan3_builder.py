import json, html, datetime, collections, sys
sys.path.insert(0,"/home/user/kidsup")
from app.ankety import normalize_phone
import time
from app import mango
ank=json.load(open("docs/rabota/ankety_progress.json"))
dod=json.load(open("docs/rabota/dod_merged.json"))
dodph={x["phone"] for x in dod if x.get("phone")}
groups={}
for a in ank:
    ph=normalize_phone(a.get("phone_raw")); child=(a.get("child") or "").strip()
    if not ph: continue
    k=(ph, child.lower()[:12])
    g=groups.setdefault(k,{"phone":ph,"child":child,"ages":set(),"ints":set()})
    if a.get("age"): g["ages"].add(str(a["age"]))
    for i in a.get("interests") or []: g["ints"].add(i)
G=[{"phone":g["phone"],"child":g["child"],"ages":sorted(g["ages"]),"ints":sorted(g["ints"])} for g in groups.values()]
# кого сегодня уже набирали
msk=datetime.datetime.utcnow()+datetime.timedelta(hours=3)
day0=msk.replace(hour=0,minute=0,second=0,microsecond=0)
req=mango._call("stats/request",{"date_from":str(int(day0.timestamp())),"date_to":str(int(msk.timestamp())),
 "fields":"records, start, finish, answer, from_extension, from_number, to_extension, to_number, disconnect_reason"})
key=req.json().get("key"); lines=[]
for i in range(10):
    r=mango._call("stats/result",{"key":key})
    if r.status_code==200 and r.text.strip(): lines=r.text.strip().splitlines(); break
    time.sleep(3)
dialed=set(); talked=set()
for l in lines:
    p=[x.strip("[]") for x in l.split(";")]
    if len(p)<9 or not p[4]: continue
    e=normalize_phone(p[7])
    if not e: continue
    dialed.add(e)
    if int(p[3] or 0) and int(p[2])-int(p[3])>=10: talked.add(e)
todo=[g for g in G if g["phone"] not in dodph and g["phone"] not in dialed]
done=[g for g in G if g["phone"] not in dodph and g["phone"] in talked]
PRIOR={"шахматы":1,"изостудия":2,"английский язык":3,"подготовка к школе":4,"ментальная арифметика":5,
       "раннее развитие. первая школа":6,"раннее развитие. музыка и речь":6,"лицей для малышей":7,
       "мини-сад":8,"нулевой класс":8,"скорочтение":9,"коррекция почерка":9,"танцы":10,"логопед":11}
todo.sort(key=lambda g: min((PRIOR.get(i,50) for i in g["ints"]), default=99))
half=(len(todo)+1)//2
lena, anya = todo[:half], todo[half:]
def fmt(p): return "+7 %s %s-%s-%s" % (p[1:4],p[4:7],p[7:9],p[9:])
def rows(lst):
    out=[]
    for i,g in enumerate(lst,1):
        out.append('<tr><td class="n">%d</td><td><b>%s</b><span class="ag2">%s</span></td>'
                   '<td class="ph"><a href="tel:+%s">%s</a></td><td class="tg">%s</td><td class="res"></td></tr>' %
                   (i, html.escape(g["child"] or "имя уточнить"), html.escape(", ".join(g["ages"])),
                    g["phone"], fmt(g["phone"]), html.escape(", ".join(g["ints"]) or "— не отметили")))
    return "".join(out)
URG=[("Анна","79654089675","Звонила в 14:27, звали на английский в 15:00, сказала «выбегаем». Проверить, дошла ли. Не дошла — позвать на Неделю открытых уроков к мисс Марте."),
     ("Холяков Максим","79152748966","Звонок оборвался из-за связи на 29-й секунде, не договорили. Из анкет: подготовка, английский, менталка."),
     ("Ветрова София","79060688848","Написала «пн-ср нам подходит, ребёнку 9 лет» — я предложил английский пн-ср 16:00. ЖДЁТ ПОДТВЕРЖДЕНИЯ ЗАПИСИ, оформить."),
     ("Печугина Софья","79104957485","Уточняла про подготовку с 4 лет, я отправил свободные группы. Ждёт, чтобы выбрали время и записали."),
     ("Козлова Ангелина","79687111684","Записана на английский 13:00. В анкете отмечала ещё алфавитную живопись, ИЗО и подготовку — добавить второе направление."),
     ("Денис, 9 лет","79159156784","Английский вт 19:00 к Miss Marta + младший 5 лет на ПШ1. Запись до сих пор не оформлена в CRM, подтверждение не отправлено."),
     ("Мама Евы, 2 года","79850037212","Пробное ЗАВТРА 31.08 в 12:00. Обещали ответ про логопеда: он забит, 33 группы из 33 заняты, свободно одно окно у Марины в субботу 9:10."),
     ("Хоснетдинов Камиль","79161623345","Вчерашний ошибочный отказ — звонок оборвался на 11-й секунде. Статус вернул, нужен нормальный разговор.")]
urg="".join('<tr><td class="n">%d</td><td><b>%s</b></td><td class="ph"><a href="tel:+%s">%s</a></td>'
            '<td class="tg">%s</td><td class="res"></td></tr>' % (i,html.escape(n),p,fmt(p),html.escape(t))
            for i,(n,p,t) in enumerate(URG,1))
c=collections.Counter()
for g in todo:
    for i in g["ints"]: c[i]+=1
now=msk
tpl=open("docs/_plan_tpl2.html",encoding="utf-8").read()
page=tpl.format(when=now.strftime("%d.%m в %H:%M"), nurg=len(URG), urg=urg,
                ntodo=len(todo), ndone=len(done), nlena=len(lena), nanya=len(anya),
                lena=rows(lena), anya=rows(anya), log=c.get("логопед",0), tanc=c.get("танцы",0))
open("docs/plan_30avgusta.html","w",encoding="utf-8").write(page)
print("осталось обзвонить: %d | сегодня уже поговорили: %d | Лена %d, Аня %d" % (len(todo), len(done), len(lena), len(anya)))
