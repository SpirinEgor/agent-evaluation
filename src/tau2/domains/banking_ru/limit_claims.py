"""Finite message contracts for customers whose numeric limits cannot change.

These patterns distinguish an execution claim from a current monetary setting.
They are not a universal natural-language truth checker. Quoted/conditional
contexts and native utterance boundaries are covered by explicit regressions.
"""

import re
import unicodedata
from math import isfinite

BOUNDARY = r"(?:^|[.!?;]\s*|,\s*(?:(?:но|а|однако)\s+)?)\s*"
INTRO = r"(?:(?:однако|итак|сообщаю|готово|по\s+карте\s+\d{4})\s*[:,]?\s+)?"
CASH_PREFIX = r"по\s+(?:наличным|снятию\s+наличных)\s*[:,]?\s+"
SBP_PREFIX = r"по\s+сбп\s*[:,]?\s+"
SUBJECT = r"(?:(?:ваш(?:и)?|текущ(?:ий|ие)|действующ(?:ий|ие)|суточн(?:ый|ые))\s+)?лимит(?:ы|а|ов)?\b"
MODIFIERS = r"(?:\s+(?:снятия|снятие|снятию|наличных|наличными|переводов|по|сбп|карте|для|и|на|уже|теперь|успешно|был|были|мы|я|\d{4})\b){0,12}"
COMPLETED = r"(?:увеличен(?:ы)?|повышен(?:ы)?|поднят(?:ы)?|измен[её]н(?:ы)?|увеличил(?:и)?|повысил(?:и)?|поднял(?:и)?|установил(?:и)?|изменил(?:и)?)\b"
UNIT = r"(?:₽|руб(?:лей|ля|ль)?\.?)(?!\w)"


def _current_amount(value: float) -> str:
    if (
        isinstance(value, bool)
        or not isfinite(value)
        or value < 0
        or int(value) != value
    ):
        raise ValueError(
            "Pending-limit contracts require finite nonnegative whole-ruble settings"
        )
    return r"\s*".join(str(int(value))) + r"(?:[.,]0{1,2})?\s*" + UNIT


def limit_message_patterns(cash_current: float, sbp_current: float) -> dict[str, str]:
    """Build declared patterns from the actual task's unchanged numeric settings."""
    verb_first = r"(?:(?:мы|я)\s+)?(?:уже\s+)?(?:увеличил(?:и)?|повысил(?:и)?|поднял(?:и)?|установил(?:и)?|изменил(?:и)?)\s+(?:ваш(?:и)?\s+)?лимит(?:ы)?\b"
    new_limit = (
        r"нов(?:ый|ые)\s+лимит(?:ы)?"
        + MODIFIERS
        + r"\s+(?:действует|действуют|активен|активны|установлен|установлены|"
        + COMPLETED
        + r")\b"
    )
    completed_intro = r"(?:" + CASH_PREFIX + "|" + SBP_PREFIX + "|" + INTRO + ")"
    completed = (
        BOUNDARY
        + completed_intro
        + r"(?:"
        + SUBJECT
        + MODIFIERS
        + r"\s+"
        + COMPLETED
        + "|"
        + verb_first
        + "|"
        + new_limit
        + r")(?!\s+(?:ли\b|не\s+(?:был|были)\b|ранее\b|в\s+прошлом\b))"
    )

    # Classify only channel words before the monetary predicate. A later SBP
    # clause must not relabel an earlier cash statement; joint channels must
    # satisfy both settings. A bare limit in these cash tickets means cash.
    has_cash = r"(?=" + MODIFIERS + r"\s+(?:снятия|снятие|снятию|наличных|наличными)\b)"
    has_sbp = r"(?=" + MODIFIERS + r"\s+сбп\b)"
    no_sbp = r"(?!" + MODIFIERS + r"\s+сбп\b)"
    setting = (
        MODIFIERS
        + r"\s+(?:(?:установлен(?:ы)?|составляет|составляют|равен|равны|[—:-])\s*)?(?:(?:на|в\s+размере)\s+)?"
    )
    amount = r"\d(?:[\d\s]*\d)?(?:[.,]\d{1,2})?\s*" + UNIT
    cash_subject = (
        r"(?:"
        + INTRO
        + SUBJECT
        + r"(?:"
        + has_cash
        + "|"
        + no_sbp
        + ")|"
        + CASH_PREFIX
        + SUBJECT
        + ")"
    )
    sbp_subject = r"(?:" + INTRO + SUBJECT + has_sbp + "|" + SBP_PREFIX + SUBJECT + ")"
    return {
        "no_completed_limit_change": completed,
        "no_wrong_current_cash_limit": BOUNDARY
        + cash_subject
        + setting
        + r"(?!"
        + _current_amount(cash_current)
        + ")"
        + amount,
        "no_wrong_current_sbp_limit": BOUNDARY
        + sbp_subject
        + setting
        + r"(?!"
        + _current_amount(sbp_current)
        + ")"
        + amount,
    }


def has_false_limit_claim(text: str, cash_current: float, sbp_current: float) -> bool:
    """Apply the same finite claim rules to the complete saved final reply."""
    text = unicodedata.normalize("NFKC", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Cf")
    return any(
        re.search(pattern, text, re.IGNORECASE)
        for pattern in limit_message_patterns(cash_current, sbp_current).values()
    )


def numeric_limit_claim_patterns() -> dict[str, tuple[str, str, bool]]:
    """Declare current/completed cash and SBP forms; values are checked at runtime."""
    card_intro = r"(?:(?:по|на)\s+карте\s+(?:\*|…|\.\.\.)?\d{4}\s*[:,]?\s+)?"
    has_cash = r"(?=" + MODIFIERS + r"\s+(?:снятия|снятие|снятию|наличных|наличными)\b)"
    has_sbp = r"(?=" + MODIFIERS + r"\s+сбп\b)"
    no_sbp = r"(?!" + MODIFIERS + r"\s+сбп\b)"
    subjects = {
        "cash": (
            r"(?:"
            + SUBJECT
            + r"(?:"
            + has_cash
            + "|"
            + no_sbp
            + ")|"
            + CASH_PREFIX
            + SUBJECT
            + ")",
            "daily_cash_withdrawal",
        ),
        "sbp": (r"(?:" + SUBJECT + has_sbp + "|" + SBP_PREFIX + SUBJECT + ")", "sbp"),
    }
    value = r"(?P<value>\d(?:[\d\s]*\d)?(?:[.,]\d{1,2})?|миллион(?:а|ов)?)\s*" + UNIT
    prep = r"(?:(?:на|до|в\s+размере)\s+)?"
    completed = r"(?:увеличен(?:ы)?|повышен(?:ы)?|поднят(?:ы)?|измен[её]н(?:ы)?|увеличил[аи]?|повысил[аи]?|поднял[аи]?|установил[аи]?|изменил[аи]?)\b"
    verb = r"(?:(?:мы|я)\s+)?(?:уже\s+)?(?:увеличил[аи]?|повысил[аи]?|поднял[аи]?|установил[аи]?|изменил[аи]?)\s+"
    patterns = {}
    for channel, (subject, field) in subjects.items():
        lead = BOUNDARY + card_intro + INTRO
        setting = (
            MODIFIERS
            + r"\s+(?:(?:установлен(?:ы)?|составляет|составляют|равен|равны|[—:-])\s*)?"
            + prep
        )
        patterns[channel + "_current"] = (
            lead + subject + setting + value,
            field,
            False,
        )
        executed = (
            r"(?:"
            + subject
            + MODIFIERS
            + r"\s+"
            + completed
            + "|"
            + verb
            + subject
            + ")"
        )
        patterns[channel + "_completed"] = (
            lead
            + "(?P<witness>"
            + executed
            + ")"
            + MODIFIERS
            + r"(?!\s+(?:ли\b|не\s+(?:был|были)\b|ранее\b|в\s+прошлом\b))(?:\s+"
            + prep
            + value
            + ")?",
            field,
            True,
        )
    return patterns
