import json, html, datetime, collections, sys
sys.path.insert(0,"/home/user/kidsup")
from app.ankety import normalize_phone
dod=json.load(open("docs/rabota/dod_merged.json"))
d=json.load(open("docs/rabota/ankety_progress.json"))
groups={}
for a in d:
    ph=normalize_phone(a.get("phone_raw")); child=(a.get("child") or "").strip()
    if not ph: continue
    k=(ph, child.lower()[:12])
    g=groups.setdefault(k,{"phone":ph,"child":child,"ages":set(),"interests":set()})
    if a.get("age"): g["ages"].add(str(a["age"]))
    for i in a.get("interests") or []: g["interests"].add(i)
G=[{"phone":g["phone"],"child":g["child"],"ages":sorted(g["ages"]),"interests":sorted(g["interests"])} for g in groups.values()]
dodph={x["phone"] for x in dod if x.get("phone")}
slots=collections.defaultdict(list)
for x in dod:
    if x.get("phone"): slots[x["phone"]].append((x["slot"],x["course"],x["child"]))
MOVE11={"Бексалиева Карина","Кружкова Ева"}; MOVE12={"Агарунова Моника"}
TOMAMA={"Петуховская Вероника","Ермолаева Ольга","Малышев Максим (зачёркнут, перенесён в АЯ)"}
AY13={"Сосненко Лада"}; PSH14={"Овчарова Есения"}
DONE={"79683280360":"не придёт — у ребёнка сон, перенесли на вторник 10:00",
      "79263609157":"номер не тот — человек не понял, о чём речь",
      "79266748866":"звонили не тем Соколовым, верный номер 965 227-10-00"}
conf=[]
for ph,items in slots.items():
    kids="; ".join(dict.fromkeys(k for s,c,k in items)); what=[]; note=""
    for s,c,k in dict.fromkeys(items):
        if k in TOMAMA: what.append("Раннее развитие 3–4 года, 12:00"); note="сменилась группа"
        elif k in MOVE11: what.append("Раннее развитие 11:00 (было 12:00)"); note="сменилось время"
        elif k in MOVE12: what.append("Раннее развитие 12:00 (было 11:00)"); note="сменилось время"
        elif k in AY13 and s=="15:00": what.append("Английский 13:00 (было 15:00), потом робо в 14:00"); note="сменилось время"
        elif k in PSH14 and s=="15:00": what.append("Подготовка 14:00 (было 15:00)"); note="сменилось время"
        else: what.append("%s (%s)" % (c,s))
    conf.append({"phone":ph,"kids":kids,"what":"; ".join(dict.fromkeys(what)),
                 "note":note,"done":DONE.get(ph,"")})
conf.sort(key=lambda x: (0 if x["note"] and not x["done"] else (2 if x["done"] else 1), x["kids"]))
notdod=[g for g in G if g["phone"] not in dodph]
PRIOR={"шахматы":1,"изостудия":2,"английский язык":3,"подготовка к школе":4,"ментальная арифметика":5,
       "раннее развитие. первая школа":6,"раннее развитие. музыка и речь":6,"лицей для малышей":7,
       "мини-сад":8,"нулевой класс":8,"скорочтение":9,"коррекция почерка":9,"танцы":10,"логопед":11}
notdod.sort(key=lambda g: min((PRIOR.get(i,50) for i in g["interests"]), default=99))
def fmt(p): return "+7 %s %s-%s-%s" % (p[1:4], p[4:7], p[7:9], p[9:])
def rc(lst):
    out=[]
    for i,x in enumerate(lst,1):
        tag=""
        if x["done"]: tag='<span class="dn">уже звонили: %s</span>' % html.escape(x["done"])
        elif x["note"]: tag='<span class="ch">%s</span>' % html.escape(x["note"])
        out.append('<tr><td class="n">%d</td><td><b>%s</b>%s</td><td class="ph"><a href="tel:+%s">%s</a></td>'
                   '<td class="tg">%s</td><td class="res"></td></tr>' %
                   (i, html.escape(x["kids"] or "—"), tag, x["phone"], fmt(x["phone"]), html.escape(x["what"])))
    return "".join(out)
def ra(lst):
    out=[]
    for i,g in enumerate(lst,1):
        out.append('<tr><td class="n">%d</td><td><b>%s</b><span class="ag2">%s</span></td>'
                   '<td class="ph"><a href="tel:+%s">%s</a></td><td class="tg">%s</td><td class="res"></td></tr>' %
                   (i, html.escape(g["child"] or "имя уточнить"), html.escape(", ".join(g["ages"])),
                    g["phone"], fmt(g["phone"]), html.escape(", ".join(g["interests"]) or "— не отметили")))
    return "".join(out)
c=collections.Counter()
for g in notdod:
    for i in g["interests"]: c[i]+=1
now=datetime.datetime.utcnow()+datetime.timedelta(hours=3)
URG = """<tr><td class="n">1</td><td><b>Соколовы</b><span class="ch">верный номер</span></td><td class="ph"><a href="tel:+79652271000">+7 965 227-10-00</a></td><td class="tg">Записаны на раннее развитие 12:00. Утром звонили по номеру из CRM и попали в другую семью — эти ещё не знают. Слот через час.</td><td class="res"></td></tr>
<tr><td class="n">2</td><td><b>Божена, 10 лет</b><span class="ch">горит сегодня</span></td><td class="ph"><a href="tel:+79161943692">+7 916 194-36-92</a></td><td class="tg">Заявка с сайта <b>на сегодняшний ДОД</b>, слот не выбран, в списках её нет. Назначить время (английский 13:00 или 15:00) и вписать.</td><td class="res"></td></tr>
<tr><td class="n">3</td><td><b>Денис, 9 лет</b><span class="ch">обещали вчера</span></td><td class="ph"><a href="tel:+79159156784">+7 915 915-67-84</a></td><td class="tg">Английский вторник 19:00 к Miss Marta, младшему 5 лет — подготовка ПШ1 вт+сб. <b>Запись не оформлена, подтверждение не отправлено.</b> Уточнить имя младшего.</td><td class="res"></td></tr>
<tr><td class="n">4</td><td><b>Мама Евы, 2 года</b><span class="ch">обещали вчера</span></td><td class="ph"><a href="tel:+79850037212">+7 985 003-72-12</a></td><td class="tg">Пробное <b>завтра 31.08 в 12:00</b>. Обещали узнать про логопеда — он забит, 37 из 38. Сказать честно про лист ожидания и подтвердить завтрашнее занятие.</td><td class="res"></td></tr>
<tr><td class="n">5</td><td><b>Софья, 3–4 года</b><span class="ch">нет таких занятий</span></td><td class="ph"><a href="tel:+79104957485">+7 910 495-74-85</a></td><td class="tg">Две заявки с сайта: <b>актёрское мастерство и хореография</b> — таких направлений нет. Честно сказать, предложить ИЗО и танцы (набираем), позвать на экскурсию.</td><td class="res"></td></tr>
<tr><td class="n">6</td><td><b>Хоснетдинов Камиль</b><span class="ch">наша ошибка</span></td><td class="ph"><a href="tel:+79161623345">+7 916 162-33-45</a></td><td class="tg">Вчера звонок оборвался на 11-й секунде, клиент не сказал ни слова — а ему поставили «Отказ». Статус вернул. Перезвонить.</td><td class="res"></td></tr>
<tr><td class="n">7</td><td><b>Люсова Анисия</b></td><td class="ph"><a href="tel:+79850962964">+7 985 096-29-64</a></td><td class="tg">Вчера попали на автоответчик оператора. Из списка тех, кому ни разу не звонили.</td><td class="res"></td></tr>"""
tpl = open("/home/user/kidsup/docs/_plan_tpl.html", encoding="utf-8").read()
page = tpl.format(when=now.strftime("%d.%m в %H:%M"), nconf=len(conf), nank=len(notdod),
                  urg=URG, conf_rows=rc(conf), lena_rows=ra(notdod),
                  anya_rows=ra(list(reversed(notdod))),
                  log=c.get("логопед",0), tanc=c.get("танцы",0))
open("docs/plan_30avgusta.html","w",encoding="utf-8").write(page)
print("план: подтверждений %d, анкет %d" % (len(conf), len(notdod)))
