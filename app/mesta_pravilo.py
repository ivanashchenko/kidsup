# -*- coding: utf-8 -*-
"""Одно правило счёта свободных мест — «как в документе владельца».

13.11.2026, Борис (решение М2, дословно: «М, П, Т — по рекомендациям»): на вопрос «сколько
свободных мест считать верным?» принято «как в вашем документе: без закрытых для записи
групп, сад и нулевой класс по дням». До этого дня один и тот же вопрос считали пять мест
по-своему, и в один день страницы показывали 146 («Места»), 78 (документ «Места в группах»)
и 64 («Шахматка») свободных. Причина: «Места» считали все группы подряд, сад — списком
до 20 детей, а «Подтвердил заявку» — занятым местом; «Подбор группы» считал занятым местом
любую запись, кроме «Отказался» и «Завершил» (отработки, новые и старые заявки, «не пришёл
на пробное»), и писал «мест нет» там, где места есть.

Правило — дословно из документа (docs/rabota/mesta_boris.py, поручение Бориса 03.11.2026):
  · всего мест — вместимость группы в МойКлассе; занято — дети в статусе «Учится»;
    пробники — «Записался на пробное» и «Посетил пробное»; остаётся = всего − занято − пробники;
  · свободно по центру — сумма «остаётся» по группам, где оно больше нуля; переполненная
    группа свободные места других групп не уменьшает и показана отдельно («переполнено»);
  · мини-сад и нулевой класс — по дням недели и в итог по центру не входят: ребёнок занимает
    место в те дни, на которые у него есть запись на прошлой или следующей неделе;
  · закрытые для записи группы (список CLOSED и группы без занятий на две недели вперёд),
    логопеды, «Заявки» и праздники в статистику не входят.

Модуль нарочно без импортов приложения (ни базы, ни настроек): его берут и сервер
(app/mesta.py — страницы «Места», «Подбор группы», блок на пульте), и документ владельца
(docs/rabota/mesta_boris.py — запускается рутиной вне сервера, по выгрузке /export/raw).
Правило одно — менять его только здесь.

Что здесь точнее прежней копии в документе (числа документа от этого меняются, см. отчёт C1):
  · запись в группу, удалённая в МойКлассе (пометка mk_udaleno, с 12.11.2026), места не занимает;
  · ребёнок с двумя записями в одну группу («Записался на пробное» и «Учится») — одно место,
    а не два: считаем детей, а не строки записей.
"""
from __future__ import annotations

import datetime as dt
import re
from collections import Counter, defaultdict

PRAVILO = "без закрытых для записи групп; сад и нулевой — по дням"
PRAVILO_PODROBNO = ("Всего мест — вместимость группы в МойКлассе. Занято — дети в статусе «Учится». "
                    "Пробники — «Записался на пробное» и «Посетил пробное». Остаётся = всего − занято − пробники, "
                    "по каждой группе; переполненная группа свободные места других групп не уменьшает. "
                    "Мини-сад и нулевой класс — по дням недели и в итог не входят. Закрытые для записи группы, "
                    "логопеды, «Заявки» и праздники в счёт не входят.")

UCHITSYA = {2}                      # «Учится»
PROBNYE = {58132, 58131}            # «Записался на пробное», «Посетил пробное» (как в бланках групп)
BYL_NA_PROBNOM = 58131
WD = ['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс']
BUDNI = ('пн', 'вт', 'ср', 'чт', 'пт')
# Мини-сад, решение Бориса 15.10.2026: пн и ср до 6 детей (комнату 3 занимает логопед), вт, чт, пт — до 8
MINISAD_CAP = {'пн': 6, 'вт': 8, 'ср': 6, 'чт': 8, 'пт': 8}
# Закрыты для записи (решения 13.10, 24.10 и 03.11): в статистику не входят
CLOSED = [
    (r'^ПШ.*\(Гр5\)', 'ПШ Гр5 — не набираем'),
    (r'^ПШ.*\(Гр15\)', 'ПШ Гр15 — не набираем'),
    (r'^МА_вс_12:00', 'Ментальная арифметика вс 12:00 — не набираем'),
    (r'^ШАХ_Группа 1', 'Шахматы Гр1 — не набираем'),
    (r'^Робототехника.*7-12', 'Робототехника 7–12 лет — группы по сути нет (Борис, 03.11)'),
]
NET_ZANYATIY = 'нет занятий в расписании на 2 недели вперёд'
NAPR = [('АЯ', 'Английский'), ('ПШ', 'Подготовка к школе'), ('Нулевой', 'Нулевой класс'), ('НК', 'Нулевой класс'),
        ('РР.Первая школа', 'Первая школа'), ('РР.Музыка и речь', 'Музыка и речь'), ('РР.Лицей', 'Лицей для малышей'),
        ('ИЗО', 'ИЗО'), ('МА', 'Ментальная арифметика'), ('ШАХ', 'Шахматы'), ('Шахматы', 'Шахматы'),
        ('Робот', 'Робототехника'), ('Мини-сад', 'Мини-сад'), ('ЛГ', 'Логопед')]
PO_DNYAM = ('Мини-сад', 'Нулевой класс')
# порядок направлений в таблицах — как в документе владельца
PORYADOK_NAPR = ['Подготовка к школе', 'Английский', 'Музыка и речь', 'Первая школа', 'Лицей для малышей', 'ИЗО',
                 'Ментальная арифметика', 'Робототехника', 'Шахматы']
SEZON = '2627_'


def napr(n: str) -> str:
    """Направление по имени группы без «2627_»."""
    for p, d in NAPR:
        if n.startswith(p):
            return d
    return 'Прочее'


def zakryta(n: str) -> str | None:
    """Причина, если группа закрыта для записи решением владельца (список CLOSED)."""
    return next((w for rx, w in CLOSED if re.search(rx, n)), None)


# --- выгрузка МойКласса (/export/raw, поле raw таблиц) → вид, который понимает poschitat ---

def gruppa_iz_mk(c: dict) -> dict:
    return {'id': c.get('id'), 'name': c.get('name') or '', 'status': c.get('status'), 'cap': c.get('maxStudents')}


def zapis_iz_mk(j: dict) -> dict:
    return {'id': j.get('id'), 'user_id': j.get('userId'), 'class_id': j.get('classId'),
            'status_id': j.get('statusId')}


def zanyatie_iz_mk(x: dict) -> dict:
    return {'class_id': x.get('classId'), 'date': str(x.get('date') or '')[:10],
            'teacher_ids': list(x.get('teacherIds') or []),
            'records': [{'user_id': r.get('userId'), 'test': bool(r.get('test'))}
                        for r in (x.get('records') or []) if r]}


def poschitat(classes, joins, lessons, today: dt.date, managers: dict | None = None,
              udaleno_joins=()) -> dict:
    """Места по правилу документа. Ничего не читает и не пишет — только считает.

    classes — [{'id', 'name', 'status', 'cap'}] (все группы; лишние отсеет само правило);
    joins — [{'id', 'user_id', 'class_id', 'status_id'}]; lessons — [{'class_id', 'date',
    'teacher_ids', 'records': [{'user_id', 'test'}]}] (хватает окна «неделя назад — две вперёд»);
    managers — {id: имя} для подписи педагога; udaleno_joins — id записей в группу, удалённых
    в МойКлассе (db.udalennye('joins')): такие записи места не занимают.

    Возвращает:
      'groups'  — открытые группы: {'cid', 'name', 'polnoe', 'dir', 'cap', 'busy', 'prob', 'left',
                  'teacher'}; у мини-сада и нулевого класса ещё 'days' {день: cap/busy/prob/left};
      'closed'  — закрытые для записи, с причиной 'why';
      'vne'     — вне статистики, но с тем же счётом (логопеды и прочее): для страницы подбора;
      'tot'     — итог по открытым группам без сада и нулевого: cap, busy, prob, left (сальдо),
                  free (сумма свободных), over (сумма переполнений);
      'days'    — {направление: дни} для сада и нулевого.
    В строках групп есть множества id детей ('_uch', '_prob', '_byl') — для счёта «оплатили»;
    наружу (в страницы и API) их не отдавать.
    """
    managers = managers or {}
    ud = {int(i) for i in udaleno_joins if i is not None}
    d0, d1 = today - dt.timedelta(days=7), today + dt.timedelta(days=13)
    s_d0, s_d1, s_today = d0.isoformat(), d1.isoformat(), today.isoformat()
    C = {c['id']: c for c in classes if c and c.get('id') is not None}
    les = defaultdict(list)
    for x in lessons:
        if x and x.get('class_id') in C and s_d0 <= (x.get('date') or '') <= s_d1:
            les[x['class_id']].append(x)
    # статусы ребёнка в группе: один ребёнок — одно место, сколько бы записей у него ни было
    st: dict = defaultdict(set)
    for j in joins:
        if not j or (j.get('id') is not None and j['id'] in ud):
            continue
        st[(j.get('class_id'), j.get('user_id'))].add(j.get('status_id'))
    po_gruppe: dict = defaultdict(dict)
    for (cid, uid), ss in st.items():
        po_gruppe[cid][uid] = ss

    def _vid(ss) -> str | None:
        if ss & UCHITSYA:
            return 'uch'
        if ss & PROBNYE:
            return 'prob'
        return None

    groups, closed_rows, vne = [], [], []
    for cid, c in C.items():
        name = c.get('name') or ''
        if not name.startswith(SEZON) or c.get('status') == 'archive':
            continue
        n = name[len(SEZON):]
        if 'аявк' in n.lower() or n.startswith('Праздник'):
            continue
        dr = napr(n)
        deti = po_gruppe.get(cid, {})
        u_uch = {u for u, ss in deti.items() if _vid(ss) == 'uch'}
        u_prob = {u for u, ss in deti.items() if _vid(ss) == 'prob'}
        u_byl = {u for u in u_prob if BYL_NA_PROBNOM in deti[u]}
        fut = [x for x in les.get(cid, []) if x['date'] >= s_today]
        tk = Counter(t for x in fut for t in (x.get('teacher_ids') or []))
        teacher = managers.get(tk.most_common(1)[0][0], '') if tk else ''
        row = {'cid': cid, 'name': n.replace('_', ' ').strip(), 'polnoe': n, 'dir': dr,
               'cap': c.get('cap') or 0, 'busy': len(u_uch), 'prob': len(u_prob), 'teacher': teacher or '',
               '_uch': u_uch, '_prob': u_prob, '_byl': u_byl}
        row['left'] = row['cap'] - row['busy'] - row['prob']
        if dr in ('Логопед', 'Прочее'):
            row['why'] = ('логопед — индивидуальные занятия' if dr == 'Логопед'
                          else 'не учебная группа сезона')
            vne.append(row)
            continue
        why = zakryta(n)
        if not why and c.get('status') not in (None, '', 'opened'):
            why = 'закрыта в МойКлассе'          # статус группы closed: набор закрыт в самой CRM
        if not fut and not why:
            why = NET_ZANYATIY
        if why:
            row['why'] = why
            closed_rows.append(row)
            continue
        if dr in PO_DNYAM:
            # по дням: ребёнок «Учится» считается в день недели, если у него есть запись в этот день
            # на прошлой или на следующей неделе; пробник — если записан на этот день вперёд
            days = {w: {'busy': set(), 'prob': set()} for w in BUDNI}
            w0, w1 = (today - dt.timedelta(days=7)).isoformat(), (today + dt.timedelta(days=6)).isoformat()
            for x in les.get(cid, []):
                w = WD[dt.date.fromisoformat(x['date']).weekday()]
                if w not in days or not (w0 <= x['date'] <= w1):
                    continue
                for r in x.get('records') or []:
                    if not r:
                        continue
                    v = _vid(deti.get(r.get('user_id'), set()))
                    if v == 'uch' and not r.get('test'):
                        days[w]['busy'].add(r['user_id'])
                    elif (v == 'prob' or r.get('test')) and x['date'] >= s_today:
                        days[w]['prob'].add(r['user_id'])
            row['days'] = {}
            for w, v in days.items():
                cap = MINISAD_CAP[w] if dr == 'Мини-сад' else row['cap']
                b, p = len(v['busy']), len(v['prob'] - v['busy'])
                row['days'][w] = {'cap': cap, 'busy': b, 'prob': p, 'left': cap - b - p}
        groups.append(row)

    reg = [g for g in groups if 'days' not in g]
    tot = {k: sum(g[k] for g in reg) for k in ('cap', 'busy', 'prob')}
    tot['left'] = tot['cap'] - tot['busy'] - tot['prob']
    tot['free'] = sum(max(0, g['left']) for g in reg)
    tot['over'] = sum(max(0, -g['left']) for g in reg)
    return {'groups': groups, 'closed': closed_rows, 'vne': vne, 'tot': tot,
            'days': {g['dir']: g['days'] for g in groups if 'days' in g},
            'pravilo': PRAVILO}


def svobodno_po_dnyam(days: dict) -> str:
    """«пн 0 · вт 5 · ср 1 · чт 2 · пт 1» — сколько мест остаётся в каждый день."""
    return ' · '.join(f"{w} {max(0, v['left'])}" for w, v in days.items())
