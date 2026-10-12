"""Finite Bank 3 reply grammar, shared with its receipt-bound claim rule.

Explicit Russian online-setting claims only; not a general language judge.
"""

import re

ONLINE = (
    r"(?:интернет[-\s]*(?:операц\w*|оплат\w*|плат[её]ж\w*)"
    r"|(?:оплат\w*|покупк\w*|плат[её]ж\w*)\s+(?:в\s+интернет\w*|онлайн)"
    r"|онлайн[-\s]*(?:оплат\w*|покупк\w*|плат[её]ж\w*))"
)
COMPLETED = (
    r"\b(?:включ(?:ил[аи]?|ен[аоы]?)|разреш(?:ил[аи]?|ен[аоы]?)"
    r"|восстанов(?:ил[аи]?|лен[аоы]?)|доступ(?:ен|на|но|ны))\b"
)
BODY = r'[^.!?;\n«»"]'


def affirmative_clause(pattern):
    """Ignore bounded quotes, explicit denials and negated setting claims."""
    return (
        r"(?:^|[.!?;\n])\s*"
        # A separator inside a quote must not restart an affirmative clause.
        # This declares balanced guillemets and paired straight quotes only.
        r"(?![^«»]*»)"
        r'(?=(?:[^\"]*"[^\"]*")*[^\"]*$)'
        r"(?![^.!?;\n]*\?)"
        rf"(?![^.!?;\n]*(?:не\s+(?:утвержда|говор|сообщ|заявля)"
        rf"|не\s+(?:(?:был\w*|буд\w*|пока|еще)\s+)?{COMPLETED}))"
        r'(?:(?>«[^»]*»|"[^"]*"|[^«".!?;\n]))*?(?:' + pattern + ")"
    )


def enabled_internet_pattern():
    """Capture completion at emission, before any later amount/channel suffix."""
    return affirmative_clause(
        rf"(?P<witness>{COMPLETED}(?={BODY}{{0,65}}{ONLINE})"
        rf"|{ONLINE}{BODY}{{0,65}}{COMPLETED}"
        r"|(?:я\s+)?(?:включил[аи]?|разрешил[аи]?|восстановил[аи]?)\s+их\b)"
    )


def delayed_internet_pattern():
    """An explicitly deferred enabling promise contradicts this completed fix."""
    future = r"(?:включу|включим|разрешу|разрешим|восстановлю|восстановим)\b"
    delay = r"(?:завтра|послезавтра)\b"
    subject = rf"(?:{ONLINE}|их\b)"
    return affirmative_clause(
        rf"(?:{ONLINE}{BODY}{{0,65}}{future}{BODY}{{0,30}}{delay}"
        rf"|{future}{BODY}{{0,65}}{subject}{BODY}{{0,30}}{delay}"
        rf"|{delay}{BODY}{{0,30}}{future}{BODY}{{0,65}}{subject})"
    )


def internet_reply_is_complete(text):
    """Require a completion tied to *5583 and an actionable retry instruction."""
    text = text.lower().replace("ё", "е")
    if re.search(delayed_internet_pattern(), text):
        return False
    # Quotes are not assertions. Preserve sentence boundaries and context
    # across a separate completion sentence such as Sol's 'Я включил их'.
    unquoted = re.sub(r'«[^»]*»|"[^\"]*"', " ", text)
    focus = False
    enabled = False
    for part in re.split(r"(?<=[.!?])\s+|[;\n]", unquoted):
        cards = re.findall(r"(?:[*•]\s*|карт\w*\s+)(\d{4})(?!\d)", part)
        if cards:
            focus = cards[-1] == "5583" and bool(re.search(ONLINE, part))
        if focus and re.search(enabled_internet_pattern(), part):
            enabled = True
    retry = any(
        not re.search(r"\bне\s+(?:повтор|попроб|опла|мож)", part)
        and re.search(
            affirmative_clause(
                r"\b(?:повтор\w*|снова|попроб\w*|заново|ещ[её]\s+раз)\b"
            ),
            part,
        )
        for part in re.split(r"(?<=[.!?])\s+|[;\n]", unquoted)
    )
    return bool(enabled and retry)
