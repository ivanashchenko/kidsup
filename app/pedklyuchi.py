# -*- coding: utf-8 -*-
"""Ключи педагогов: личные ссылки и общий ключ.

13.11.2026, Борис — «М, П, Т — по рекомендациям»:
  Т1. «Сменить общий ключ: ссылка с ним много раз пересылалась в чатах» — да, вместе с Т3.
  Т3. «Личные ключи: каждому педагогу своя ссылка, видит только своих детей. Сейчас
      пересланная ссылка показывает всех детей групп английского» — да.

Педагоги входят без пароля, по ссылке с ключом ?k=…: «Карточка речи» (/english/karta) и
«После первого занятия» (/pedagog). До 14.11.2026 ключ был один на всех — настройка
pedagog_key. Теперь ключ бывает двух видов, и кто пришёл, решает одна функция proverit():
  • личный — выдан одному педагогу; сервер знает, кто это, и показывает на «Карточке речи»
    только детей его групп (app/karta.py, pedagogi_grupp: по преподавателю в занятиях МойКласса);
  • общий — как раньше: один на всех, видит всех.

Пока владелец ничего не сделал, общий ключ — прежний, из настройки pedagog_key, и для
педагогов не меняется ничего: ссылка из чата открывается как открывалась. Новое включается
его действиями в блоке «Ключи педагогов» на /owner (blok_vladelca, deystvie_vladelca):
выдать личный ключ, выдать заново, отключить; сменить общий ключ, выключить, включить снова.

Хранение — по образцу личных входов (app/vhod.py) и в том же файле data/vhody.db, таблица
pedagog_klyuchi: кому выдан (имя педагога, как в списке страницы /pedagog, и id сотрудника
в МойКлассе), SHA-256 ключа, когда выдан, когда последний раз открывал, включён ли. Сам ключ
не хранится нигде: ссылка показывается один раз — в ответе на запрос выдачи. Ключ — 40
случайных знаков из латинских букв и цифр. В копию базы (/api/backup/db) таблица не входит;
в копию файла входов рядом (vhody.kopiya.db) — входит.

Общий ключ после смены — такая же строка таблицы (vid='obshchiy', только хеш); прежний
ключ из настройки pedagog_key при этом стирается, а отметка «прежний снят» пишется в два
места — строкой таблицы и настройкой pedagog_obshchiy_snyat основной базы: основная база,
восстановленная из старой копии, или пропавший файл входов не оживят прежний ключ.

Перебор — общий счётчик vhod.schet_sekreta (те же правила, что у серого окна): пять разных
неверных ключей с одного адреса за час — пауза; в паузу верный ключ, которого сервер с
этого адреса ещё не видел, получает тот же отказ, что и неверный. Устаревшая ссылка
(прежний ключ педагога, отключённый ключ, прежний общий) перебором не считается: её
откроют из старого сообщения ещё не раз, и запирать из-за этого Wi-Fi центра нельзя.

Ключ и ссылка не пишутся ни в журнал, ни в лог, ни в настройки.
"""
from __future__ import annotations

import json
import logging
import secrets
import sqlite3
import string
import threading

from . import config, db, vhod

log = logging.getLogger("kidsup.pedklyuchi")

OBSHCHIY, LICHNYY = "obshchiy", "lichnyy"
SPOSOB = "pedklyuch"                       # request.state.kto["sposob"] у педагога с личным ключом
DLINA = 40                                 # знаков в ключе (не короче 32)
ALFAVIT = string.ascii_letters + string.digits
SCHET = vhod.OKNO + "pedagog"              # счётчик неверных ключей — отдельно от формы и окна
SNYAT = "pedagog_obshchiy_snyat"           # настройка основной базы: прежний общий ключ снят (когда)
SHAG_POSL = 300                            # «последний раз открывал» пишется не чаще раза в 5 минут
STRANICY = ("/pedagog", "/english/karta")  # открытие этих страниц общим ключом идёт в счёт «за 7 дней»
TREVOGA = ("app.kidsup.ru: за час {n} неверных ключей педагогов с разных адресов. Страницы педагогов не "
           "закрыты. Если это не педагоги со старыми ссылками — смените общий ключ и выдайте личные заново: "
           "/owner, блок «Ключи педагогов».")


# --- таблица -----------------------------------------------------------------------------------

def _stroki() -> list:
    with vhod._baza() as c:
        return [dict(r) for r in c.execute("SELECT * FROM pedagog_klyuchi ORDER BY id")]


def _obshchaya(rows) -> dict | None:
    return next((r for r in rows if r["vid"] == OBSHCHIY), None)


def _snyat() -> str:
    """Отметка в основной базе: прежний общий ключ (настройка pedagog_key) снят владельцем."""
    return vhod._nastroyki(SNYAT).get(SNYAT) or ""


def novyy_klyuch() -> str:
    return "".join(secrets.choice(ALFAVIT) for _ in range(DLINA))


# --- кто пришёл ----------------------------------------------------------------------------------

def _nayti(k: str) -> tuple[dict | None, bool]:
    """→ (кто пришёл или None, ключ устарел: прежний, отключённый или снятый общий)."""
    h = vhod.hesh_klyucha(k)
    try:
        rows = _stroki()
    except Exception:  # noqa: BLE001 — сбой файла входов прежнему общему ключу не мешает
        vhod._zhaloba("pedklyuchi: таблица ключей не прочитана — проверяю только прежний общий ключ")
        rows = None
    naydeno, ustarel = None, False
    for r in rows or ():
        sovpal = bool(r["hesh"]) and vhod._ravny(r["hesh"], h)
        if sovpal and r["vkl"]:
            naydeno = r
        elif sovpal or (bool(r["hesh_prezhniy"]) and vhod._ravny(r["hesh_prezhniy"], h)):
            ustarel = True
    if naydeno is not None:
        return {"vid": naydeno["vid"], "id": naydeno["id"], "imya": naydeno["imya"] or "",
                "mk_id": naydeno["mk_id"], "k": k}, False
    # Прежний общий ключ из настройки pedagog_key — пока владелец его не сменил и не выключил.
    # Снят он, если так говорит хоть одно из двух хранилищ; не читается одно — решает второе.
    try:
        snyat = bool(_snyat())
    except Exception:  # noqa: BLE001
        snyat = False
    if not snyat and (rows is None or _obshchaya(rows) is None):
        want = db.get_setting("pedagog_key", "")
        if want and vhod._ravny(k, want):
            return {"vid": OBSHCHIY, "id": 0, "imya": "", "mk_id": None, "k": k}, False
    return None, ustarel


def proverit(k, request=None) -> dict | None:
    """Ключ из ссылки (?k=, заголовок X-Pedagog-Key, поле k) → кто пришёл, или None.

    {"vid": "lichnyy", "id", "imya", "mk_id", "k"} — личный ключ педагога;
    {"vid": "obshchiy", …} — общий ключ. None — ключ не подошёл: вызывающий отвечает так же,
    как отвечал на неверный ключ всегда (страница «Ссылка недействительна», «ключ не
    подходит», для карточки речи — обычная проверка входа).
    С request считается перебор (vhod.schet_sekreta): в паузу и верный ключ, незнакомый
    серверу с этого адреса, получает None — иначе перебор узнавал бы удачу по ответу."""
    k = str(k or "")
    if not k:
        return None
    kto, ustarel = _nayti(k)
    if request is not None:
        try:
            p = vhod.schet_sekreta(SCHET, vhod.adres(request), k, kto is not None, "ключи педагогов",
                                   schitat=not ustarel, trevoga=TREVOGA)
        except Exception:  # noqa: BLE001 — сбой счётчика ссылку педагогу не закрывает
            vhod._zhaloba("pedklyuchi: счёт перебора не сработал")
            p = 0
        if p > 0:
            return None
    return kto


def _mk_ids(ped: dict) -> set:
    """id сотрудника МойКласса для ключа; если при выдаче id не было — по имени из списка сотрудников."""
    if ped.get("mk_id") is not None:
        return {int(ped["mk_id"])}
    imya = (ped.get("imya") or "").strip()
    if not imya:
        return set()
    with db.get_conn() as c:
        return {r["id"] for r in c.execute("SELECT id, name FROM managers") if (r["name"] or "").strip() == imya}


def gruppy(ped: dict) -> set:
    """Группы «Карточки речи», которые ведёт педагог с личным ключом (считается раз на запрос).
    Сбой — пустое множество: без данных личный ключ не видит никого."""
    if "gruppy" not in ped:
        try:
            from . import karta
            ped["gruppy"] = karta.gruppy_pedagoga(_mk_ids(ped))
        except Exception:  # noqa: BLE001
            vhod._zhaloba("pedklyuchi: группы педагога не определены — личному ключу не показываю никого")
            ped["gruppy"] = set()
    return ped["gruppy"]


def v_zapros(request, ped: dict, admin: bool = False) -> None:
    """Педагог в запросе — по образцу личных входов: request.state.pedagog (кто, каким ключом)
    и, для личного ключа, request.state.kto — тогда слой после ответа пишет изменяющие запросы
    в журнал «кто что сделал» под именем педагога (путь без ключа). admin=True — ту же ссылку
    открыл вошедший админ или владелец: открытием педагога это не считается."""
    ped["ne_schitat"] = bool(admin)
    request.state.pedagog = ped
    if ped.get("vid") == LICHNYY and not admin:
        request.state.kto = vhod._k("", ped.get("imya") or "", "pedagog", SPOSOB)


def lichnyy(request) -> dict | None:
    """Педагог с личным ключом в этом запросе, или None (общий ключ, админ, никто)."""
    ped = getattr(getattr(request, "state", None), "pedagog", None)
    return ped if ped and ped.get("vid") == LICHNYY else None


# --- после ответа: «последний раз открывал» и счёт общего ключа -----------------------------------

_ZAMOK = threading.Lock()
_POSL: dict = {}             # id ключа → когда «последний раз открывал» записан в базу
_ZHDUT: dict = {}            # день → [открытий общим ключом, ещё не записанных; время последнего]
_NACHALO = [False]           # отметка «с какого времени идёт счёт» уже стоит


def _zapisat(zaprosy: list) -> bool:
    """Короткая запись в файл входов мимо vhod._baza(): запертый файл ждём не дольше полсекунды и
    общую отметку «файл заперт» не ставим — из-за справочной отметки вход админов ждать не должен.
    → False — не записано (попробуем на следующем запросе)."""
    try:      # mode=rw: файла нет — не создаём пустой (его готовит и восстанавливает из копии vhod._baza)
        con = sqlite3.connect(vhod._put().resolve().as_uri() + "?mode=rw", uri=True, timeout=0.5)
    except (sqlite3.Error, OSError, ValueError):
        return False
    try:
        for sql, par in zaprosy:
            con.execute(sql, par)
        con.commit()
        return True
    except sqlite3.Error:
        return False
    finally:
        con.close()


def _nachalo() -> list:
    """Запрос «с какого времени идёт счёт открытий» — ставится один раз, первым же запросом педагога."""
    return [("INSERT OR IGNORE INTO pedagog_vhody (den, vid, n, posl) VALUES ('', 'nachalo', 0, ?)", (vhod._iso(),))]


def sloy_zapis(request, kod: int) -> None:
    """После ответа, в потоке (main._vhod_sloy). Личный ключ: «последний раз открывал» — не чаще
    раза в 5 минут на ключ. Общий ключ: +1 к открытиям страниц за день (только GET страниц
    педагога, не запросы со страницы). Запись не удалась — счёт остаётся в памяти до следующей."""
    ped = getattr(request.state, "pedagog", None)
    if not ped or ped.get("ne_schitat") or not 200 <= kod < 400:
        return
    t = vhod.seychas()
    zaprosy = []
    with _ZAMOK:
        if not _NACHALO[0]:
            zaprosy += _nachalo()
        if ped.get("vid") == LICHNYY:
            pisat = t - _POSL.get(ped["id"], -1e18) >= SHAG_POSL
            if pisat:
                _POSL[ped["id"]] = t
                zaprosy.append(("UPDATE pedagog_klyuchi SET posl=? WHERE id=?", (vhod._iso(t), ped["id"])))
        elif request.method.upper() == "GET" and request.url.path.rstrip("/") in STRANICY:
            e = _ZHDUT.setdefault(vhod._iso(t)[:10], [0, ""])
            e[0], e[1] = e[0] + 1, vhod._iso(t)
        zhdut = {d: list(v) for d, v in _ZHDUT.items()}
        _ZHDUT.clear()
    for den, (n, posl) in zhdut.items():
        zaprosy.append(("INSERT INTO pedagog_vhody (den, vid, n, posl) VALUES (?,?,?,?) "
                        "ON CONFLICT(den, vid) DO UPDATE SET n = n + excluded.n, posl = excluded.posl",
                        (den, OBSHCHIY, n, posl)))
    if not zaprosy:
        return
    if _zapisat(zaprosy):
        _NACHALO[0] = True
        return
    with _ZAMOK:                       # файл занят: вернуть несчитанное, отметку «открывал» повторить позже
        for den, (n, posl) in zhdut.items():
            e = _ZHDUT.setdefault(den, [0, ""])
            e[0], e[1] = e[0] + n, max(e[1], posl)
        if ped.get("vid") == LICHNYY:
            _POSL.pop(ped["id"], None)


# --- управление (зовёт только обработчик владельца) -----------------------------------------------

def pedagogi_spisok() -> list:
    """Педагоги, которым можно выдать ключ: [{"mk_id", "imya"}] — те же, кого показывает список
    страницы /pedagog (сотрудники МойКласса с ролью «педагог», работающие), только из
    локальной выгрузки, без запроса в МойКласс. Плюс те, кто по занятиям ведёт группы
    английского, даже если роль у них не отмечена."""
    from . import karta, karta_razvitiya
    out, imena = {}, {}
    with db.get_conn() as c:
        for r in c.execute("SELECT id, name, raw FROM managers"):
            imena[r["id"]] = (r["name"] or "").strip()
            try:
                raw = json.loads(r["raw"] or "{}")
            except ValueError:
                raw = {}
            if karta_razvitiya.eto_pedagog(raw) and imena[r["id"]]:
                out[r["id"]] = imena[r["id"]]
        vedut = set().union(*karta.pedagogi_grupp(c).values())
    for mk_id in vedut:
        out.setdefault(mk_id, imena.get(mk_id) or f"сотрудник МойКласса №{mk_id}")
    return [{"mk_id": i, "imya": n} for i, n in sorted(out.items(), key=lambda x: (x[1].casefold(), x[0]))]


def _stroka_pedagoga(c, mk_id, imya: str):
    if mk_id is not None:
        return c.execute("SELECT * FROM pedagog_klyuchi WHERE vid=? AND mk_id=?", (LICHNYY, int(mk_id))).fetchone()
    return c.execute("SELECT * FROM pedagog_klyuchi WHERE vid=? AND mk_id IS NULL AND imya=?", (LICHNYY, imya)).fetchone()


def vydat(mk_id, imya: str) -> str:
    """Выдать педагогу личный ключ — или выдать заново: прежний перестаёт работать сразу.
    → ключ (показать один раз; хранится только SHA-256). Отметка «открывал» начинается заново."""
    imya = " ".join((imya or "").split())[:80]
    if not imya:
        raise ValueError("нужно имя педагога")
    klyuch = novyy_klyuch()
    with vhod._baza() as c:
        r = _stroka_pedagoga(c, mk_id, imya)
        if r is None:
            c.execute("INSERT INTO pedagog_klyuchi (vid, imya, mk_id, hesh, vydan, vkl) VALUES (?,?,?,?,?,1)",
                      (LICHNYY, imya, int(mk_id) if mk_id is not None else None, vhod.hesh_klyucha(klyuch), vhod._iso()))
        else:
            c.execute("UPDATE pedagog_klyuchi SET imya=?, hesh=?, hesh_prezhniy=?, vydan=?, posl=NULL, vkl=1 WHERE id=?",
                      (imya, vhod.hesh_klyucha(klyuch), r["hesh"] or r["hesh_prezhniy"], vhod._iso(), r["id"]))
            with _ZAMOK:
                _POSL.pop(r["id"], None)
    vhod._obnovit_kopiyu()
    return klyuch


def otklyuchit(kid: int) -> str:
    """Отключить личный ключ: ссылка перестаёт открываться сразу. → имя педагога или "" (нет такого)."""
    with vhod._baza() as c:
        r = c.execute("SELECT * FROM pedagog_klyuchi WHERE id=? AND vid=?", (int(kid), LICHNYY)).fetchone()
        if r is None:
            return ""
        c.execute("UPDATE pedagog_klyuchi SET vkl=0 WHERE id=?", (r["id"],))
    vhod._obnovit_kopiyu()
    return r["imya"] or "педагог"


def _obshchiy_zapisat(novyy: str | None) -> None:
    """Общий ключ: novyy — новый ключ (сменить, включить снова), None — выключить.
    Прежний ключ из настройки pedagog_key снимается: отметка в основной базе, строка в файле
    входов, само значение стирается. Порядок — от строгого: сначала отметка, потом строка."""
    ts = vhod._iso()
    prezhniy = db.get_setting("pedagog_key", "")
    if not _snyat():
        db.set_setting(SNYAT, ts)
    with vhod._baza() as c:
        r = c.execute("SELECT * FROM pedagog_klyuchi WHERE vid=?", (OBSHCHIY,)).fetchone()
        staryy_hesh = (r["hesh"] or r["hesh_prezhniy"]) if r is not None else (
            vhod.hesh_klyucha(prezhniy) if prezhniy else None)
        if r is None:
            c.execute("INSERT INTO pedagog_klyuchi (vid, imya, hesh, hesh_prezhniy, vydan, vkl) VALUES (?,?,?,?,?,?)",
                      (OBSHCHIY, "", vhod.hesh_klyucha(novyy) if novyy else "", staryy_hesh, ts, 1 if novyy else 0))
        else:
            c.execute("UPDATE pedagog_klyuchi SET hesh=?, hesh_prezhniy=?, vydan=?, vkl=? WHERE id=?",
                      (vhod.hesh_klyucha(novyy) if novyy else "", staryy_hesh, ts, 1 if novyy else 0, r["id"]))
    if prezhniy:
        db.set_setting("pedagog_key", "")
    vhod._obnovit_kopiyu()


def obshchiy_smenit() -> str:
    """«Сменить общий ключ» и «Включить снова»: новый случайный ключ, прежний не работает. → ключ."""
    klyuch = novyy_klyuch()
    _obshchiy_zapisat(klyuch)
    return klyuch


def obshchiy_vyklyuchit() -> None:
    """«Выключить общий ключ»: страницы педагогов открываются только личными ссылками."""
    _obshchiy_zapisat(None)


def ssylka(klyuch: str, put: str = "/pedagog") -> str:
    return f"{config.SVOY_ADRES}{put}?k={klyuch}"


# --- состояние без секретов ------------------------------------------------------------------------

def _korotko(g: str) -> str:
    """«вт-чт_17:00_5-7 лет_A1 Pre-Starter (Гр6)» → «вт-чт 17:00»; «Мини-сад · английский…» → «Мини-сад»."""
    if " · " in g:
        return g.split(" · ")[0]
    ch = g.split("_")
    return " ".join(ch[:2]) if len(ch) >= 2 else g


def sostoyanie() -> dict:
    """Всё о ключах педагогов без ключей и хешей — для блока на /owner и GET /api/pedagogi/sostoyanie:
    общий ключ (прежний, сменён, выключен; сколько открытий за 7 дней), педагоги (кому выдан
    ключ, когда, когда открывал, какие группы английского увидит), группы без педагога."""
    from . import karta
    t = vhod.seychas()
    fayl = "в порядке"
    rows, za_7, posl_obshch, schet_s = [], 0, None, None
    try:
        with vhod._baza() as c:
            rows = [dict(r) for r in c.execute("SELECT * FROM pedagog_klyuchi ORDER BY id")]
            r = c.execute("SELECT COALESCE(SUM(n), 0) AS n, MAX(posl) AS posl FROM pedagog_vhody "
                          "WHERE vid=? AND den>=?", (OBSHCHIY, vhod._iso(t - 6 * 86400)[:10])).fetchone()
            za_7, posl_obshch = int(r["n"] or 0), r["posl"]
            n = c.execute("SELECT posl FROM pedagog_vhody WHERE vid='nachalo'").fetchone()
            if n is None:
                c.execute(*_nachalo()[0])
                n = c.execute("SELECT posl FROM pedagog_vhody WHERE vid='nachalo'").fetchone()
            schet_s = n["posl"] if n is not None else None
        _NACHALO[0] = True
    except Exception:  # noqa: BLE001
        vhod._zhaloba("pedklyuchi: состояние — файл входов не читается")
        fayl = "не читается"
    with _ZAMOK:                                   # открытия, ещё не записанные в файл
        za_7 += sum(v[0] for v in _ZHDUT.values())
        posl_obshch = max([x for x in [posl_obshch] + [v[1] for v in _ZHDUT.values()] if x], default=None)
    ob = _obshchaya(rows)
    try:
        snyat = _snyat()
    except Exception:  # noqa: BLE001
        snyat = ""
    if ob is not None or snyat:
        vkl = bool(ob is not None and ob["vkl"] and ob["hesh"])
        obshchiy = {"sostoyanie": "smenen" if vkl else "vyklyuchen", "rabotaet": vkl,
                    "kogda": (ob["vydan"] if ob is not None else None) or snyat or None}
    else:
        obshchiy = {"sostoyanie": "prezhniy", "rabotaet": bool(db.get_setting("pedagog_key", "")), "kogda": None}
    obshchiy.update({"otkrytiy_za_7_dney": za_7, "posl_otkrytie": posl_obshch, "schet_s": schet_s})

    try:
        gr = karta.gruppy_s_pedagogami()
    except Exception:  # noqa: BLE001
        vhod._zhaloba("pedklyuchi: группы английского не прочитаны")
        gr = []
    try:
        spisok = pedagogi_spisok()
    except Exception:  # noqa: BLE001
        vhod._zhaloba("pedklyuchi: список педагогов не прочитан")
        spisok = []
    lichnye = [r for r in rows if r["vid"] == LICHNYY]
    po_id = {r["mk_id"]: r for r in lichnye if r["mk_id"] is not None}
    pedagogi, v_spiske = [], set()

    def kartochka(mk_id, imya, r, est: bool) -> dict:
        svoi = [g for g in gr if mk_id is not None and mk_id in g["педагоги"]]
        return {"mk_id": mk_id, "imya": imya, "v_spiske": est,
                "gruppy": [_korotko(g["группа"]) for g in svoi], "detey": sum(g["детей"] for g in svoi),
                "klyuch": None if r is None else {
                    "id": r["id"], "vkl": bool(r["vkl"]), "vydan": r["vydan"], "posl": r["posl"]}}

    for p in spisok:
        r = po_id.get(p["mk_id"])
        if r is not None:
            v_spiske.add(r["id"])
        pedagogi.append(kartochka(p["mk_id"], p["imya"], r, True))
    for r in lichnye:                              # ключ выдан, а педагога в списке уже нет (уволен, снята роль)
        if r["id"] not in v_spiske:
            pedagogi.append(kartochka(r["mk_id"], r["imya"], r, False))
    pedagogi.sort(key=lambda p: (not p["gruppy"], not p["v_spiske"], p["imya"].casefold()))
    s_klyuchom = [p for p in pedagogi if p["klyuch"] and p["klyuch"]["vkl"]]
    return {
        "seychas": vhod._iso(t), "fayl_vhodov": fayl, "obshchiy": obshchiy, "pedagogi": pedagogi,
        "s_klyuchom": len(s_klyuchom), "otkryvali": sum(1 for p in s_klyuchom if p["klyuch"]["posl"]),
        "gruppy_bez_pedagoga": [_korotko(g["группа"]) for g in gr if not g["педагоги"]],
        "grupp_angliyskogo": len(gr),
    }


# --- действия владельца ----------------------------------------------------------------------------

def deystvie_vladelca(request, f) -> dict:
    """POST /owner/pedagogi. f — поля формы. Зовёт только обработчик владельца, уже проверивший:
    человек-владелец, вошёл через форму. Каждое действие подтверждается его паролем
    (vhod.podtverdit), как в блоке «Входы». → {"msg"|"osh": текст, "ssylki": [(подпись, ссылка)] —
    показать один раз}. В журнал идёт только название действия и имя педагога."""
    def pole(n):
        return str(f.get(n) or "")
    d, _, arg = pole("deystvie").strip().partition(":")
    request.state.chto = "ключи педагогов: " + (d or "?")[:20] + " — отказ"
    prichina = vhod.podtverdit(request, pole("moy_parol"))
    if prichina:
        return {"osh": prichina}

    def gotovo(chto, msg, **kw):
        request.state.chto = "ключи педагогов: " + chto
        return {"msg": msg, **kw}

    if d == "vydat":
        try:
            mk_id = int(arg)
        except ValueError:
            return {"osh": "Нет такого педагога в списке."}
        p = next((x for x in pedagogi_spisok() if x["mk_id"] == mk_id), None)
        if p is None:
            return {"osh": "Нет такого педагога в списке — ключ не выдан."}
        klyuch = vydat(mk_id, p["imya"])
        ped = {"mk_id": mk_id, "imya": p["imya"]}
        put = "/english/karta" if gruppy(ped) else "/pedagog"
        return gotovo("выдан ключ — " + p["imya"],
                      f"{p['imya']}: личный ключ выдан. Прежняя личная ссылка этого педагога, если была, больше не открывается.",
                      ssylki=[(f"Личная ссылка — {p['imya']}", ssylka(klyuch, put))], komu=p["imya"])

    if d == "otklyuchit":
        try:
            imya = otklyuchit(int(arg))
        except ValueError:
            imya = ""
        if not imya:
            return {"osh": "Нет такого ключа."}
        return gotovo("отключён ключ — " + imya, f"{imya}: личный ключ отключён — ссылка больше не открывается. "
                      "Вернуть доступ — «Выдать заново» (ссылка будет новая).")

    if d in ("obshchiy_smenit", "obshchiy_vkl"):
        klyuch = obshchiy_smenit()
        chto = "общий ключ педагогов сменён" if d == "obshchiy_smenit" else "общий ключ педагогов включён снова (новый)"
        vhod.otmetit_vazhnoe(chto, vhod.imya_v_zhurnal(vhod.iz(request)) or "владелец")
        return gotovo(chto, "Общий ключ сменён: прежние общие ссылки больше не открываются." if d == "obshchiy_smenit"
                      else "Общий ключ снова включён — он новый, прежние общие ссылки не открываются.",
                      ssylki=[("Общая ссылка — «После первого занятия»", ssylka(klyuch, "/pedagog")),
                              ("Общая ссылка — «Карточка речи»", ssylka(klyuch, "/english/karta"))], komu="")

    if d == "obshchiy_vykl":
        obshchiy_vyklyuchit()
        vhod.otmetit_vazhnoe("общий ключ педагогов выключен", vhod.imya_v_zhurnal(vhod.iz(request)) or "владелец")
        return gotovo("общий ключ педагогов выключен",
                      "Общий ключ выключен: страницы педагогов открываются только личными ссылками. "
                      "Вернуть — «Включить снова» (ключ будет новый).")

    return {"osh": "Неизвестное действие — ничего не изменено."}


# --- блок «Ключи педагогов» на /owner --------------------------------------------------------------

SKRIPT = """
(function(){
  var f=document.getElementById('ped-forma'); if(!f)return;
  f.addEventListener('keydown',function(e){if(e.key==='Enter'&&e.target.tagName==='INPUT')e.preventDefault();});
  var sp=f.querySelectorAll('button[data-sprosit]'),i;
  for(i=0;i<sp.length;i++)sp[i].addEventListener('click',function(e){if(!confirm(this.getAttribute('data-sprosit')))e.preventDefault()});
  f.addEventListener('submit',function(e){var m=f.elements['moy_parol'];
    if(m&&!m.value){e.preventDefault();m.focus();m.scrollIntoView({block:'center'});
      var t=document.getElementById('ped-nado');if(t)t.hidden=false}});
  var kop=document.querySelectorAll('button.ped-kop');
  for(i=0;i<kop.length;i++)kop[i].onclick=function(){var b=this,k=b.parentNode.querySelector('input');k.select();
    function ok(){b.textContent='Скопировано'}
    if(navigator.clipboard&&navigator.clipboard.writeText)navigator.clipboard.writeText(k.value).then(ok,function(){document.execCommand('copy');ok()});
    else{document.execCommand('copy');ok()}};
  var it=document.getElementById('ped-itog'); if(it)it.scrollIntoView({block:'center'});
})();
"""


def _mnogo(n: int, odin: str, dva: str, pyat: str) -> str:
    """1 раз, 2 раза, 5 раз."""
    if n % 10 == 1 and n % 100 != 11:
        return odin
    return dva if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else pyat


def blok_vladelca(request, itog: dict | None = None) -> str:
    """HTML блока «Ключи педагогов» для /owner. Виден только человеку-владельцу, вошедшему через
    форму (те же правила, что у блока «Входы»); остальным — ссылка «войти через форму».
    Ключей и хешей в странице нет; только что выданная ссылка показывается один раз — в
    ответе на сам запрос выдачи (itog["ssylki"])."""
    e = vhod._e
    kogda = vhod.kogda
    k = vhod.iz(request)
    if not (k.get("rol") == "owner" and k.get("sposob") == "forma"):
        return ("<section class='vh' id='pedagogi'><h2>Ключи педагогов</h2><p><a href='/vhod?next=/owner'><b>Управлять "
                "ключами педагогов → войти через форму</b></a></p><p class='tih'>Личные ссылки педагогам, смена и "
                "выключение общего ключа открываются только после входа под своим именем на странице входа.</p></section>")
    itog = itog or {}
    s = sostoyanie()
    ob = s["obshchiy"]
    h = ["<section class='vh' id='pedagogi'><h2>Ключи педагогов</h2>"
         "<p class='tih'>Педагоги открывают «Карточку речи» и «После первого занятия» без пароля, по ссылке с ключом. "
         "Личная ссылка — одному педагогу: на «Карточке речи» он видит только детей своих групп, а в записях видно, "
         "кто что отметил. Общая ссылка — одна на всех, показывает всех детей групп английского.</p>"]
    if itog.get("osh"):
        h.append(f"<div class='osh' role='alert' id='ped-itog'>{e(itog['osh'])}</div>")
    if itog.get("msg"):
        h.append(f"<div class='ok' id='ped-itog'>{e(itog['msg'])}</div>")
    if itog.get("ssylki"):
        h.append("<div class='kl'><b>Ссылка показывается один раз</b>")
        for podpis, url in itog["ssylki"]:
            h.append(f"<p style='margin:8px 0 2px'>{e(podpis)}</p><div class='ryad'>"
                     f"<input type='text' class='ped-ssylka' readonly value='{e(url)}'>"
                     "<button type='button' class='ped-kop'>Скопировать</button></div>")
        h.append("<p>" + ("Отправьте её этому педагогу лично — не в общий чат: по ней видны его дети. "
                          if itog.get("komu") else "Её можно отправить в чат педагогов, как прежнюю. ")
                 + "После закрытия этой страницы ссылку не увидеть — только выдать новую.</p></div>")
    h.append("<form method='post' action='/owner/pedagogi' id='ped-forma' autocomplete='off'>"
             "<input type='text' name='username' value='boris' autocomplete='username' hidden>"
             "<div class='moy'><b>Ваш нынешний пароль</b><input type='password' name='moy_parol' "
             "autocomplete='current-password'><p class='tih'>Нужен для любого действия в этом блоке: введите и нажмите "
             "нужную кнопку.</p><p class='osh' id='ped-nado' hidden>Сначала введите ваш нынешний пароль.</p></div>")
    if s["fayl_vhodov"] != "в порядке":
        h.append("<p class='osh'>Файл входов сейчас не читается — список ключей ниже может быть неполным.</p>")

    # --- общий ключ
    h.append("<h3>Общий ключ</h3>")
    if ob["sostoyanie"] == "prezhniy":
        h.append("<p>" + ("Работает прежний — тот, что в ссылках из чатов. Любой, у кого есть такая ссылка, видит всех "
                          "детей групп английского." if ob["rabotaet"] else "Не задан: общей ссылки нет.") + "</p>")
    elif ob["rabotaet"]:
        h.append(f"<p>Сменён {e(kogda(ob['kogda']))}: работает новый общий ключ, прежние общие ссылки не открываются.</p>")
    else:
        h.append(f"<p><b>Выключен</b> {e(kogda(ob['kogda']))}: страницы педагогов открываются только личными ссылками.</p>")
    n7 = ob["otkrytiy_za_7_dney"]
    stroka = (f"Личным ключом страницы уже открывали: <b>{s['otkryvali']} из {s['s_klyuchom']}</b> "
              f"{_mnogo(s['s_klyuchom'], 'педагога', 'педагогов', 'педагогов')} с ключом. "
              f"По общему ключу за 7 дней: <b>{n7} {_mnogo(n7, 'открытие', 'открытия', 'открытий')}</b>")
    if ob["posl_otkrytie"]:
        stroka += f", последнее — {e(kogda(ob['posl_otkrytie']))}"
    stroka += "."
    if ob["schet_s"]:
        stroka += f" <span class='tih'>Счёт открытий идёт с {e(kogda(ob['schet_s']))}.</span>"
    h.append(f"<p id='ped-sostoyanie'>{stroka}</p>")
    if ob["rabotaet"]:
        h.append("<div class='vb'><button type='submit' name='deystvie' value='obshchiy_smenit' data-sprosit='Сменить общий "
                 "ключ? Прежняя общая ссылка сразу перестанет открываться у всех педагогов — новую нужно будет разослать.'>"
                 "Сменить общий ключ</button>"
                 "<button type='submit' class='op' name='deystvie' value='obshchiy_vykl' data-sprosit='Выключить общий ключ? "
                 f"Страницы откроются только личными ссылками. Личным ключом уже открывали {s['otkryvali']} из "
                 f"{s['s_klyuchom']}; по общему за 7 дней — {n7}.'>Выключить общий ключ</button></div>"
                 "<p class='tih'>«Сменить» — новый общий ключ, ссылка показывается один раз. «Выключить» — когда личные "
                 "ссылки розданы и ими уже открывают; вернуть можно кнопкой «Включить снова».</p>")
    else:
        h.append("<div class='vb'><button type='submit' name='deystvie' value='obshchiy_vkl' data-sprosit='Включить общий "
                 "ключ снова? Будет выдан новый ключ: по общей ссылке видны все дети групп английского.'>"
                 + ("Включить снова" if ob["sostoyanie"] == "vyklyuchen" else "Выдать общий ключ") + "</button></div>"
                 "<p class='tih'>Ключ будет новый; ссылка показывается один раз.</p>")

    # --- личные ключи
    h.append("<h3>Личные ключи</h3>")
    bez = s["gruppy_bez_pedagoga"]
    if bez:
        n = len(bez)
        h.append(f"<div class='pr' id='ped-bez'><b>У {n} {_mnogo(n, 'группы', 'групп', 'групп')} английского не определён "
                 f"педагог:</b> {e(', '.join(bez))}. В занятиях этих групп в МойКлассе не указан преподаватель "
                 "(смотрим 4 недели назад и вперёд). Личным ключам такие группы не показываются; по общему ключу и "
                 "админам они видны. Исправить — указать преподавателя в занятиях группы в МойКлассе.</div>")
    if not s["pedagogi"]:
        h.append("<p>Список педагогов пуст: в локальной выгрузке МойКласса нет сотрудников с ролью «педагог». "
                 "Нажмите «Синхронизировать» и откройте страницу снова.</p>")
    for p in s["pedagogi"]:
        kl = p["klyuch"]
        zag = f"<b>{e(p['imya'])}</b>"
        if not p["v_spiske"]:
            zag += " · нет в списке педагогов МойКласса"
        if p["gruppy"]:
            n = p["detey"]
            gr = (f"«Карточка речи»: {e(', '.join(p['gruppy']))} — {n} "
                  f"{_mnogo(n, 'ребёнок', 'ребёнка', 'детей')}; и «После первого занятия»")
        else:
            gr = "групп английского нет — по личной ссылке только «После первого занятия»"
        if kl is None:
            st = "ключ не выдан"
        elif not kl["vkl"]:
            st = "<b>ключ отключён</b> — ссылка не открывается"
        else:
            st = "ключ выдан " + e(kogda(kl["vydan"]))
            st += (" · последний раз открывал: " + e(kogda(kl["posl"]))) if kl["posl"] else " · ещё не открывал"
        kn = []
        if p["v_spiske"] and p["mk_id"] is not None:
            if kl is None:
                kn.append(f"<button type='submit' name='deystvie' value='vydat:{int(p['mk_id'])}'>Выдать ключ</button>")
            else:
                kn.append(f"<button type='submit' name='deystvie' value='vydat:{int(p['mk_id'])}' data-sprosit='Выдать "
                          f"заново: {e(p['imya'])}? Прежняя личная ссылка сразу перестанет открываться.'>Выдать заново</button>")
        if kl is not None and kl["vkl"]:
            kn.append(f"<button type='submit' class='op' name='deystvie' value='otklyuchit:{int(kl['id'])}' data-sprosit='"
                      f"Отключить ключ: {e(p['imya'])}? Личная ссылка перестанет открываться.'>Отключить</button>")
        h.append(f"<div class='vk{'' if kl is None or kl['vkl'] else ' net'}'><div class='vz'>{zag}</div>"
                 f"<div class='vs'>{st} · {gr}</div>" + (f"<div class='vb'>{''.join(kn)}</div>" if kn else "") + "</div>")
    h.append("<p class='tih'>Ссылка показывается один раз, в момент выдачи; сервер хранит только её отпечаток. "
             "Потерялась — «Выдать заново»: старая перестанет работать. Группы педагога берутся из занятий МойКласса: "
             "кто стоит преподавателем не меньше чем в четверти занятий группы за 4 недели назад и вперёд.</p>")
    h.append("</form></section>")
    return "".join(h)


def blok_vladelca_bezopasno(request, itog: dict | None = None) -> str:
    """Блок «Ключи педагогов» или, при сбое, короткая строка: остальная страница владельца должна открыться."""
    try:
        return blok_vladelca(request, itog)
    except Exception:  # noqa: BLE001
        vhod._zhaloba("pedklyuchi: блок «Ключи педагогов» не собран")
        return ("<section class='vh' id='pedagogi'><h2>Ключи педагогов</h2><p class='osh'>Блок «Ключи педагогов» не "
                "открылся — ошибка записана в журнал сервера. Остальные настройки работают.</p></section>")
