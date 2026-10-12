"""Bounded adjacent-sentence card references, within one outgoing utterance."""

import re


def adjacent_card_context(previous: str, sentence: str) -> str:
    """Bind bare 'Карта заблокирована' to one immediately preceding last4.

    This is only a resource selector. It never changes the claim's emission
    position or supplies an operation result. Ambiguous/quoted antecedents and
    an explicit selector in the new sentence are left to the normal extractor.
    """
    if not re.match(r"\s*карта\s+(?:заблок|разблок)", sentence, re.I):
        return sentence
    if re.search(r"\d|\bcard_", sentence, re.I):
        return sentence
    if (
        previous.count("«") != previous.count("»")
        or previous.count("“") != previous.count("”")
        or previous.count('"') % 2
    ):
        return sentence
    selectors = list(re.finditer(r"\bкарт\w*\s+\*(\d{4})(?!\d)", previous, re.I))
    if len(selectors) != 1 or len(re.findall(r"\bкарт\w*", previous, re.I)) != 1:
        return sentence
    if any(
        quoted.start() <= selectors[0].start() < quoted.end()
        for quoted in re.finditer(r'«[^»]*»|"[^"\n]*"|“[^”]*”', previous)
    ):
        return sentence
    if len(re.findall(r"\*\d{4}(?!\d)", previous)) != 1:
        return sentence
    return previous + " " + sentence
