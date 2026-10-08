# -*- coding: utf-8 -*-
"""Кто записывается на пробное в сезоне 2026/27: откуда пришли семьи, дошедшие до пробного
с 24.08 по 07.10, и сколько таких контактов осталось в каждом пуле. python3 -I kogorty.py <oct4> <features.json>"""
import json, sys, collections, datetime as dt
SRC, FEAT = sys.argv[1], sys.argv[2]
L = lambda n: json.load(open(f"{SRC}/{n}.json"))
lessons = {l["id"]: l for l in L("lessons")}
classes = {c["id"]: c for c in L("classes")}
feats = {f["id"]: f for f in json.load(open(FEAT))}
pays = collections.defaultdict(list)
for p in L("payments"):
    if p.get("optype") == "income" and (p.get("summa") or 0) > 0: pays[p["userId"]].append(p["date"])
first_trial = {}
for r in L("lesson_records"):
    if not r.get("test"): continue
    l = lessons.get(r["lessonId"]) or {}
    d = l.get("date") or (l.get("beginTime") or "")[:10]
    if not d or d < "2026-08-24" or d > "2026-10-07": continue
    if r["userId"] not in first_trial or d < first_trial[r["userId"]][0]:
        first_trial[r["userId"]] = (d, bool(r.get("visit")), (classes.get(l.get("classId")) or {}).get("name", ""))
def pool(uid, ref):
    f = feats.get(uid) or {}
    created = f.get("created") or "2000-01-01"
    ps = [d for d in pays.get(uid, []) if d < "2026-08-01"]
    age_days = (dt.date.fromisoformat(ref) - dt.date.fromisoformat(created)).days
    if age_days <= 30: return "A новая заявка (карточка ≤30 дн. до пробного)"
    if any(d >= "2025-08-01" for d in ps): return "B клиент 2025/26 (платили с 08.2025)"
    if ps: return "C бывший клиент до 2025/26 (база 2024/25 и старше)"
    return "D старый контакт без оплат"
cnt = collections.Counter(); vis = collections.Counter()
for uid, (d, v, c) in first_trial.items():
    k = pool(uid, d); cnt[k] += 1; vis[k] += v
print("Первые пробные 24.08–07.10:", len(first_trial))
for k in sorted(cnt): print(f"  {k}: {cnt[k]} (пришли {vis[k]})")
# пулы сейчас: кого можно звонить (не клиент с абонементом, не отказ/переехали/не писать)
bad = {"переехали", "не писать"}
now = collections.Counter()
for f in feats.values():
    if not f.get("phone") or f["state"] in bad or f["active_sub_oct"]: continue
    if any(j["status"] in ("учится", "подтвердил") for j in f["season_joins"]): continue
    k = pool(f["id"], "2026-10-08")
    if f["state"] == "отказ": k += " · статус «отказ»"
    now[k] += 1
print("Пулы для обзвона на 08.10 (с телефоном, без абонемента и вступления «учится»):")
for k in sorted(now): print(f"  {k}: {now[k]}")
# конверсия в пробное по пулам за сезон: сколько контактов пула (на 24.08) дошло до пробного
print("--- Конверсия в пробное по пулам (сезон 24.08–07.10)")
# A: карточки, созданные 25.07–07.10 (новые заявки), — доля дошедших до пробного
newc = [f for f in feats.values() if "2026-07-25" <= (f.get("created") or "") <= "2026-10-07"]
a_tr = sum(1 for f in newc if f["id"] in first_trial)
print(f"  A новые карточки 25.07–07.10: {len(newc)}, из них пробное {a_tr} — {a_tr/len(newc):.0%}")
# по возрасту заявки: первые 7 дней / позже — сколько пробных записано через N дней после создания
lag = collections.Counter()
for f in newc:
    if f["id"] in first_trial:
        d = (dt.date.fromisoformat(first_trial[f["id"]][0]) - dt.date.fromisoformat(f["created"])).days
        lag["0–3 дн." if d <= 3 else "4–7 дн." if d <= 7 else "8–14 дн." if d <= 14 else "15–30 дн." if d <= 30 else ">30 дн."] += 1
print("    пробное через (дней от заявки):", dict(lag))
# B / C / D — пулы на 24.08 по карточкам, созданным до 25.07
old = [f for f in feats.values() if (f.get("created") or "") < "2026-07-25"]
def grp(f):
    ps = [d for d in pays.get(f["id"], []) if d < "2026-08-01"]
    if any(d >= "2025-08-01" for d in ps): return "B"
    if ps: return "C"
    return "D"
tot = collections.Counter(grp(f) for f in old); tr = collections.Counter(grp(f) for f in old if f["id"] in first_trial)
for g in "BCD": print(f"  {g}: в пуле {tot[g]}, пробное {tr[g]} — {tr[g]/max(1,tot[g]):.1%}")
# B подробнее: клиенты 2025/26, не продлившие сезон, — сколько из них пришли на пробное
b_nosub = [f for f in old if grp(f) == "B" and not f["active_sub_oct"] and not any(j["status"] == "учится" for j in f["season_joins"])]
print(f"  B без абонемента и без «учится» сейчас: {len(b_nosub)}; из них были на пробном в сезоне {sum(1 for f in b_nosub if f['id'] in first_trial)}")
# D подробнее: старые контакты — по году создания
dy = collections.Counter((f["created"] or "")[:4] for f in old if grp(f) == "D"); dt_ = collections.Counter((f["created"] or "")[:4] for f in old if grp(f) == "D" and f["id"] in first_trial)
print("  D по году карточки (в пуле / пробное):", {y: (dy[y], dt_[y]) for y in sorted(dy)})
