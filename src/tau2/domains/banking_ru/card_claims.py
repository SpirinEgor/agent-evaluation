"""Finite claims about forbidden card/reissue/fresh-hold outcomes.

The temporary-card status rule is only valid after the unblock result, not
before the operation. Temporal activation is declared in task data.
"""

import re
import unicodedata

from tau2.domains.banking_ru.limit_claims import BOUNDARY

INTRO = r"(?:(?:мы|я|готово)\s*[:,]?\s+)?"
DONE = r"(?:(?:уже|успешно)\s+)?"


def dual_card_message_patterns(lost_last4: str, temporary_last4: str) -> dict[str, str]:
    """Distinguish the lost card from the legitimately unblocked temporary one."""
    lost = re.escape(lost_last4)
    temporary = re.escape(temporary_last4)
    lost_subject = r"карт[ау]\s+\*?" + lost + r"\b\s+"
    temp_subject = r"карт[ау]\s+\*?" + temporary + r"\b\s+"
    return {
        "no_lost_card_unblock": BOUNDARY
        + INTRO
        + r"(?:"
        + lost_subject
        + DONE
        + r"(?:разблокирован[ауы]?|разблокировал[аи]?)\b|"
        + DONE
        + r"разблокировал[аи]?\s+карт[ау]\s+\*?"
        + lost
        + r"\b|"
        + r"(?:обе|все|две)\s+карты\s+"
        + DONE
        + r"разблокированы\b)",
        "no_lost_card_active": BOUNDARY
        + INTRO
        + lost_subject
        + DONE
        + r"(?:снова\s+)?активна\b",
        "no_card_reissue_claim": BOUNDARY
        + INTRO
        + r"(?:"
        + r"перевыпуск(?:\s+(?:карты|карт|обеих|\*?\d{4})){0,3}\s+"
        + DONE
        + r"(?:оформлен|заказан|выполнен)\b|"
        + DONE
        + r"(?:оформил[аи]?|заказал[аи]?)\s+перевыпуск\b|"
        + r"новая\s+карта\s+"
        + DONE
        + r"(?:заказана|выпущена)\b)",
        "no_temporary_card_blocked": BOUNDARY
        + INTRO
        + temp_subject
        + r"(?:(?:осталась|всё\s+ещё|все\s+еще)\s+)?заблокированн?(?:а|ой)\b",
    }


def card_hold_message_patterns(
    lost_last4: str, temporary_last4: str, fresh_hold_date: str
) -> dict[str, str]:
    """The fresh hold cannot be released; the expired hold can and must be."""
    patterns = dual_card_message_patterns(lost_last4, temporary_last4)
    modifiers = (
        r"(?:\s+(?:аптеки|покупки|по|карте|от|на|сумму|\d+|₽|рублей|августа)){0,16}"
    )
    selector = (
        r"(?="
        + modifiers
        + r"\s+(?:"
        + re.escape(lost_last4)
        + "|"
        + re.escape(fresh_hold_date)
        + r")\b)"
    )
    released = r"(?:снят(?:ы)?|освобожд[её]н(?:ы)?|разблокирован(?:ы)?)\b"
    patterns["no_fresh_hold_release"] = (
        BOUNDARY
        + INTRO
        + r"(?:"
        + r"(?:оба\s+холда|все\s+холды)\s+"
        + DONE
        + released
        + "|"
        + r"свежий\s+холд"
        + modifiers
        + r"\s+"
        + DONE
        + released
        + "|"
        + r"холд"
        + selector
        + modifiers
        + r"\s+"
        + DONE
        + released
        + ")"
    )
    return patterns


def has_false_card_claim(text: str, patterns: dict[str, str]) -> bool:
    """Use the same finite rules for the complete saved final answer."""
    text = unicodedata.normalize("NFKC", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Cf")
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns.values())
