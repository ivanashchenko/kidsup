# -*- coding: utf-8 -*-
"""Чистка имён карточек (поручение владельца 05.09): в имени остаются только
фамилия и имя. Всё остальное — в комментарий (исходник целиком), канал общения
и скидка — в теги. Возраст из имени — в комментарий (дата рождения не угадывается).
Телефон вместо имени и «Звонок от …» не трогаем: настоящего имени мы не знаем.
Перед запуском снята копия всех карточек: docs/rabota/backup/users_2026-09-05.json.gz."""
import sys, re, json, time, logging
sys.path.insert(0, "/home/user/kidsup")
from app import sync
from app.moyklass_client import MoyklassClient
logging.basicConfig(level=logging.INFO, format="%(message)s")
TAG = {"whatsapp": 117413, "ватсап": 117413, "вотсап": 117413, "wa": 117413, "telegram": 117414, "телеграм": 117414, "телеграмм": 117414, "тг": 117414, "tg": 117414, "max": 117415, "макс": 117415, "мах": 117415}
TAG_DISCOUNT = 117347  # проверяется ниже по имени тега
PAREN = re.compile(r"\s*\(([^)]*)\)"); AGE = re.compile(r"\s+(\d{1,2}(?:[.,]\d{1,2})?)\s*(лет|года?|г\.?|мес\.?)?\s*$", re.I)
PHONE = re.compile(r"^\+?\d[\d\s\-]{8,}$"); SERVICE = re.compile(r"^(Заявка|Звонок от|Новый контакт|Лид)\b", re.I)
mk = MoyklassClient(sync.get_api_key())
tags_all = mk.get("/v1/company/userTags"); tags_all = tags_all if isinstance(tags_all, list) else tags_all.get("tags", [])
disc = [t["id"] for t in tags_all if "скидк" in t["name"].lower()]; nocall = [t["id"] for t in tags_all if "не звонить" in t["name"].lower() or "не писать" in t["name"].lower()]
TAG_DISCOUNT = disc[0] if disc else None; TAG_NOCALL = nocall[0] if nocall else None
logging.info("тег скидки %s, тег не звонить %s", TAG_DISCOUNT, TAG_NOCALL)
junk = json.load(open("/home/user/kidsup/docs/rabota/imena_junk_0509.json"))
done_ids = set()
try: done_ids = set(json.load(open("/home/user/kidsup/docs/rabota/imena_done_0509.json")))
except Exception: pass
stat = {"переименовано": 0, "пропущено": 0, "ошибок": 0, "тегов": 0}
for uid, name, reasons, state in junk:
    if uid in done_ids: continue
    nm = name.strip()
    if PHONE.match(nm.replace(" ", "")) or SERVICE.match(nm):
        stat["пропущено"] += 1; continue
    extras = []
    for m in PAREN.finditer(nm): extras.append(m.group(1).strip())
    clean = PAREN.sub("", nm).strip()
    m = AGE.search(clean)
    if m:
        extras.append("возраст в имени: " + m.group(0).strip()); clean = clean[:m.start()].strip()
    clean = re.sub(r"[!?:;/]+", " ", clean); clean = re.sub(r"\s{2,}", " ", clean).strip(" -,.")
    words = clean.split()
    if len(words) > 3 or not words or not re.search(r"[А-Яа-яA-Za-zЁё]", clean):
        stat["пропущено"] += 1; logging.info("пропуск (вручную): %s", nm); continue
    if clean == nm:
        stat["пропущено"] += 1; continue
    try:
        u = mk.get(f"/v1/company/users/{uid}")
        cur = [t["id"] for t in (u.get("tags") or []) if isinstance(t, dict)]
        want = []
        low = " ".join(extras).lower()
        for k, tid in TAG.items():
            if re.search(r"\b" + re.escape(k) + r"\b", low): want.append(tid)
        if "скидк" in low and TAG_DISCOUNT: want.append(TAG_DISCOUNT)
        if ("не звонить" in low or "не писать" in low) and TAG_NOCALL: want.append(TAG_NOCALL)
        add = [w for w in dict.fromkeys(want) if w not in cur]
        mk.post("/v1/company/userComments", {"userId": uid, "showToUser": False,
                "comment": (f"Клод, чистка имени 05.09: было «{nm}», стало «{clean}». Вынесено из имени: {'; '.join(extras) or '—'}.")[:1000]})
        if add:
            mk.post(f"/v1/company/users/{uid}/tags", {"tags": cur + add}); stat["тегов"] += len(add)
        mk.safe_update_user(uid, name=clean)
        stat["переименовано"] += 1; done_ids.add(uid)
        logging.info("%s → %s", nm, clean)
    except Exception as e:
        stat["ошибок"] += 1; logging.warning("uid=%s не почищен: %s", uid, str(e)[:120])
    time.sleep(0.35)
    if stat["переименовано"] % 25 == 0:
        json.dump(sorted(done_ids), open("/home/user/kidsup/docs/rabota/imena_done_0509.json", "w"))
json.dump(sorted(done_ids), open("/home/user/kidsup/docs/rabota/imena_done_0509.json", "w"))
logging.info("ИТОГ %s", stat)
mk.close()
