"""Private, finite reply checks for the single-problem Bank 6–10 cases."""

import re

from tau2.domains.banking_ru.autopayment_reply import autopayment_reply_is_complete
from tau2.domains.banking_ru.deposit_reply import deposit_reply_is_complete
from tau2.domains.banking_ru.first_five import clauses
from tau2.domains.banking_ru.transfer_fee_reply import transfer_fee_reply_is_complete


def amount(text, value):
    """Recognize a stated decimal amount, with ordinary grouping separators."""
    value = str(value).replace(".", r"[.,]")
    return bool(re.search(r"(?<![\d.,])" + value + r"(?!\d)", text))


class SecondFiveAssertions:
    """These assertions are excluded from the public MCP catalog."""

    def assert_bank_6_reply(self, customer_id: str) -> bool:
        return transfer_fee_reply_is_complete(
            self.db.ticket_replies.get(customer_id, "")
        )

    def assert_bank_7_reply(self, customer_id: str) -> bool:
        return autopayment_reply_is_complete(
            self.db.ticket_replies.get(customer_id, "")
        )

    def assert_bank_8_reply(self, customer_id: str) -> bool:
        return deposit_reply_is_complete(
            self.db.ticket_replies.get(customer_id, ""), self.db.today
        )

    def assert_bank_9_reply(self, customer_id: str) -> bool:
        text, _ = clauses(self.db.ticket_replies.get(customer_id, ""))
        return bool(
            "2290" in text
            and re.search(r"украд|краж|stolen", text)
            and re.search(
                r"разблок.{0,35}(?:нельзя|невозмож)|(?:нельзя|невозмож).{0,35}разблок|не\s+подлежит\s+разблок|блокировк.{0,20}снять\s+нельзя",
                text,
            )
            and re.search(r"перевыпуск|новая\s+карта|новую\s+карту", text)
            and re.search(
                r"не\s+(?:заказ|оформ)|после.{0,30}соглас|с\s+вашего\s+соглас", text
            )
        )

    def assert_bank_10_reply(self, customer_id: str) -> bool:
        text, _ = clauses(self.db.ticket_replies.get(customer_id, ""))
        return bool(
            amount(text, 1200)
            and amount(text, 900)
            and re.search(r"штраф|послаблен", text)
            and re.search(
                r"один\s+раз|одно\s+послаблен|единствен|исчерпан|использован", text
            )
            and re.search(
                r"не\s+(?:спис|отмен|могу\s+спис)|(?:спис|отмен).{0,45}(?:нельзя|невозмож|не\s+можем)|отказ",
                text,
            )
            and re.search(r"премиум|премиальн", text)
        )
