"""Finite Bank 8 payout grammar, shared by final and intermediate checks.

Explicit ruble amounts, dates, Russian negations and balanced quotes are covered;
this is not a universal natural-language or encoded-message judge.
"""

import re
from decimal import Decimal

BODY = r'(?:\.(?=\d)|[^.!?;\n«»"])'
CURRENCY = r"(?:₽|руб\w*|RUB\b)"
NUMBER = r"(?<![\d.,])(?<!\d\s)\d+(?:[\s\u00a0\u202f\u2009]+\d{3})*(?:[.,]\d{1,2})?"
EARLY = r"(?:досроч\w*|сегодня|сейчас)"
MATURITY = (
    r"(?:1\s+марта\s+2027(?:\s+года)?|2027-03-01|01\.03\.2027"
    r"|(?:конц\w*|окончан\w*)\s+срока)"
)
LABELS = {
    "early": rf"{EARLY}{BODY}{{0,130}}?",
    "maturity": rf"{MATURITY}{BODY}{{0,180}}?",
    "difference": rf"(?:разниц\w*|потер\w*|недополуч\w*){BODY}{{0,80}}?",
}
DATE = r"(?:\d{1,2}\s+[а-я]+\s+\d{4}(?:\s+года)?|\d{2}[./]\d{2}[./]\d{4}|\d{4}-\d{2}-\d{2})"
EARLY_SUMMARY = rf"{EARLY}\s*(?:(?:закрыти\w*|возврат\w*)\s*)?(?:{DATE}\s*)?[:—,-]\s*"
DIFFERENCE = r"(?:разниц\w*|потер\w*|недополуч\w*)"
DIFFERENCE_AFTER = rf"\s*[—:-]\s*(?:это\s+)?{DIFFERENCE}"
EXPECTED = {"early": "502465.75", "maturity": "540000.00", "difference": "37534.25"}
PAYOUT_BODY = rf"(?:(?!разниц\w*|потер\w*|недополуч\w*){BODY})"
PAYOUT_VALUE_BODY = rf"(?:(?!{NUMBER}\s*{CURRENCY}){PAYOUT_BODY})"
PAYOUT = r"(?:выплат\w*|получите|получим|получил\w*\s+бы|всего|итого)"


def affirmative(pattern, excluding_difference=False):
    """Preserve decimal punctuation, excluding quotes and explicit meta-denials."""
    guard = r"(?!разниц\w*|потер\w*|недополуч\w*)" if excluding_difference else ""
    unit = r'(?>«[^»]*»|"[^"]*"|\.(?=\d)|[^«".!?;\n])'
    return (
        r"(?:^|[!?;\n]|\.(?!\d))\s*(?![^«»\n]*»)"
        r'(?=(?:[^"\n]*"[^"\n]*")*[^"\n]*(?:\n|$))'
        r"(?![^.!?;\n]*не\s+(?:утвержда\w*|говор\w*|сообща\w*|заявля\w*)"
        r"\s*,?\s*что\b)" + rf"(?:{guard}{unit})*?(?:{pattern})"
    )


def invalid_deposit_claim_patterns():
    """Task-specific wrong payouts/difference, including fractional amounts."""
    patterns = {}
    for family, value in EXPECTED.items():
        whole, cents = value.split(".")
        whole = r"\s*".join([whole[:-3], whole[-3:]])
        correct = whole + (r"(?:[.,]00)?" if cents == "00" else rf"[.,]{cents}")
        wrong = rf"(?!{correct}\s*{CURRENCY}){NUMBER}\s*{CURRENCY}"
        prefix = LABELS[family]
        if family != "difference":
            marker = EARLY if family == "early" else MATURITY
            other = MATURITY if family == "early" else EARLY
            role_body = rf"(?:(?!{other}){PAYOUT_BODY})"
            prefix = (
                rf"(?:{marker}{role_body}{{0,180}}?{PAYOUT}{PAYOUT_VALUE_BODY}{{0,45}}?"
                rf"|{EARLY_SUMMARY if family == 'early' else marker + r'\s*(?:года)?\s*[—:,-]\s*'})"
            )
        patterns[family] = affirmative(prefix + wrong, family != "difference")
        if family == "difference":
            patterns["difference_amount_first"] = affirmative(wrong + DIFFERENCE_AFTER)
        if family != "difference":
            other = MATURITY if family == "early" else EARLY
            across = rf'(?:(?!{other}|разниц\w*|{PAYOUT})[^«»"!?\n]){{0,220}}?'
            patterns[family + "_context"] = affirmative(
                rf"{marker}{across}{PAYOUT}{PAYOUT_VALUE_BODY}{{0,45}}?{wrong}", True
            )
            patterns[family + "_amount_first"] = affirmative(
                rf"{wrong}{PAYOUT_BODY}{{0,45}}?{PAYOUT}{PAYOUT_BODY}{{0,100}}?{marker}",
                True,
            )
    patterns["tax_guarantee"] = affirmative(
        rf"(?:налог\w*{BODY}{{0,70}}(?:гарант\w*|точно){BODY}{{0,40}}"
        r"(?:отсутств|не\s+будет)|(?:гарант\w*|точно)"
        rf"{BODY}{{0,50}}(?:без\s+налог|налог\w*\s+не\s+будет))"
    )
    return patterns


def deposit_payout_claim_patterns():
    """Capture explicit payout claims at the moment the amount is sent."""
    variants = [
        affirmative(rf"{prefix}{NUMBER}\s*{CURRENCY}")
        for prefix in [*LABELS.values(), rf"{PAYOUT}{BODY}{{0,45}}?"]
    ] + [
        affirmative(rf"{NUMBER}\s*{CURRENCY}{BODY}{{0,45}}?{PAYOUT}"),
        affirmative(rf"{NUMBER}\s*{CURRENCY}{DIFFERENCE_AFTER}"),
    ]
    # ArgumentEvidence.patterns is conjunctive: alternative wording belongs in
    # a single regex, otherwise all alternatives become mandatory speech.
    return ["(?P<witness>(?:" + "|".join(variants) + "))"]


def _own_text(text):
    text = text.lower().replace("ё", "е")
    text = re.sub(r'«[^»]*»|"[^"]*"', " ", text)
    text = re.sub(
        r"(?:^|[!?;\n]|\.(?!\d))[^.!?;\n]*не\s+"
        r"(?:утвержда\w*|говор\w*|сообща\w*|заявля\w*)\s*,?\s*что\b[^.!?;\n]*",
        " ",
        text,
    )
    months = "января февраля марта апреля мая июня июля августа сентября октября ноября декабря".split()

    def date_text(day, month, year):
        return (
            f"{int(day)} {months[int(month) - 1]} {year} года"
            if 1 <= int(month) <= 12
            else f"{day}.{month}.{year}"
        )

    text = re.sub(
        r"\b(\d{2})[./](\d{2})[./](\d{4})\b", lambda m: date_text(*m.groups()), text
    )
    text = re.sub(
        r"\b(\d{4})-(\d{2})-(\d{2})\b", lambda m: date_text(m[3], m[2], m[1]), text
    )
    return re.sub(r"(?<=\d)[\s\u00a0\u202f\u2009]+(?=\d)", "", text)


def deposit_reply_is_complete(text, as_of="2026-08-28"):
    """Require payout meanings rather than an unordered bag of numeric tokens."""
    own = _own_text(text)
    if any(re.search(p, text, re.I) for p in invalid_deposit_claim_patterns().values()):
        return False
    values = {family: [] for family in EXPECTED}
    family = None
    for clause in re.split(r"[!?;\n]|\.(?!\d)", own):
        if re.search(r"разниц\w*|потер\w*|недополуч\w*", clause):
            family = "difference"
            prefix = LABELS[family]
        elif re.search(MATURITY, clause) or re.search(EARLY, clause):
            markers = [
                (m.start(), kind)
                for kind, pattern in (("early", EARLY), ("maturity", MATURITY))
                for m in re.finditer(pattern, clause)
            ]
            payout = re.search(PAYOUT, clause)
            preceding = [m for m in markers if payout is None or m[0] <= payout.start()]
            # A product header can name the maturity date before describing an
            # early payout. Its date must not override the nearer payout label.
            family = max(preceding or markers)[1]
            summary = (
                EARLY_SUMMARY
                if family == "early"
                else rf"{MATURITY}\s*(?:года)?\s*[—:,-]\s*"
            )
            prefix = rf"(?:{PAYOUT}{BODY}{{0,45}}?|{summary})"
        else:
            prefix = rf"{PAYOUT}{BODY}{{0,45}}?"
        if family is not None:
            for match in re.finditer(
                prefix + rf"(?P<value>{NUMBER})\s*{CURRENCY}", clause
            ):
                values[family].append(
                    Decimal(re.sub(r"\s", "", match["value"]).replace(",", "."))
                )
        if family == "difference":
            for match in re.finditer(
                rf"(?P<value>{NUMBER})\s*{CURRENCY}{DIFFERENCE_AFTER}", clause
            ):
                values[family].append(
                    Decimal(re.sub(r"\s", "", match["value"]).replace(",", "."))
                )
    for family, found in values.items():
        if not found or any(value != Decimal(EXPECTED[family]) for value in found):
            return False
    if not re.search(
        r"до\s+налогооблож|до\s+(?:(?:возможного|вычета|удержания|уплаты)\s+)?(?:налог|ндфл)|без\s+учета\s+налог",
        own,
    ):
        return False
    # Any explicit calculation date must agree with the received system snapshot.
    year, month, day = map(int, as_of.split("-"))
    months = "января февраля марта апреля мая июня июля августа сентября октября ноября декабря".split()
    calculation_date_found = False
    for match in re.finditer(
        r"(?:сегодня|по\s+состоянию\s+на|на(?:\s+дату\s+расчета)?)\s+(\d{1,2})\s+("
        + "|".join(months)
        + r")\s+(\d{4})(?:\s+года)?",
        own,
    ):
        d, name, y = match.groups()
        tail = own[match.end() : match.end() + 60]
        if (
            own[match.start() : match.end()].startswith(("сегодня", "по состоянию"))
            or re.search(EARLY, tail)
            or re.search(
                r"(?:расчет\w*|рассчитал\w*).{0,80}$",
                own[max(0, match.start() - 80) : match.start()],
            )
        ):
            if (int(y), months.index(name) + 1, int(d)) != (year, month, day):
                return False
            calculation_date_found = True
    if not calculation_date_found:
        return False
    return bool(
        re.search(
            r"не\s+(?:закры|расторг|провод|оформ)|остает\w*\s+(?:открыт|актив)"
            r"|закрытие.{0,60}не\s+(?:выполня|провод)|без\s+закрытия",
            own,
        )
    )
