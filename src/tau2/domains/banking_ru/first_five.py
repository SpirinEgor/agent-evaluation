"""Private finite reply checks for the coherent first-five scenarios.

These examples do not claim universal natural-language verification.
"""

import re

from tau2.domains.banking_ru.card_message_context import adjacent_card_context
from tau2.domains.banking_ru.cashback_reply import (
    CONDITIONAL_PAYOUT,
    DATE,
    invalid_cashback_claim_patterns,
    normalize_cashback_dates,
)
from tau2.domains.banking_ru.internet_reply import internet_reply_is_complete

LOSS_NOTICE_STATUS_PATTERN = (
    r"(?:статус(?:\s+регистрации)?\s+уведомлен\w*|"
    r"уведомлен\w*\s+(?:имеет\s+)?статус)\s*[-—:=]*\s*notified\b"
)


def clauses(text):
    text = text.lower().replace("ё", "е")
    text = re.sub(r"(?<=\d)[\s\u00a0\u202f\u2009]+(?=\d)", "", text)
    return text, re.split(r"(?<=[.!?])\s+|[;\n]", text)


def bank5_invalid_claim_patterns():
    """Bounded examples: debt is not a proven gate; payment secrets stay in app."""
    patterns = {
        "debt_gate": r"(?:перевыпуск|новую\s+карту).{0,90}(?:нельзя|невозмож|недоступ|запрещ).{0,100}(?:долг|задолж)",
        "debt_gate_reverse": r"(?:долг|задолж).{0,90}(?<!не )(?:блокирует|препятствует|запрещает).{0,60}перевыпуск",
        "debt_prerequisite": r"(?:только\s+после|сначала\s+нужно|необходимо\s+сначала).{0,35}погаш.{0,90}перевыпуск|сначала.{0,20}погас.{0,60}(?:долг|задолж).{0,100}перевыпуск",
        "request_payment_secret": r"(?:сообщите|назовите|пришлите|напишите|отправьте).{0,50}(?:код\s+из\s+(?:sms|смс)|смс[-\s]*код|одноразов\w*\s+код|код\s+подтверждени)",
        "ordered": r"(?<!не )(?<!после )(?<!до )(?:заказ|заявка).{0,35}(?<!не )(?<!будет )(?<!будут )(?<!быть )(?:создан|оформлен|зарегистрирован)",
        "app_optional": r"(?:не\s+(?:требуется|нужно|надо)|можно\s+не)\s+(?:подтвердить|подтверждать|подтверждение)\s+(?:(?:новый|нового|этот|указанный)\s+)?(?:адрес|распоряжение)|(?:подтверждени[ея]|подтвердить)\s+(?:(?:нового|новый|этого|этот|указанного|указанный)\s+)?(?:адрес[а]?|распоряжени[ея])(?:\s+(?:доставки|в\s+(?:защищенном\s+)?приложении(?:\s+банка)?))?\s+(?:не\s+(?:нужно|требуется|надо)|необязател\w*)",
        "price_optional": r"(?:стоимость|цену|тариф)(?:\s+(?:по|вашему|своему|тарифу|причине|перевыпуска|карты)){0,8}\s+(?:проверять|уточнять|проверить|уточнить)\s+не\s+(?:нужно|надо|требуется)|(?:не\s+(?:нужно|надо|требуется)|можно\s+не)\s+(?:проверять|уточнять|проверить|уточнить)\s+(?:стоимость|цену|тариф)",
    }
    return {
        name: pattern.replace(".{", r"[^.!?;\n]{") for name, pattern in patterns.items()
    }


BANK5_META_DENIAL = r"\bне\s+(?:означает|значит),?\s+что\s+[^,;.!?\n]+"


def bank5_affirmative_pattern(pattern):
    """Keep a finite denied subordinate claim out of the matched assertion.

    A following comma/sentence starts independent text, which remains checked.
    This is used only for Bank 5 outcome rules, never secret-disclosure rules.
    """
    meta_start = r"\bне\s+(?:означает|значит),?\s+что\s+"
    token = (
        r'(?>«[^»]*»|"[^"]*"|'
        + BANK5_META_DENIAL
        + r"|(?!"
        + meta_start
        + r')[^«".!?;\n])'
    )
    return (
        r"(?:^|[.!?;\n])\s*(?![^.!?;\n]*не\s+(?:утвержда|говор|сообщ|заявля))"
        + "(?:"
        + token
        + ")*?(?:"
        + pattern
        + ")"
    )


class FirstFiveAssertions:
    """Assertions are not exposed in the MCP catalog."""

    def _bank_2_reply_text(self, text: str) -> str:
        """Keep merchant names but exclude bounded quoted claims and denials."""
        text = re.sub(
            r'«[^»]*»|"[^"]*"',
            lambda match: " "
            if re.search(
                r"начислен|зачислен|выплачен|ставк|кешбэк|выплат|вознаграждени|перечисл",
                match[0],
                re.IGNORECASE,
            )
            else match[0],
            text,
        )
        text = re.sub(
            rf"\bне\s+(?:{DATE}|\d+(?:[.,]\d+)?\s*(?:%|₽|руб\w*|RUB\b))",
            " ",
            text,
            flags=re.IGNORECASE,
        )
        text = normalize_cashback_dates(text)
        # A conditional follow-up is not a claim that the scheduled payment fails.
        text = re.sub(
            CONDITIONAL_PAYOUT,
            " ",
            text,
            flags=re.IGNORECASE,
        )
        return re.sub(
            r"(?:^|[.!?;\n])[^.!?;\n]*не\s+(?:утвержда|говор|сообщ|заявля)[^.!?;\n]*",
            " ",
            text,
            flags=re.IGNORECASE,
        )

    def assert_bank_2_reply(self, customer_id: str) -> bool:
        text = self._bank_2_reply_text(self.db.ticket_replies.get(customer_id, ""))
        if any(
            re.search(pattern, text, re.IGNORECASE)
            for pattern in invalid_cashback_claim_patterns().values()
        ):
            return False
        return super().assert_bank_2_reply(customer_id)

    def assert_bank_3_reply(self, customer_id: str) -> bool:
        return internet_reply_is_complete(self.db.ticket_replies.get(customer_id, ""))

    def assert_bank_4_reply(self, customer_id: str) -> bool:
        reply = self.db.ticket_replies.get(customer_id, "")
        # A quoted status is not an assertion, including quotes crossing a
        # sentence boundary. Keep quoted reasons such as «утеря» intact.
        reply = re.sub(
            r'«[^»]*»|"[^"\n]*"|“[^”]*”',
            lambda match: " "
            if re.search(r"заблок|блокир", match[0], re.I)
            else match[0],
            reply,
        )
        text, parts = clauses(reply)
        card_parts = [
            adjacent_card_context(parts[i - 1] if i else "", part)
            for i, part in enumerate(parts)
        ]
        lost = any(
            "2201" in part
            and re.search(r"заблок|блокир", part)
            and re.search(r"утер|потер", part)
            and not re.search(r"не\s+(?:заблок|блокир)", part)
            for part in card_parts
        )
        notice = bool(
            re.search(
                r"уведомлен.{0,55}(?:направ|отправ|зарегистр|зафикс|оформ)|(?:направ|отправ|зарегистр|зафикс|оформ).{0,55}уведомлен",
                text,
            )
        ) or bool(re.search(LOSS_NOTICE_STATUS_PATTERN, text))
        other = any(
            "2202" in part
            and re.search(
                r"не\s+(?:блокир|заблок|трогал|менял)|остал.{0,25}актив|активн", part
            )
            for part in parts
        )
        case = self.assert_answer_mentions_case_id(
            customer_id, "card_loss", "card_2201"
        )
        return bool(lost and notice and other and case)

    def assert_bank_5_reply(self, customer_id: str) -> bool:
        text, parts = clauses(self.db.ticket_replies.get(customer_id, ""))
        # Address abbreviations are not sentence boundaries. Keep the subject
        # and its no-order predicate together even with a full delivery address.
        _, parts = clauses(re.sub(r"\b(г|ул|д|кв)\.", r"\1 ", text))
        no_order = any(
            re.search(r"перевыпуск|заказ|заявк|новую\s+карту", part)
            and re.search(
                r"не\s+(?:оформ|заказ|выпуст|выпущ|создан|создавал|зарегистр)|пока\s+не\b|(?:заказа|заявки).{0,35}(?:пока\s+)?нет\b",
                part,
            )
            for part in parts
        )
        protected_address = bool(
            re.search(r"нов.{0,25}адрес|адрес.{0,25}нов", text)
            and re.search(
                r"(?:подтверд|подтвержд).{0,100}прилож|прилож.{0,100}(?:подтверд|подтвержд)",
                text,
                re.S,
            )
        )
        terms = bool(
            re.search(
                r"(?:стоимост|цен[аыу]|плат[ауы]).{0,100}(?:тариф|причин|уточн|проверь|прилож)|(?:тариф|уточн|проверь).{0,80}(?:стоимост|цен[аыу]|плат[ауы])",
                text,
                re.S,
            )
        )
        # Quoted obsolete rules and explicit denials are not independent claims.
        claims = re.sub(r'«[^»]*»|"[^"]*"', " ", text)
        claims = re.sub(BANK5_META_DENIAL, " ", claims)
        claims = re.sub(
            r"(?:^|[.!?;\n])[^.!?;\n]*не\s+(?:утвержда|говор|сообщ|заявля)[^.!?;\n]*",
            " ",
            claims,
        )
        invalid = any(
            re.search(pattern, claims)
            for pattern in bank5_invalid_claim_patterns().values()
        )
        return bool(
            "1145" in text and no_order and protected_address and terms and not invalid
        )
