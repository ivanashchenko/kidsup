"""CLI интеграции с hh.ru: python3 -m hh <команда>."""

import argparse
import json
import pathlib
import sys
import uuid

from . import analyze as analyze_mod
from . import auth
from .client import HHError


def _employer_id(client, explicit=None):
    if explicit:
        return explicit
    me = client.me()
    employer = (me.get("employer") or {}).get("id")
    if not employer:
        raise SystemExit(
            "У текущего токена нет аккаунта работодателя. "
            "Войдите менеджером компании: python3 -m hh login"
        )
    return employer


def _fetch_responses(client, vacancy_id, limit, only_new):
    items, page = [], 0
    while len(items) < limit:
        chunk = client.responses(
            vacancy_id, page=page, per_page=min(50, limit - len(items)),
            only_new=str(bool(only_new)).lower() if only_new else None,
        )
        found = chunk.get("items") or []
        items.extend(found)
        if page + 1 >= (chunk.get("pages") or 1) or not found:
            break
        page += 1
    return items[:limit]


def _profile(path):
    return pathlib.Path(path).read_text(encoding="utf-8")


def cmd_login(args):
    auth.login(open_browser=not args.no_browser)


def cmd_me(args):
    me = auth.client().me()
    print(json.dumps(
        {k: me.get(k) for k in ("id", "first_name", "last_name", "email", "is_employer", "employer")},
        ensure_ascii=False, indent=2,
    ))


def cmd_vacancies(args):
    client = auth.client()
    data = client.active_vacancies(_employer_id(client, args.employer))
    for v in data.get("items", []):
        counters = v.get("counters") or {}
        print(f"{v['id']}  {v.get('name'):<45} откликов: {counters.get('responses', '?')}"
              f"  новых: {counters.get('unread_responses', '?')}")
    if not data.get("items"):
        print("Активных вакансий не найдено.")


def cmd_responses(args):
    client = auth.client()
    items = _fetch_responses(client, args.vacancy, args.limit, args.only_new)
    for it in items:
        r = it.get("resume") or {}
        name = f"{r.get('first_name') or ''} {r.get('last_name') or ''}".strip() or "без имени"
        state = (it.get("state") or {}).get("name") or ""
        print(f"{it['id']:<12} {name:<28} {(r.get('title') or '')[:35]:<36} {state}")
    print(f"\nВсего: {len(items)}")


def cmd_analyze(args):
    client = auth.client()
    items = _fetch_responses(client, args.vacancy, args.limit, args.only_new)
    if not items:
        raise SystemExit("Откликов не найдено.")

    resumes = {}
    if args.full_resumes:
        for it in items:
            rid = (it.get("resume") or {}).get("id")
            if not rid:
                continue
            try:
                resumes[rid] = client.resume(rid)
            except HHError as exc:
                print(f"Резюме {rid} недоступно (HTTP {exc.status}), беру краткие данные",
                      file=sys.stderr)

    assessments = analyze_mod.analyze(items, _profile(args.profile), resumes)
    report = analyze_mod.render_report(assessments, args.vacancy)
    if args.out:
        pathlib.Path(args.out).write_text(report, encoding="utf-8")
        print(f"Отчёт: {args.out}")
    else:
        print(report)


def cmd_chat(args):
    client = auth.client()
    negotiation = client.negotiation(args.negotiation)
    chat_id = negotiation.get("chat_id")
    if chat_id:
        data = client.chat_messages(chat_id)
    else:
        data = client.legacy_messages(args.negotiation)
    for m in data.get("items", []):
        author = (m.get("author") or {}).get("participant_type") or m.get("author_participant_type") or "?"
        print(f"[{m.get('created_at') or ''}] {author}: {(m.get('text') or '').strip()}")


def cmd_draft(args):
    client = auth.client()
    negotiation = client.negotiation(args.negotiation)
    resume_id = (negotiation.get("resume") or {}).get("id")
    resume = None
    if resume_id and args.full_resume:
        try:
            resume = client.resume(resume_id)
        except HHError as exc:
            print(f"Резюме недоступно (HTTP {exc.status})", file=sys.stderr)
    text = analyze_mod.draft_reply(negotiation, _profile(args.profile), args.intent,
                                   resume, args.note)
    print(text)


def cmd_reply(args):
    client = auth.client()
    negotiation = client.negotiation(args.negotiation)
    text = args.text if args.text else sys.stdin.read().strip()
    if not text:
        raise SystemExit("Пустое сообщение.")

    r = negotiation.get("resume") or {}
    print(f"Кандидат: {(r.get('first_name') or '')} {(r.get('last_name') or '')}".strip())
    print(f"Отклик:   {args.negotiation}")
    print("-" * 60)
    print(text)
    print("-" * 60)
    if not args.yes:
        if input("Отправить кандидату? [y/N] ").strip().lower() not in ("y", "yes", "д", "да"):
            raise SystemExit("Отменено, ничего не отправлено.")

    chat_id = negotiation.get("chat_id")
    if chat_id:
        client.send_chat_message(chat_id, text, str(uuid.uuid4()), is_automated=args.automated)
    else:
        client.send_negotiation_message(args.negotiation, text)
    print("Отправлено.")


def cmd_screen(args):
    """Офлайн: анализ резюме, выгруженных из кабинета hh вручную."""
    assessments = analyze_mod.analyze_files(args.paths, _profile(args.profile))
    report = analyze_mod.render_report(assessments, args.title or "")
    if args.out:
        pathlib.Path(args.out).write_text(report, encoding="utf-8")
        print(f"Отчёт: {args.out}")
    else:
        print(report)


def cmd_letter(args):
    """Офлайн: текст письма кандидату — скопировать и отправить руками в hh."""
    print(analyze_mod.draft_reply_file(args.resume, _profile(args.profile),
                                       args.intent, args.note))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python3 -m hh", description="Работа с откликами hh.ru")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("login", help="OAuth-вход менеджером работодателя")
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("me", help="проверить токен и аккаунт")
    p.set_defaults(func=cmd_me)

    p = sub.add_parser("vacancies", help="активные вакансии и счётчики откликов")
    p.add_argument("--employer")
    p.set_defaults(func=cmd_vacancies)

    p = sub.add_parser("responses", help="список откликов по вакансии")
    p.add_argument("--vacancy", required=True)
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--only-new", action="store_true")
    p.set_defaults(func=cmd_responses)

    p = sub.add_parser("analyze", help="анализ откликов через Claude")
    p.add_argument("--vacancy", required=True)
    p.add_argument("--profile", default="hh/profiles/teacher.md")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--only-new", action="store_true")
    p.add_argument("--full-resumes", action="store_true", help="дотягивать полные резюме")
    p.add_argument("--out", help="файл для отчёта в markdown")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("chat", help="переписка по отклику")
    p.add_argument("negotiation")
    p.set_defaults(func=cmd_chat)

    p = sub.add_parser("draft", help="черновик ответа кандидату (не отправляется)")
    p.add_argument("negotiation")
    p.add_argument("--intent", default="invite", choices=["invite", "clarify", "reject"])
    p.add_argument("--profile", default="hh/profiles/teacher.md")
    p.add_argument("--note", help="дополнение от HR: время встречи, вопросы и т.п.")
    p.add_argument("--full-resume", action="store_true")
    p.set_defaults(func=cmd_draft)

    p = sub.add_parser("reply", help="отправить сообщение кандидату")
    p.add_argument("negotiation")
    p.add_argument("--text", help="текст; если не задан — читается со stdin")
    p.add_argument("--yes", action="store_true", help="без подтверждения")
    p.add_argument("--automated", action="store_true",
                   help="пометить сообщение как автоматическое (is_automated)")
    p.set_defaults(func=cmd_reply)

    p = sub.add_parser("screen", help="офлайн-анализ резюме из файлов (без токена hh)")
    p.add_argument("paths", nargs="+", help="файлы .pdf/.txt/.md или папка с ними")
    p.add_argument("--profile", default="hh/profiles/teacher.md")
    p.add_argument("--title", help="название вакансии для заголовка отчёта")
    p.add_argument("--out", help="файл для отчёта в markdown")
    p.set_defaults(func=cmd_screen)

    p = sub.add_parser("letter", help="офлайн-черновик письма по файлу резюме")
    p.add_argument("resume", help="файл резюме .pdf/.txt/.md")
    p.add_argument("--intent", default="invite", choices=["invite", "clarify", "reject"])
    p.add_argument("--profile", default="hh/profiles/teacher.md")
    p.add_argument("--note", help="дополнение от HR: время встречи, вопросы и т.п.")
    p.set_defaults(func=cmd_letter)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except HHError as exc:
        raise SystemExit(f"Ошибка API hh.ru: {exc}")


if __name__ == "__main__":
    main()
