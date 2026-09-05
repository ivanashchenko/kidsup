# -*- coding: utf-8 -*-
"""Теги канала общения по входящим сообщениям с 01.08 (поручение владельца 05.09).
Источник — /api/wazzup/channels-map сервера (wazzup_inbox). Тег ставится всем
карточкам на номере через точечный POST /users/{id}/tags."""
import sys, json, time, logging
sys.path.insert(0, "/home/user/kidsup")
from app import sync, db
from app.moyklass_client import MoyklassClient
logging.basicConfig(level=logging.INFO, format="%(message)s")
TAG = {"whatsapp": 117413, "telegram": 117414, "telegroup": 117414, "max": 117415}
rows = json.load(open("/home/user/kidsup/docs/rabota/channels_map_0509.json"))
by_phone = {}
for r in rows:
    t = TAG.get(r["chat_type"])
    if t: by_phone.setdefault(r["phone"][-10:], set()).add(t)
mk = MoyklassClient(sync.get_api_key())
stat = {"номеров": len(by_phone), "карточек": 0, "тегов добавлено": 0, "без карточки": 0, "ошибок": 0}
nocard = []
for ph, tags in by_phone.items():
    ids = set()
    try:
        with db.get_conn() as conn:
            ids |= {r[0] for r in conn.execute("SELECT id FROM users WHERE phone LIKE ?", ("%" + ph,))}
        for q in (ph, "7" + ph):
            r = mk.get("/v1/company/users", {"phone": q, "limit": 20}); time.sleep(0.25)
            ids |= {u["id"] for u in ((r.get("users") if isinstance(r, dict) else r) or [])}
    except Exception as e:
        stat["ошибок"] += 1; continue
    if not ids:
        stat["без карточки"] += 1; nocard.append(ph); continue
    for uid in ids:
        try:
            u = mk.get(f"/v1/company/users/{uid}"); time.sleep(0.25)
            cur = [t["id"] if isinstance(t, dict) else t for t in (u.get("tags") or [])]
            add = [t for t in tags if t not in cur]
            stat["карточек"] += 1
            if add:
                mk.post(f"/v1/company/users/{uid}/tags", {"tags": sorted(set(cur) | set(add))}); time.sleep(0.25)
                stat["тегов добавлено"] += len(add); logging.info("%s %s +%s", uid, u.get("name"), add)
        except Exception as e:
            stat["ошибок"] += 1; logging.warning("uid=%s: %s", uid, str(e)[:100])
json.dump({"stat": stat, "без карточки": nocard}, open("/home/user/kidsup/docs/rabota/tegi_kanalov_0509.json", "w"), ensure_ascii=False, indent=1)
logging.info("ИТОГ %s", stat)
mk.close()
