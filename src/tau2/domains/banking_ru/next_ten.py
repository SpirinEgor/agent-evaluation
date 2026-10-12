"""Private finite reply checks for the coherent Bank 11–20 scenarios."""

import re

from tau2.domains.banking_ru.first_five import clauses
from tau2.domains.banking_ru.second_five import amount


class NextTenAssertions:
    """These predicates are evaluation internals, not public MCP tools."""

    def _reply(self, customer_id):
        return clauses(self.db.ticket_replies.get(customer_id, ""))[0]

    def _correct_explicit_today(self, text):
        """Check bounded explicit dates; hypothetical dates remain permitted."""
        text = re.sub(r'«[^»]*»|"[^"]*"', " ", text)
        text = re.sub(
            r"(?:^|(?<=[.!?])\s+|[;\n])[^;\n]*?не\s+(?:утвержда|говор|сообщ|заявля).*?(?=[.!?]\s|[;\n]|$)",
            " ",
            text,
        )
        year, month, day = map(int, str(self.db.today).split("-"))
        prefix = r"\bсегодня[\s,:—-]*"
        for match in re.finditer(prefix + r"(\d{1,2})[./](\d{1,2})[./](\d{4})", text):
            d, m, y = map(int, match.groups())
            if (y, m, d) != (year, month, day):
                return False
        for match in re.finditer(prefix + r"(\d{4})-(\d{2})-(\d{2})", text):
            if tuple(map(int, match.groups())) != (year, month, day):
                return False
        months = {
            name: index
            for index, name in enumerate(
                "января февраля марта апреля мая июня июля августа сентября октября ноября декабря".split(),
                1,
            )
        }
        for match in re.finditer(
            prefix + r"(\d{1,2})\s+(" + "|".join(months) + r")(?:\s+(\d{4}))?", text
        ):
            d, name, y = match.groups()
            if (int(y or year), months[name], int(d)) != (year, month, day):
                return False
        return True

    def assert_bank_11_reply(self, customer_id: str) -> bool:
        text = self._reply(customer_id)
        return bool(
            all(amount(text, v) for v in [18990, 3500, 15490])
            and "120" in text
            and "30" in text
            and re.search(r"14\s+декабр|2026-12-14|14\.12", text)
            and re.search(r"не\s+(?:откры|подан|зарегистр)|пока\s+нет\s+спор", text)
            and re.search(r"фактическ|дат\w*\s+подачи|после\s+подачи", text)
            and self._correct_explicit_today(text)
        )

    def assert_bank_12_referral(self, customer_id: str) -> bool:
        """One scoped statement, optionally followed by one human handoff.

        Route labels are internal alternatives; a bare escalation without the
        purchase link cannot replace registration of the customer's statement.
        DB comparison preserves other customers, products, orders and money.
        Receipt, ownership and all-attempt constraints remain in the grader.
        """
        cases = [c for c in self.db.cases.values() if c.customer_id == customer_id]
        scoped = [
            c
            for c in cases
            if c.category in {"merchant_investigation", "other", "escalation"}
            and c.transaction_id == "txn_540117"
            and c.amount == 7450.0
            and c.status == "open"
            and c.created_at == self.db.today
        ]
        handoffs = [
            c
            for c in cases
            if c.category == "escalation"
            and c.transaction_id is None
            and c.amount is None
            and c.status == "open"
            and c.created_at == self.db.today
        ]
        if len(scoped) != 1 or len(handoffs) > 1:
            return False
        if len(cases) != len(scoped) + len(handoffs):
            return False
        if handoffs:
            log = self._successful_tool_calls
            registrations = [i for i, name in enumerate(log) if name == "create_case"]
            transfers = [i for i, name in enumerate(log) if name == "escalate_to_human"]
            if (
                not registrations
                or not transfers
                or min(transfers) < max(registrations)
            ):
                return False
        return True

    def assert_bank_12_reply(self, customer_id: str) -> bool:
        text = self._reply(customer_id)
        return bool(
            amount(text, 7450)
            and "120" in text
            and re.search(r"20\s+феврал|2026-02-20|20\.02", text)
            and re.search(r"истек|прошел|пропущен|старше", text)
            and re.search(
                r"не\s+(?:откры|подан|зарегистр)|(?:откры|оспор|зарегистр).{0,45}(?:нельзя|невозмож|не\s+могу|не\s+можем)",
                text,
            )
        )

    def assert_bank_13_reply(self, customer_id: str) -> bool:
        text = self._reply(customer_id)
        return bool(
            "dsp_3400" in text
            and amount(text, 32000)
            and "10" in text
            and re.search(r"12\s+август|2026-08-12|12\.08", text)
            and re.search(r"истек|прошел|позже|пропущен", text)
            and re.search(
                r"не\s+(?:отозв|закры)|(?:отозв|отзыв).{0,30}(?:нельзя|невозмож)", text
            )
            and re.search(r"рассмотрени|доведен.{0,15}решени|довод.{0,15}решени", text)
        )

    def assert_bank_14_reply(self, customer_id: str) -> bool:
        text = self._reply(customer_id)
        return bool(
            amount(text, 3000)
            and "яндекс" in text
            and re.search(r"активн|действующ", text)
            and re.search(r"подписк|сервис", text)
            and re.search(
                r"не\s+(?:откры|отключ|отмен|менял|измен)|без\s+изменени", text
            )
        )

    def _incident_reply(self, customer_id, last4, total, values):
        text = self._reply(customer_id)
        return bool(
            last4 in text
            and all(amount(text, v) for v in [total, *values])
            and re.search(r"заблокир", text)
            and re.search(r"обращен|заявлен", text)
            and re.search(r"case_\d+", text)
            and re.search(
                r"не\s+(?:возвращ|вернул)|пока.{0,20}не\s+вернул|(?:передан|начат).{0,25}расследован",
                text,
            )
            and re.search(
                r"не\s+гарант|гарантир\w*.{0,25}(?:нельзя|невозмож)|решени.{0,25}(?:провер|расслед)",
                text,
            )
        )

    def assert_bank_15_reply(self, customer_id: str) -> bool:
        return self._incident_reply(customer_id, "8850", 170000, [95000, 45000, 30000])

    def assert_bank_16_reply(self, customer_id: str) -> bool:
        return self._incident_reply(customer_id, "4478", 16500, [12400, 4100])

    def assert_bank_17_reply(self, customer_id: str) -> bool:
        text = self._reply(customer_id)
        return bool(
            amount(text, 4200)
            and re.search(r"холд|резерв|блокиров", text)
            and re.search(r"снят|снял|освобож|освобод", text)
            and re.search(r"просроч|27\s+август|2026-08-27", text)
            and re.search(
                r"(?:оплат|покупк).{0,35}(?:остает|проведен)|не\s+возврат", text
            )
        )

    def assert_bank_18_reply(self, customer_id: str) -> bool:
        text = self._reply(customer_id)
        return bool(
            all(amount(text, v) for v in [50000, 12000, 38000])
            and re.search(r"обращен|заявлен", text)
            and re.search(r"case_\d+", text)
            and re.search(
                r"(?:не|нельзя).{0,35}отмен|односторонн.{0,35}не\s+отмен", text
            )
            and re.search(r"не\s+гарант|гарантир\w*.{0,25}(?:нельзя|невозмож)", text)
        )

    def assert_bank_19_reply(self, customer_id: str) -> bool:
        text = self._reply(customer_id)
        return bool(
            all(amount(text, v) for v in [100000, 150000, 300000, 500000])
            and "сбп" in text
            and "lrq_5581" in text
            and re.search(r"рассмотрени|pending", text)
            and re.search(
                r"не\s+(?:менял|меняла|измен|повыш)|(?:менять|повысить|изменить).{0,20}нельзя",
                text,
            )
        )

    def assert_bank_20_reply(self, customer_id: str) -> bool:
        text = self._reply(customer_id)
        return bool(
            amount(text, 10000)
            and "4417" in text
            and re.search(r"26\s+август|2026-08-26|26\.08", text)
            and re.search(r"31\s+август|2026-08-31|31\.08", text)
            and re.search(r"обработк|processing", text)
            and re.search(
                r"не\s+(?:зачислен|поступил|дошел)|еще\s+не|в\s+обработке", text
            )
            and re.search(
                r"не\s+(?:оформ|созда|откры)|(?:не\s+нужн|не\s+требует|нет\s+основан)",
                text,
            )
        )
