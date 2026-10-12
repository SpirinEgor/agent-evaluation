"""Finite Bank 6 fee assertions; shared final and intermediate claim grammar.

Explicit Russian calculations, ordinary negations and balanced quotations only.
This is not a general semantic judge or a guarantee about every paraphrase.
"""

import re

BODY = r'[^.!?;\n«»"]'
BASE = r"50\s*000(?:[.,]00)?"
CORRECT_FEE = r"500(?:[.,]00)?(?!\d|[.,]\d)"
WRONG_FEE = rf"(?!{CORRECT_FEE})\d+(?:[.,]\d+)?"
CURRENCY = r"(?:₽|руб\w*|RUB\b)"
META_DENIAL = (
    r"не\s+(?:утвержда\w*|говор\w*|сообща\w*|заявля\w*)"
    r"\s*(?:,?\s*что\b|[:«\"])"
)


def affirmative_fee_clause(pattern):
    """Ignore quoted claims and explicit denials, retaining a later assertion."""
    return (
        r"(?:^|[.!?;\n])\s*(?![^«»]*»)"
        r'(?=(?:[^\"]*"[^\"]*")*[^\"]*$)'
        rf"(?![^.!?;\n]*{META_DENIAL})"
        r'(?:(?>«[^»]*»|"[^"]*"|[^«".!?;\n]))*?(?:' + pattern + ")"
    )


def invalid_fee_claim_patterns():
    """Declare bounded wrong-rate, wrong-calculation and completed-refund claims."""
    return {
        "calculated_amount": affirmative_fee_clause(
            rf"1\s*%{BODY}{{0,25}}(?:то\s+есть|это|равно|=)\s*"
            rf"{WRONG_FEE}\s*{CURRENCY}"
        ),
        "equation": affirmative_fee_clause(
            rf"{BASE}\s*(?:{CURRENCY})?\s*[×*xх]\s*1\s*%\s*=\s*"
            rf"{WRONG_FEE}(?!\d)"
        ),
        "fee_amount": affirmative_fee_clause(
            rf"комисси\w*{BODY}{{0,45}}(?<!не )(?:составляет|равна|должна\s+быть)"
            rf"\s*{WRONG_FEE}\s*{CURRENCY}"
        ),
        "wrong_rate": affirmative_fee_clause(
            rf"(?:комисси\w*|ставк\w*){BODY}{{0,45}}(?<!не )(?:составляет|равна)"
            r"\s*(?!1\s*%)\d+(?:[.,]\d+)?\s*%"
        ),
        "correct_250": affirmative_fee_clause(
            rf"правильн\w*{BODY}{{0,25}}(?:комисси\w*|сумм\w*)"
            rf"{BODY}{{0,25}}(?<!не )250\s*{CURRENCY}"
        ),
        "refunded": affirmative_fee_clause(
            rf"(?<!не )(?:вернул[аи]?|возместил[аи]?){BODY}{{0,35}}комисси\w*"
            rf"|комисси\w*{BODY}{{0,40}}(?<!не )(?<!не была )(?<!не будет )"
            r"(?:возвращена|вернул[аи]?|зачислена)"
        ),
    }


def transfer_fee_reply_is_complete(text):
    """Require the actual base, rate, computed fee and absence of overpayment."""
    text = text.lower().replace("ё", "е")
    if any(re.search(p, text, re.I) for p in invalid_fee_claim_patterns().values()):
        return False
    # A quoted false calculation is not evidence for the agent's own answer.
    text = re.sub(
        r'«[^»]*»|"[^"]*"',
        lambda m: " " if re.search(r"\d|комисс|возврат", m[0]) else m[0],
        text,
    )
    text = re.sub(
        rf"(?:^|[.!?;\n])[^.!?;\n]*{META_DENIAL}[^.!?;\n]*",
        " ",
        text,
    )
    text = re.sub(r"(?<=\d)[\s\u00a0\u202f\u2009]+(?=\d)", "", text)
    # Remove explicitly rejected alternatives, preserving the actual calculation.
    text = re.sub(r"\bне\s+\d+(?:[.,]\d+)?\s*(?:%|₽|руб\w*)", " ", text)
    correct = (
        re.search(r"(?<![\d.,])50000(?:[.,]00)?(?!\d)", text)
        and re.search(r"(?<![\d.,])500(?:[.,]00)?(?!\d|[.,]\d)", text)
        and re.search(r"(?<![\d.,])1\s*%|один\s+процент", text)
    )
    lawful = re.search(
        r"тариф|правомер|рассчитан\w*\s+(?:верно|правильно)|коррект", text
    )
    decision = re.search(
        r"переплат\w*\s+нет|нет\s+переплат|возврат\w*\s+не\s+(?:провод|подлеж)"
        r"|не\s+подлежит\s+возврат|(?:вернуть|возвращать)\s+(?:нечего|не\s+мож)"
        r"|основан\w*.{0,25}возврат\w*.{0,20}нет|не\s+(?:возвращ|вернул)|отказ",
        text,
    )
    return bool(correct and lawful and decision)
