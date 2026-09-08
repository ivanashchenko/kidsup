import json, re
from collections import defaultdict, OrderedDict

P = '/tmp/claude-0/-home-user-kidsup/f2c35386-c271-55ec-b217-3b85ac2d6607/scratchpad/'
D = json.load(open(P + 'dossier_gruppy.json'))
G = OrderedDict((g['id'], g) for g in D['groups'])
PA = {'ПШ', 'АЯ'}
WEEKS = 4

# --- PRICES из app/main.py, regex (без импорта) ---
src = open('/home/user/kidsup/app/main.py', encoding='utf-8').read()
m = re.search(r'^PRICES = \{(.*?)^\}', src, re.S | re.M)
block = m.group(1)
PR = {}
cur = None
for line in block.splitlines():
    mc = re.match(r'\s*"([^"]+)": \{', line)
    if mc:
        cur = mc.group(1); PR[cur] = {}
    for ml in re.finditer(r'\("([^"]+)", (\d+), (\d+)\)', line):
        PR[cur][ml.group(1)] = int(ml.group(3))
price = {
    'ПШ': PR['Подготовка к школе']['8 занятий (2 р/нед)'],
    'АЯ': PR['Английский язык']['8 занятий (2 р/нед)'],
    'ШАХ': PR['Шахматы']['8 занятий (2 р/нед)'],
    'РР.Музыка и речь': PR['Раннее развитие']['Музыка и речь · 8 занятий (2 р/нед)'],
    'РР.Первая школа': PR['Раннее развитие']['Первая школа · 8 занятий (2 р/нед)'],
    'РР.Первая школа_1': PR['Раннее развитие']['Первая школа · 4 занятия (1 р/нед)'],
    'РР.Лицей': PR['Раннее развитие']['Лицей для малышей · 8 занятий (2 р/нед)'],
    'ИЗО': PR['ИЗО-студия']['8 занятий (2 р/нед)'],
    'МА': PR['Ментальная арифметика']['4 занятия'],
    'Робототехника': PR['Робототехника']['8 занятий'],
    'Мини-сад': PR['Английский детский сад']['Мини-сад, 20 посещений (5 дней)'],
    'Нулевой класс': PR['Английский детский сад']['Нулевой класс, 20 посещений (5 дней)'],
}
print('PRICES:', price)

def mp(g):
    d = g['direction']
    if d == 'РР.Первая школа' and g['lessons_per_week'] == 1:
        return price['РР.Первая школа_1']
    return price[d]

def base_paid(g):
    # правило досье: 50% пробных, 70% учатся без оплаты, 30% посетили, 50% подтвердили
    return g['paid'] + 0.5*g['trial_ahead'] + 0.7*g['uch_unpaid'] + 0.3*g['visited_unpaid'] + 0.5*g['confirmed']

def cost(g_dir, lpw, paid, sept, running=True):
    if not running: return 0
    if g_dir in ('Мини-сад', 'Нулевой класс'): return 0
    if g_dir in PA:
        per = (1000 if paid < 4 else 250*paid) if sept else 250*paid
    else:
        per = 1500
    return per * lpw * WEEKS

# ---------------- РЕКОМЕНДУЕМЫЙ ПЛАН ----------------
# (action, target_paid_3009, lpw_plan)  action: keep/merge/close/pause/hold
plan = {
 727735: ('merge', 0),   # ПШ Гр8 → Гр1
 727737: ('merge', 0),   # ПШ Гр9 → Гр2
 727740: ('keep', 4),    # ПШ Гр10
 727739: ('keep', 4),    # ПШ Гр11
 727751: ('keep', 5),    # ПШ Гр1 (+Гр8)
 727752: ('keep', 6),    # ПШ Гр2 (+пробные Гр9)
 727753: ('keep', 5),    # ПШ Гр3
 727754: ('keep', 5),    # ПШ Гр4 (+Параскив из Гр13)
 727732: ('keep', 3),    # ПШ Гр12
 727731: ('keep', 3),    # ПШ Гр5 (контроль 30.09)
 727734: ('keep', 5),    # ПШ Гр6
 727733: ('keep', 6),    # ПШ Гр7 (2 → ПШ2 19:00)
 731648: ('keep', 3),    # ПШ Гр14 — оставить до 30.09
 731649: ('keep', 4),    # ПШ Гр15
 727738: ('merge', 0),   # ПШ Гр13 → Гр4 (чт 19:00) / сб 1 р/нед в Гр12
 727760: ('merge', 0),   # АЯ Гр5 → Гр1 22.09 при ≥4 подтверждённых в Starters 5–8
 727741: ('keep', 4),    # АЯ Гр6 (3 пробных → Starters)
 727742: ('keep', 5),    # АЯ Гр7
 727743: ('keep', 3),    # АЯ Гр8 Movers
 727759: ('keep', 6),    # АЯ Гр1 (+Гр5)
 727750: ('keep', 4),    # АЯ Гр2
 727749: ('keep', 6),    # АЯ Гр3
 727755: ('keep', 5),    # АЯ Гр4
 731647: ('keep', 5),    # Лицей Гр1
 727758: ('keep', 4),    # Лицей Гр2
 727721: ('keep', 4),    # МиР Гр1
 727722: ('keep', 6),    # МиР Гр2
 727723: ('keep', 7),    # МиР Гр3 (2 долга → 12:45)
 727718: ('keep', 6),    # МиР Гр4
 727719: ('keep', 7),    # МиР Гр5
 727720: ('keep', 6),    # МиР Гр6
 727724: ('keep', 6),    # ПШк Гр1
 727725: ('keep', 3),    # ПШк Гр2 (контроль 30.09)
 727726: ('keep', 5),    # ПШк Гр3
 727727: ('merge', 0),   # ПШк вс Гр4 → Гр5
 727728: ('keep', 4),    # ПШк вс Гр5 (единая 1,5–3; <4 → закрыть)
 727744: ('keep', 3),    # ИЗО Гр1 — оставить до 30.09
 727746: ('keep', 5),    # ИЗО Гр2 (+2 из Гр4)
 727745: ('keep', 7),    # ИЗО Гр3
 727747: ('close', 0),   # ИЗО Гр4 → Гр2
 751255: ('pause', 0),   # МА 10:30
 727767: ('keep', 4),    # МА 12:00
 727768: ('keep', 3),    # МА 14:00
 727757: ('hold', 1),    # ШАХ Гр1: пт с Гр2, вс сохраняем
 727748: ('keep', 4),    # ШАХ Гр2
 751227: ('keep', 3),    # Робо 4–7
 751230: ('pause', 0),   # Робо 7–12
 727729: ('keep', 8),    # Сад
 727730: ('keep', 8),    # НК
}
assert set(plan) == set(G)
NEW = [  # name, direction, lpw, expected paid 30.09
 ('ПШ2 читающие пн-чт 19:00', 'ПШ', 2, 3),
 ('АЯ Starters 5–8 вт-чт 16:00 (с 22.09, ≥4 подтверждённых)', 'АЯ', 2, 3),
 ('Музыка и речь 2,2–3 ср-сб 12:45', 'РР.Музыка и речь', 2, 3),
]
NEW_COND = [('Первая школа 2–3 вт-чт 13:00 (только ≥4 подтверждённых к 12.09)', 'РР.Первая школа', 2, 2)]

def running_now(g):
    return g['live'] > 0  # МА 10:30 пустая

def scen_A():
    paid = rev = c_oct = c_sep = lpw = 0
    for g in G.values():
        r = running_now(g); bp = base_paid(g) if r else 0
        paid += bp; rev += bp*mp(g)
        c_oct += cost(g['direction'], g['lessons_per_week'], bp, False, r)
        c_sep += cost(g['direction'], g['lessons_per_week'], bp, True, r)
        lpw += g['lessons_per_week'] if r else 0
    return dict(name='А. Ничего не делать', paid=paid, rev=rev, c_oct=c_oct, c_sep=c_sep, lpw=lpw, groups=48, ads=0, sept_save=0)

# Б. только слияния по схеме досье (8 слияний + закрытия), без добора и новых групп
MERGE_B = {727735:727751, 727737:727752, 731648:727740, 727738:727732, 727760:727759, 727744:727746, 727747:727746, 727727:727728}
def scen_B(loss=0.2):
    eff = defaultdict(float)
    for gid, g in G.items():
        if not running_now(g): continue
        bp = base_paid(g)
        if gid in MERGE_B: eff[MERGE_B[gid]] += bp*(1-loss)
        else: eff[gid] += bp
    paid = rev = c_oct = c_sep = lpw = 0
    for gid, g in G.items():
        if gid in MERGE_B or gid == 751230 or not running_now(g): continue
        p = min(eff[gid], g['cap']+1)  # переполнение в приёмных группах до cap+1
        l = g['lessons_per_week'] if gid != 727757 else 1
        paid += p; rev += p*mp(g)
        c_oct += cost(g['direction'], l, p, False); c_sep += cost(g['direction'], l, p, True); lpw += l
    # сентябрьская разовая экономия минимумов: 2 недели 16–30.09
    sept_save = 0
    for gid in list(MERGE_B) + [751230]:
        g = G[gid]; per = 1000 if g['direction'] in PA else 1500
        sept_save += per * g['lessons_per_week'] * 2
    sept_save += 1500*1*2  # шахматы пт
    return dict(name='Б. Только слияния', paid=paid, rev=rev, c_oct=c_oct, c_sep=c_sep, lpw=lpw, groups=48-9, ads=0, sept_save=sept_save)

# В. только добор: все 49 групп как есть, внешние источники в существующие группы (по местам), без новых групп
EXT_C = {727740:0.5, 727739:1, 727751:0.5, 727752:0, 727754:0.5, 727732:1, 727731:1, 731648:1, 731649:0.5,  # ПШ 6
         727742:1.5, 727743:1, 727750:1, 727759:1,  # АЯ 4.5 (Starters 5–8 без слота → буфер АЯ теряется)
         727721:1, 727725:0.5, 727758:0.5, 727744:1, 727748:0.5, 751227:2, 727729:1, 727730:1, 727767:0.5}
def scen_C():
    paid = rev = c_oct = c_sep = lpw = 0
    for gid, g in G.items():
        r = running_now(g)
        if not r: continue
        p = min(base_paid(g) + EXT_C.get(gid, 0), g['cap'])
        paid += p; rev += p*mp(g)
        c_oct += cost(g['direction'], g['lessons_per_week'], p, False); c_sep += cost(g['direction'], g['lessons_per_week'], p, True)
        lpw += g['lessons_per_week']
    return dict(name='В. Только добор', paid=paid, rev=rev, c_oct=c_oct, c_sep=c_sep, lpw=lpw, groups=48, ads=65000, sept_save=0)

def scen_D():
    paid = rev = c_oct = c_sep = lpw = 0; n = 0
    for gid, g in G.items():
        act, tgt = plan[gid]
        if act in ('merge', 'close', 'pause'): continue
        l = g['lessons_per_week'] if act != 'hold' else 1
        paid += tgt; rev += tgt*mp(g)
        c_oct += cost(g['direction'], l, tgt, False); c_sep += cost(g['direction'], l, tgt, True); lpw += l; n += 1
    for name, d, l, ep in NEW:
        paid += ep; rev += ep*price[d]
        c_oct += cost(d, l, ep, False); c_sep += cost(d, l, ep, True); lpw += l; n += 1
    sept_save = 0
    for gid in (727735, 727737, 727738):  # ПШ одиночки: 2 недели минимума
        sept_save += 1000 * 2 * 2
    sept_save += 1500*2*2 + 1500*1*2 + 1500*1*2  # ИЗО Гр4, ПШк вс, шах пт
    sept_save += 1000*2*1  # АЯ Гр5 с 22.09 — 1 неделя
    return dict(name='Г. Рекомендуемый', paid=paid, rev=rev, c_oct=c_oct, c_sep=c_sep, lpw=lpw, groups=n, ads=65000, sept_save=sept_save)

A, B, C, Dd = scen_A(), scen_B(), scen_C(), scen_D()
now_rev = sum(g['paid']*mp(g) for g in G.values())
now_cost = sum(cost(g['direction'], g['lessons_per_week'], g['paid'], True, running_now(g)) for g in G.values())
now_lpw = sum(g['lessons_per_week'] for g in G.values() if running_now(g))
print(f"СЕЙЧАС: оплат 123, выручка {now_rev}, педагоги (сент) {now_cost}, маржа {now_rev-now_cost}, занятий/нед {now_lpw}")
for s in (A, B, C, Dd):
    marg = s['rev'] - s['c_oct']
    dm = marg - (A['rev'] - A['c_oct'])
    new_payers = s['paid'] - 123
    disc = new_payers * 0.15 * (s['rev']/s['paid'])  # первый месяц: −15% на новых абонементах
    print(f"{s['name']}: оплат {s['paid']:.1f} | выручка/мес {s['rev']:.0f} | педагоги окт {s['c_oct']:.0f} | маржа окт {marg:.0f} | Δ маржи vs А {dm:+.0f} | занятий/нед {s['lpw']} | групп {s['groups']} | касса 1-го мес ≈ {s['rev']-disc:.0f} | разово сент: реклама −{s['ads']} экономия +{s['sept_save']}")
print()
# по направлениям для плана Г
bd = defaultdict(lambda: [0,0,0,0])  # base, plan, cap_plan, live
for gid, g in G.items():
    d = g['direction']; act, tgt = plan[gid]
    bd[d][0] += base_paid(g) if running_now(g) else 0
    bd[d][1] += tgt if act not in ('merge','close','pause') else 0
    bd[d][2] += g['cap'] if act not in ('merge','close','pause') else 0
for name, d, l, ep in NEW:
    bd[d][1] += ep; bd[d][2] += 8 if d in PA else 7
for d, v in bd.items():
    print(f"{d}: база {v[0]:.1f} → план {v[1]} (мест {v[2]})")
print('сумма целей существующих', sum(t for a, t in plan.values() if a not in ('merge','close','pause')), '+ новые', sum(n[3] for n in NEW))
# внешние источники в плане Г vs база: по группам
print()
recv = {727735:727751, 727737:727752, 727738:727754, 727760:727759, 727747:727746, 727727:727728, 751230:751227}
eff = defaultdict(float)
for gid, g in G.items():
    if running_now(g): eff[recv.get(gid, gid)] += base_paid(g)
for gid, g in G.items():
    act, tgt = plan[gid]
    if act in ('merge','close','pause'): continue
    dlt = tgt - eff[gid]
    if abs(dlt) >= 0.45: print(f"  {dlt:+.1f}  цель {tgt}  база {eff[gid]:.1f}  {g['name']}")
# экономика слияний/удержаний спорных групп
for gid in (731648, 727760, 727744):
    g = G[gid]; print(g['name'], 'paid', g['paid'], 'live', g['live'], 'месячная выручка оплативших', g['paid']*mp(g))
