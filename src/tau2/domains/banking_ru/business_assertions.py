"""Private banking checks of business state, independent of customer prose."""


class BankingBusinessAssertions:
    """Business predicates used by evaluation, never public MCP tools."""

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
            and c.escalation_request is not None
            and c.escalation_request.type == "card_claim_referral"
            and c.transaction_id == "txn_540117"
            and any(c.case_id == statement.id for statement in scoped)
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
