"""Finite Bank 7 claims, with merchant punctuation and bounded quote handling.

These declared Russian forms are not a universal natural-language judge.
"""

import re

AUTOPAYMENT = r"автоплат[её]ж\w*"
BODY = r'(?:Яндекс\.Плюс|[^.!?;\n«»"])'
STATUS = r"(?:отключ[её]н|отмен[её]н|cancelled)"
META_DENIAL = (
    r"не\s+(?:утвержда\w*|говор\w*|сообща\w*|заявля\w*)"
    r"\s*(?:,?\s*что\b|[:«\"])"
)


def affirmative_autopayment_clause(pattern):
    """Retain literal merchant dots, ignoring balanced quotes and meta-denials."""
    return (
        r"(?:^|[.!?;\n])\s*(?![^«»]*»)"
        r'(?=(?:[^\"]*"[^\"]*")*[^\"]*$)'
        rf"(?!{BODY}*{META_DENIAL})"
        r'(?:(?>«[^»]*»|"[^"]*"|Яндекс\.Плюс|[^«".!?;\n]))*?(?:' + pattern + ")"
    )


def cancellation_claim_pattern():
    """A completed cancellation, rather than a denial or future instruction."""
    return affirmative_autopayment_clause(
        rf"(?P<witness>{AUTOPAYMENT}"
        rf"(?!{BODY}{{0,90}}\bне\s+(?:был[аи]?\s+)?(?:ранее\s+)?[«\"]?{STATUS})"
        rf"{BODY}{{0,90}}"
        rf"(?<!не )(?<!не был )[«\"]?{STATUS}[»\"]?"
        rf"|(?<!не )(?:отключил[аи]?|отменил[аи]?){BODY}{{0,90}}{AUTOPAYMENT})"
    )


def opposite_status_pattern():
    """Declared contradictory status forms, activated after successful cancellation."""
    return affirmative_autopayment_clause(
        rf"{AUTOPAYMENT}{BODY}{{0,90}}(?:не\s+(?:был[аи]?\s+)?[«\"]?{STATUS}[»\"]?"
        r"|(?<!не )(?<!был )(?<!была )(?<!были )актив(?:ен|ный|ным)\b)"
        rf"|не\s+(?:отключил[аи]?|отменил[аи]?|отключал[аи]?|отменял[аи]?)"
        rf"{BODY}{{0,60}}{AUTOPAYMENT}"
    )


def incorrect_independence_pattern():
    """Explicit denials of the two independent bank/vendor objects."""
    return affirmative_autopayment_clause(
        r"не\s+(?:(?:явля\w*|счита\w*)\s+)?"
        rf"(?:независим\w*|отдельн\w*|разные){BODY}{{0,30}}(?:запис\w*|объект\w*)"
    )


def incorrect_subscription_pattern():
    """Declared current-status contradictions for the already cancelled subscription."""
    subscription_body = rf"(?:(?!автоплат){BODY})"
    return affirmative_autopayment_clause(
        rf"подписк\w*{subscription_body}{{0,60}}(?:не\s+(?:была\s+)?отмен\w*"
        r"|(?<!не )(?<!была )актив(?:на|ная|ной)\b)"
    )


def autopayment_reply_is_complete(text):
    """Require the cancelled object, prior subscription and their independence."""
    if any(
        re.search(p, text, re.I)
        for p in (
            opposite_status_pattern(),
            incorrect_independence_pattern(),
            incorrect_subscription_pattern(),
        )
    ):
        return False
    # Keep quoted merchant names and bare status labels; quoted propositions do
    # not supply the agent's own result or explanation.
    text = re.sub(
        r'«[^»]*»|"[^"\n]*"',
        lambda m: m[0][1:-1]
        if re.fullmatch(rf"(?:Яндекс[. ]Плюс|{STATUS})", m[0][1:-1], re.I)
        else " ",
        text,
    )
    text = re.sub(rf"(?:^|[.!?;\n]){BODY}*{META_DENIAL}{BODY}*", " ", text, flags=re.I)
    text = text.replace("ё", "е").lower()
    merchant = re.search(r"яндекс[. ]плюс", text)
    amount = re.search(r"(?<![\d.,])299(?:[.,]00)?\s*(?:₽|руб\w*|RUB\b)", text, re.I)
    cancelled = re.search(cancellation_claim_pattern(), text, re.I)
    prior_subscription = re.search(
        rf"подписк\w*{BODY}{{0,90}}(?<!не )отмен\w*"
        rf"|(?<!не )отмен\w*{BODY}{{0,60}}подписк\w*",
        text,
        re.I,
    )
    independent = re.search(
        r"независим\w*\s+(?:запис\w*|объект\w*)"
        rf"|(?:отдельн\w*|разные){BODY}{{0,30}}(?:запис\w*|объект\w*)"
        rf"|отмена\s+подписки{BODY}{{0,45}}не\s+(?:отключ|отмен)",
        text,
        re.I,
    )
    return bool(
        merchant and amount and cancelled and prior_subscription and independent
    )
