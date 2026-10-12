"""Finite Bank 2 claim grammar; not a universal natural-language judge.

Only explicit numeric rates, ruble amounts and calendar dates in the declared
Russian claim forms are covered. The same patterns guard intermediate channels.
"""

import re

MONTHS = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)
DATE = (
    rf"(?:\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}[./-]\d{{1,2}}(?:[./-]\d{{4}})?"
    rf"|\d{{1,2}}\s+(?:{'|'.join(MONTHS)})(?:\s+\d{{4}})?)"
)
EXPECTED_PAYOUT = (
    r"(?:2026-09-05|0?5[./-]0?9(?:[./-]2026)?(?![./-]\d)"
    r"|0?5\s+сентября(?:\s+2026)?(?!\s+\d{4}))(?!\d)"
)


CONDITIONAL_PAYOUT = (
    rf"\bесли\s+{EXPECTED_PAYOUT}\s+"
    r"(?:(?:кешбэк|вознаграждение|деньги)\s+|60\s*(?:₽|руб\w*|RUB)\s+)?"
    r"не\s+поступ\w*"
)

WRONG_RUBLE_WORDS = r"(?:восемьдесят\s+четыре|восьмидесяти\s+четыр[её]м)"


def invalid_cashback_claim_patterns():
    """Return scoped explicit claims incompatible with the Bank 2 fixture."""
    return {
        "rate": (
            r"ставк\w*[^.!?;\n]{0,55}?(?<![\d.,])(?<!не )"
            r"(?!5(?:[.,]0+)?\s*%)\d+(?:[.,]\d+)?\s*%"
        ),
        "amount": (
            r"(?:кешбэк\w*|вознаграждени\w*)[^\d.!?;\n]{0,35}?(?<![\d.,])(?<!не )"
            r"(?!60(?:[.,]0+)?\s*(?:₽|руб|RUB))\d+(?:[.,]\d+)?\s*"
            r"(?:₽|руб(?:ль|ля|лей|ли|лях)?\b|RUB\b)"
        ),
        "amount_words": (
            r"(?:кешбэк\w*|вознаграждени\w*)"
            rf"(?:(?!\bне\s+(?:(?:равен|составляет)\s+)?{WRONG_RUBLE_WORDS})[^.!?;\n]){{0,35}}?"
            rf"(?<!не ){WRONG_RUBLE_WORDS}"
            r"\s+руб\w*"
        ),
        "payout": (
            r"(?<!отсутствие )(?:дат\w*\s+(?:выплат\w*|начислен\w*)|поступ\w*|выплат\w*|начисл\w*|ожида\w*|перечисл\w*)"
            r"(?:(?!покупк|операци)[^.!?;\n]){0,45}?(?<![\w./-])(?<!не )"
            rf"(?!{EXPECTED_PAYOUT}){DATE}(?!\d)"
        ),
        "denied_payout": (
            r"(?:5|пятого)\s+(?:сентября|числа)[^.!?;\n]{0,30}"
            r"(?:денег\s+не\s+будет|не\s+(?:поступ|начисл|выплат))"
        ),
    }


def normalize_cashback_dates(text):
    """Map explicit numeric dates to the existing month-name reply vocabulary."""

    def replace(match):
        if match.group("iso_year"):
            year, month, day = (
                match.group("iso_year"),
                match.group("iso_month"),
                match.group("iso_day"),
            )
        else:
            day, month, year = (
                match.group("day"),
                match.group("month"),
                match.group("year"),
            )
        month_number = int(month)
        if not 1 <= month_number <= 12:
            return match[0]
        return f"{int(day)} {MONTHS[month_number - 1]}" + (f" {year}" if year else "")

    return re.sub(
        r"(?<!\d)(?:(?P<iso_year>\d{4})-(?P<iso_month>\d{2})-(?P<iso_day>\d{2})"
        r"|(?P<day>\d{1,2})[./-](?P<month>\d{1,2})(?:[./-](?P<year>\d{4}))?)(?!\d)",
        replace,
        text,
    )
