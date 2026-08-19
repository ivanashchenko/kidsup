"""Анализ откликов и черновики ответов кандидатам через Claude API.

Модуль опциональный: без ключа ANTHROPIC_API_KEY остальные команды
(выгрузка откликов, чтение переписки) работают как обычно.
"""

from typing import List, Literal, Optional

MODEL = "claude-opus-5"

SYSTEM = """Ты — помощник HR частного детского сада и центра развития детей KidsUP (Москва).
Оцениваешь отклики кандидатов на вакансию по профилю требований.

Правила:
- Опирайся только на факты из резюме и сопроводительного письма. Ничего не додумывай.
- Отдельно отмечай то, что критично для работы с детьми: профильное образование,
  опыт с нужным возрастом, медкнижка, документы, готовность к графику.
- Если данных для вывода не хватает — это не недостаток кандидата, а вопрос для уточнения.
- Пиши по-русски, кратко и по делу."""


class _Lazy:
    """Импортируем pydantic/anthropic только когда анализ реально запускают."""

    def __init__(self):
        self._ready = False

    def load(self):
        if self._ready:
            return
        try:
            import anthropic  # noqa: F401
            from pydantic import BaseModel  # noqa: F401
        except ImportError as exc:
            raise SystemExit(
                "Для анализа нужны зависимости: pip install -r hh/requirements.txt"
            ) from exc
        self._ready = True


_lazy = _Lazy()


def _models():
    _lazy.load()
    from pydantic import BaseModel, Field

    class Assessment(BaseModel):
        negotiation_id: str = Field(description="ID отклика из входных данных")
        candidate: str = Field(description="Имя кандидата или 'без имени'")
        score: int = Field(description="Соответствие профилю, 0-100", ge=0, le=100)
        summary: str = Field(description="1-2 предложения: кто это и насколько подходит")
        strengths: List[str]
        concerns: List[str] = Field(description="Слабые места и несоответствия профилю")
        missing_info: List[str] = Field(description="Что стоит уточнить у кандидата")
        recommended_action: Literal["invite", "clarify", "reject"]
        reason: str = Field(description="Почему именно такое действие")

    class Report(BaseModel):
        assessments: List[Assessment]

    return Report


def _client():
    _lazy.load()
    import anthropic

    return anthropic.Anthropic()


def candidate_brief(item, resume=None):
    """Собирает компактную выжимку по отклику для передачи модели."""
    r = item.get("resume") or {}
    parts = [
        f"ID отклика: {item.get('id')}",
        f"Кандидат: {(r.get('first_name') or '')} {(r.get('last_name') or '')}".strip(),
        f"Желаемая должность: {r.get('title') or '—'}",
        f"Возраст: {r.get('age') or '—'}",
        f"Город: {((r.get('area') or {}).get('name')) or '—'}",
        f"Опыт (мес.): {((r.get('total_experience') or {}).get('months')) or '—'}",
    ]
    letter = item.get("message") or item.get("cover_letter")
    if isinstance(letter, list):
        letter = " ".join(str(m.get("text", "")) for m in letter if isinstance(m, dict))
    if letter:
        parts.append(f"Сопроводительное письмо: {letter}")

    src = resume or r
    for exp in (src.get("experience") or [])[:8]:
        parts.append(
            "Опыт: {} — {} ({}—{}): {}".format(
                exp.get("company") or "—",
                exp.get("position") or "—",
                exp.get("start") or "?",
                exp.get("end") or "по н.в.",
                (exp.get("description") or "")[:600],
            )
        )
    for edu in ((src.get("education") or {}).get("primary") or [])[:5]:
        parts.append(
            "Образование: {}, {} ({})".format(
                edu.get("name") or "—", edu.get("organization") or "—", edu.get("year") or "—"
            )
        )
    if src.get("skill_set"):
        parts.append("Навыки: " + ", ".join(src["skill_set"][:40]))
    if src.get("skills"):
        parts.append("О себе: " + str(src["skills"])[:800])
    return "\n".join(parts)


def analyze(items, profile_text, resumes=None):
    """Возвращает список оценок кандидатов, отсортированный по убыванию score."""
    Report = _models()
    resumes = resumes or {}
    briefs = "\n\n---\n\n".join(
        candidate_brief(it, resumes.get((it.get("resume") or {}).get("id"))) for it in items
    )
    prompt = (
        f"Профиль вакансии и требования:\n{profile_text}\n\n"
        f"Отклики ({len(items)} шт.):\n\n{briefs}\n\n"
        "Оцени каждый отклик. Верни оценку для всех откликов, ничего не пропуская."
    )
    response = _client().messages.parse(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": prompt}],
        output_format=Report,
    )
    result = response.parsed_output.assessments
    return sorted(result, key=lambda a: a.score, reverse=True)


INTENTS = {
    "invite": "пригласить на собеседование (очное, по адресу центра), предложить 2-3 варианта времени",
    "clarify": "вежливо задать уточняющие вопросы из missing_info",
    "reject": "корректно отказать, поблагодарив за отклик",
}


def draft_reply(item, profile_text, intent, resume=None, extra=None):
    """Черновик сообщения кандидату. Никогда не отправляется автоматически."""
    _lazy.load()
    task = INTENTS.get(intent, intent)
    prompt = (
        f"Профиль вакансии:\n{profile_text}\n\n"
        f"Отклик:\n{candidate_brief(item, resume)}\n\n"
        f"Задача: {task}.\n"
        + (f"Дополнительно от HR: {extra}\n" if extra else "")
        + "Напиши текст сообщения кандидату в hh.ru: на «вы», доброжелательно, "
        "без канцелярита и эмодзи, 3-6 предложений. Только текст сообщения, без пояснений."
    )
    response = _client().messages.create(
        model=MODEL,
        max_tokens=2000,
        system=SYSTEM,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": prompt}],
    )
    return "\n".join(b.text for b in response.content if b.type == "text").strip()


def render_report(assessments, vacancy_name=""):
    lines = [f"# Отклики: {vacancy_name}".rstrip(), ""]
    for a in assessments:
        lines += [
            f"## {a.candidate} — {a.score}/100 ({a.recommended_action})",
            f"Отклик: {a.negotiation_id}",
            "",
            a.summary,
            "",
        ]
        if a.strengths:
            lines += ["**Плюсы:**"] + [f"- {s}" for s in a.strengths] + [""]
        if a.concerns:
            lines += ["**Риски:**"] + [f"- {s}" for s in a.concerns] + [""]
        if a.missing_info:
            lines += ["**Уточнить:**"] + [f"- {s}" for s in a.missing_info] + [""]
        lines += [f"_Рекомендация: {a.reason}_", ""]
    return "\n".join(lines)
