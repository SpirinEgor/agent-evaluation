"""Private bounded reply examples for the final banking wave."""

import json
import re

from tau2.domains.banking_ru.first_five import clauses
from tau2.domains.banking_ru.second_five import amount
from tau2.domains.banking_ru.utils import BANKING_RU_DATA_DIR


class FinalWaveAssertions:
    """Excluded from the public MCP catalog."""

    def assert_final_wave_reply(self, customer_id: str, bank_number: int) -> bool:
        checks = json.loads(
            (BANKING_RU_DATA_DIR / "final_reply_checks.json").read_text()
        )[str(bank_number)]
        text, _ = clauses(self.db.ticket_replies.get(customer_id, ""))
        return all(amount(text, v) for v in checks["amounts"]) and all(
            re.search(p, text, flags=re.IGNORECASE) for p in checks["reply_patterns"]
        )
