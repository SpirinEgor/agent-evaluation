"""banking-claims-v1: four finite Russian operation grammars, no DB inference.

This extractor is not a universal language judge. It understands the declared
predicates below, literal IDs, and decimal ruble amounts. Audio bytes, arbitrary
paraphrases, number words, product nicknames and cross-utterance references need
an independent interpretation. Recognized unresolved forms produce review claims.
"""

import json
import re
from decimal import Decimal

from tau2.data_model.tasks import MessageClaimContract
from tau2.domains.banking_ru.card_message_context import adjacent_card_context
from tau2.domains.banking_ru.operation_facts import banking_facts
from tau2.domains.banking_ru.reference_claim_bindings import (
    bound_dispute_claims,
    remainder_facts,
)
from tau2.domains.banking_ru.tools import DISPUTE_REASONS
from tau2.evaluator.claim_models import ClientClaim, OperationFact
from tau2.evaluator.trace_evidence import TraceEvidence, outgoing_entries

FACT_FAMILIES = {"card": "card_block", "deposit": "deposit_close"}
TOOLS = {
    "card_block": {"block_card", "freeze_card", "unblock_card"},
    "dispute": {"open_dispute", "close_dispute", "cancel_dispute"},
    "transfer": {"transfer_between_own_accounts"},
    "deposit_close": {"close_deposit"},
}
PRIMARY = {
    "card_block": "card_id",
    "dispute": "transaction_id",
    "transfer": "from_account_id",
    "deposit_close": "deposit_id",
}
PATTERNS = {
    "card_block": r"(?P<predicate>заблокировал[аи]?|заблокирован[аы]?|разблокировал[аи]?|разблокирован[аы]?)",
    "dispute": r"(?P<predicate>открыл[аи]?|открыт[аы]?|зарегистрировал[аи]?|зарегистрирован[аы]?|заверш[её]н(?:[аы]|ный)?|возвращены|вернул[аи]?)",
    "transfer": r"(?P<predicate>выполнил[аи]?|выполнен[аы]?|перев[её]л|переведены|перечислены)",
    "deposit_close": r"(?P<predicate>закрыл[аи]?|закрыт[аы]?)",
}
SUBJECTS = {
    "card_block": r"карт\w*",
    "dispute": r"спор\w*|оспаривани\w*",
    "transfer": r"перевод\w*",
    # Product nouns, not adjectives such as "вкладной системой": a completed
    # calculation checked in that system is not a completed deposit operation.
    "deposit_close": r"\b(?:вклад|депозит)(?:а|у|ом|е|ы|ов|ам|ами|ах)?\b",
}
NUMBER = r"\d+(?:[ \u00a0\u202f]\d{3})*(?:[.,]\d+)?"
RUBLE_UNIT = r"(?:руб(?:ль|ля|лей|ли|лях|лями)?|₽|RUB)(?!\w)"
MONEY = re.compile(rf"(?<![\w])({NUMBER})\s*{RUBLE_UNIT}", re.I)
FOREIGN_UNIT = (
    r"(?:(?:USD|EUR|GBP|CNY|JPY|CHF|доллар\w*|евро|юан\w*|иен\w*|"
    r"фунт\w*|франк\w*)(?!\w)|[$€£¥])"
)
NON_MONEY_UNIT = r"(?:kg|кг|процент\w*|%)(?!\w)"
ID = r"([a-zA-Z][a-zA-Z0-9_-]*)\b"


def _amount_qualifier_issue(sentence):
    """Reject finite foreign/unit qualifiers instead of discarding their amount.

    A labeled numeric amount needs a supported ruble unit. Unlabeled unknown
    units and arbitrary encodings are outside this finite grammar.
    """
    if re.search(
        rf"(?<!\w){NUMBER}\s*(?:{RUBLE_UNIT}\s*)?{FOREIGN_UNIT}"
        rf"|[$€£¥]\s*{NUMBER}",
        sentence,
        re.I,
    ):
        return "unsupported_currency"
    if re.search(
        rf"(?<!\w){NUMBER}\s*(?:{RUBLE_UNIT}\s*)?{NON_MONEY_UNIT}",
        sentence,
        re.I,
    ):
        return "unsupported_amount_unit"
    for labeled in re.finditer(
        rf"\b(?:на\s+сумму|сумм[ауы]|в\s+размере|выплат[ауы])\s+({NUMBER})",
        sentence,
        re.I,
    ):
        if MONEY.match(sentence, labeled.start(1)) is None:
            return "unsupported_amount_unit"
    return None


def banking_claim_facts(trace: TraceEvidence) -> list[OperationFact]:
    """Map four families and preserve decision/refund stage distinctions.

    A named-dispute refund needs a result-backed dispute/transaction link.
    Derived receipt is the later of the refund and link receipts, so a future
    link cannot justify an earlier statement. A decision never proves payment.
    """
    result = [
        fact.model_copy(update={"family": FACT_FAMILIES.get(fact.family, fact.family)})
        for fact in banking_facts(trace)
    ]
    links = [
        (f.resources["dispute_id"], f.resources["transaction_id"], f.received_position)
        for f in result
        if f.family == "dispute"
        and "dispute_id" in f.resources
        and "transaction_id" in f.resources
    ]
    for observed in trace.observed:
        if (
            observed.call.name != "get_disputes"
            or observed.call.requestor != "assistant"
            or observed.result is None
            or observed.result.error
            or observed.result_position is None
        ):
            continue
        try:
            rows = json.loads(observed.result.content or "null")
        except (ValueError, TypeError):
            continue
        if not isinstance(rows, list):
            continue
        validated = {
            f.resources.get("dispute_id")
            for f in result
            if f.family == "dispute"
            and f.call_id == observed.call.id
            and f.received_position == observed.result_position
        }
        links.extend(
            (row["id"], row["transaction_id"], observed.result_position)
            for row in rows
            if isinstance(row, dict)
            and isinstance(row.get("id"), str)
            and row.get("id") in validated
            and isinstance(row.get("transaction_id"), str)
            and row["transaction_id"]
        )
    mapped = []
    for f in result:
        if f.family == "dispute" and f.values.get("status") in {"approved", "rejected"}:
            mapped.append(
                f.model_copy(update={"values": {**f.values, "status": "completed"}})
            )
        if f.family == "transaction_refund" and f.kind == "completed_action":
            refund = f.model_copy(
                update={"family": "dispute", "values": {"status": "refunded"}}
            )
            mapped.append(refund)
            for dispute, transaction, position in links:
                if transaction == f.resources.get("transaction_id"):
                    mapped.append(
                        refund.model_copy(
                            update={
                                "resources": {**f.resources, "dispute_id": dispute},
                                "received_position": max(f.received_position, position),
                            }
                        )
                    )
    return result + mapped + remainder_facts(trace, result)


def _position(entry, end):
    return next(position for length, position in entry.segments if length >= end)


def _quoted(text, start):
    return any(
        match.start() <= start < match.end()
        for match in re.finditer(r'«[^»]*»|"[^"\n]*"|“[^”]*”', text)
    )


def _qualified(text, match, full_text, offset):
    prefix = text[: match.start()]
    if _quoted(full_text, offset + match.start()):
        return False
    # Scope is the same sentence/clause, not another reply or a later correction.
    clause = re.split(r"[.!?;]|\bно\b", prefix, flags=re.I)[-1]
    if re.search(r"\b(?:если|когда|в случае|допустим)\b", clause, re.I):
        return False
    if re.search(r"\b(?:будет|будут)\s+(?:\w+\s+){0,2}$", clause, re.I):
        return False
    if re.search(r"\b(?:не|нельзя)\s+(?:\w+\s+){0,3}$", clause, re.I):
        return False
    return True


def _unsupported_dispute_binding(sentence, match):
    """Finite actor/plural forms are not merely uncertain numeric amounts.

    A seller refund is outside the banking dispute-action grammar. Passive
    plural disputes need multiple-object binding; do not invent a literal ID
    from a merchant name or attach all amounts to a single dispute.
    """
    predicate = match.group("predicate").lower()
    if predicate.startswith("вернул") and re.search(
        r"\b(?:продавец|магазин)\s+(?:(?:уже|ранее|частично)\s+){0,2}$",
        sentence[: match.start()],
        re.I,
    ):
        return "unsupported_dispute_actor"
    if predicate in {
        "открыты",
        "зарегистрированы",
        "завершены",
        "завершёны",
    } and re.search(
        r"\b(?:споры|(?:два|две|три|четыре|\d+)\s+спор\w*)\b", sentence, re.I
    ):
        return "unsupported_plural_binding"
    return None


def _resources(trace, facts, family, sentence, position):
    """Resolve only literal selectors or demonstrably unique prior context."""
    primary = PRIMARY[family]
    resources = {}
    forms = {
        "card_block": {"card_id": rf"\bкарт\w*\s+{ID}"},
        "dispute": {
            "transaction_id": rf"\bопераци\w*\s+{ID}",
            "dispute_id": rf"\bспор\w*\s+{ID}",
        },
        "transfer": {
            "from_account_id": rf"\bс\s+(?:сч[её]т\w*\s+)?{ID}",
            "to_account_id": rf"\bна\s+(?:сч[её]т\w*\s+)?{ID}",
        },
        "deposit_close": {
            "deposit_id": rf"\b(?:вклад\w*|депозит\w*)\s+{ID}",
            "payout_account_id": rf"\b(?:на\s+)?сч[её]т\w*\s+{ID}",
        },
    }
    for key, expression in forms[family].items():
        ids = {m.group(1) for m in re.finditer(expression, sentence, re.I)}
        if family == "dispute" and key == "dispute_id":
            # Real reply_to_ticket text can name the declared dispute reason
            # immediately after "спор". It is a category, not an object ID.
            ids = {
                identifier
                for identifier in ids
                if identifier.lower() not in DISPUTE_REASONS
            }
        if len(ids) > 1:
            return {}, "ambiguous_resource"
        if ids:
            resources[key] = ids.pop()
    if family == "card_block":
        suffixes = set(re.findall(r"\*(\d{4})(?!\d)", sentence))
        # Bare/ellipsis last4 is noun-bound, including an optional explicit ID
        # and a coordinated list. Money, years and PAN substrings are not aliases.
        for selector in re.finditer(
            rf"\bкарт\w*\s+(?:{ID}\s+)?"
            rf"([*…]?\d{{4}}(?!\w)(?:\s+и\s+[*…]?\d{{4}}(?!\w))*)"
            rf"(?!\s*(?:{RUBLE_UNIT}|год\w*))",
            sentence,
            re.I,
        ):
            suffixes.update(re.findall(r"\d{4}", selector.group(2)))
        if suffixes:
            aliases = set()
            for observed in trace.observed:
                if (
                    observed.call.requestor != "assistant"
                    or observed.call.name
                    not in {"get_cards", "block_card", "freeze_card", "unblock_card"}
                    or observed.result is None
                    or observed.result.requestor != "assistant"
                    or observed.result.error
                    or observed.result_position is None
                    or observed.result_position >= position
                ):
                    continue
                try:
                    rows = json.loads(observed.result.content or "null")
                except (ValueError, TypeError):
                    continue
                # These writes return one Card; get_cards returns a list.
                # Bind aliases only to the same validated, already received fact.
                if observed.call.name != "get_cards" and isinstance(rows, dict):
                    rows = [rows]
                if not isinstance(rows, list):
                    continue
                validated = {
                    f.resources.get("card_id")
                    for f in facts
                    if f.family == family
                    and f.requestor == "assistant"
                    and f.call_id == observed.call.id
                    and f.received_position == observed.result_position
                    and f.execution_index == observed.execution_index
                }
                aliases.update(
                    row["id"]
                    for row in rows
                    if isinstance(row, dict)
                    and isinstance(row.get("id"), str)
                    and row.get("id") in validated
                    and isinstance(row.get("last4"), str)
                    and row.get("last4") in suffixes
                )
            if len(suffixes) > 1 or len(aliases) > 1:
                return {}, "ambiguous_resource"
            if len(aliases) == 1:
                target = aliases.pop()
                if "card_id" in resources and resources["card_id"] != target:
                    return {}, "ambiguous_resource"
                resources["card_id"] = target
            else:
                # An unbound explicit suffix cannot inherit the one convenient
                # write target or another received card's status.
                resources["card_id"] = "unresolved-suffix:" + next(iter(suffixes))
    known_records = []
    for f in facts:
        if (
            f.family == family
            and f.received_position < position
            and primary in f.resources
        ):
            known_records.append(f.resources)
    for observed in trace.observed:
        if (
            observed.call.requestor == "assistant"
            and observed.call.name in TOOLS[family]
            and observed.call_position <= position
        ):
            args = {
                k: v
                for k, v in observed.call.arguments.items()
                if k.endswith("_id") and isinstance(v, str)
            }
            if primary in args:
                known_records.append(args)
    known_sets = {frozenset(obj.items()) for obj in known_records}
    known = [
        dict(obj) for obj in known_sets if not any(obj < other for other in known_sets)
    ]
    # Explicit IDs can also be placed before their noun in the declared context.
    for key in {k for obj in known for k in obj}:
        mentioned = {
            obj[key]
            for obj in known
            if key in obj
            and re.search(rf"(?<!\w){re.escape(obj[key])}(?!\w)", sentence)
        }
        if len(mentioned) == 1 and key not in resources:
            resources[key] = mentioned.pop()
    if primary in resources or (family == "dispute" and "dispute_id" in resources):
        # Do not silently fill omitted transfer/payout targets when multiple routes
        # on the same source/deposit exist; those require explicit interpretation.
        candidates = [
            obj for obj in known if all(obj.get(k) == v for k, v in resources.items())
        ]
        if len(candidates) > 1:
            return resources, "ambiguous_resource"
        if len(candidates) == 1:
            for k, v in candidates[0].items():
                resources.setdefault(k, v)
        return resources, None
    if len(known) > 1:
        return resources, "ambiguous_resource"
    if len(known) == 1:
        return known[0] | resources, None
    return resources, None


def extract_banking_claims(
    trace: TraceEvidence, contract: MessageClaimContract, *, ticket: str = ""
) -> list[ClientClaim]:
    """Extract declared predicates while keeping same-utterance syntax and timing.

    The completed predicate's last emitted character is the witness boundary;
    an object/amount appended after receipt cannot move this boundary forward.
    Unsupported positive completion wording within the explicit fallback grammar
    is reviewable. Unrecognized prose outside that grammar is not interpreted.
    """
    facts = banking_claim_facts(trace)
    result = []
    for entry in outgoing_entries(trace, contract.tool_arguments):
        for sentence_match in re.finditer(
            r"(?:[^.!?;]|(?<=\d)\.(?=\d))+(?:[.!?;]|$)", entry.text
        ):
            sentence = sentence_match.group()
            for family in contract.families:
                if not re.search(SUBJECTS[family], sentence, re.I):
                    continue
                matched = False
                for match in re.finditer(rf"\b{PATTERNS[family]}\b", sentence, re.I):
                    matched = True
                    if not _qualified(
                        sentence, match, entry.text, sentence_match.start()
                    ):
                        continue
                    predicate = match.group("predicate").lower()
                    position = _position(
                        entry, sentence_match.start() + match.end("predicate")
                    )
                    if family == "dispute":
                        bound = bound_dispute_claims(
                            trace,
                            facts,
                            sentence,
                            predicate,
                            position,
                            entry.channel,
                            ticket,
                        )
                        if bound is not None:
                            result.extend(bound)
                            continue
                    binding_issue = (
                        _unsupported_dispute_binding(sentence, match)
                        if family == "dispute"
                        else None
                    )
                    if binding_issue:
                        # Explicit agent markers on either side identify the
                        # stage, while its receipt cutoff stays at the predicate.
                        # Plural targets still need independent interpretation.
                        known_agent_stage = (
                            binding_issue == "unsupported_plural_binding"
                            and re.search(
                                r"\b(?:мною|нами)\b|только что",
                                sentence,
                                re.I,
                            )
                        )
                        final_stage = predicate.startswith("заверш")
                        result.append(
                            ClientClaim(
                                family=family,
                                kind=(
                                    "completed_action"
                                    if final_stage
                                    else "accepted_request"
                                )
                                if known_agent_stage
                                else "unresolved",
                                resources={},
                                values={
                                    "status": "completed"
                                    if final_stage
                                    else "under_review"
                                }
                                if known_agent_stage
                                else {},
                                emitted_position=position,
                                channel=entry.channel,
                                text=sentence,
                                unresolved_reason="ambiguous_resource"
                                if known_agent_stage
                                else binding_issue,
                            )
                        )
                        continue
                    binding_sentence = sentence
                    if family == "card_block":
                        preceding = re.findall(
                            r"[^.!?;]+[.!?;]?", entry.text[: sentence_match.start()]
                        )
                        binding_sentence = adjacent_card_context(
                            preceding[-1] if preceding else "", sentence
                        )
                    resources, unresolved = _resources(
                        trace, facts, family, binding_sentence, position
                    )
                    actor = bool(
                        re.search(r"\b(?:мною|нами)\b|только что", sentence, re.I)
                    )
                    values = {}
                    if family == "card_block":
                        values = {
                            "status": "active"
                            if predicate.startswith("раз")
                            else "blocked"
                        }
                        kind = (
                            "completed_action"
                            if actor or "ировал" in predicate
                            else "historical"
                            if re.search(r"\bбыла\s+$", sentence[: match.start()], re.I)
                            else "current_state"
                        )
                        if kind == "historical" and not re.fullmatch(
                            rf"\s*карта\s+(?:{ID}\s+)?(?:[*…]?\d{{4}}\s+)?"
                            r"была\s+(?:заблокирована|разблокирована)\s*"
                            r"(?:,\s*теперь\s+временная\s+блокировка\s+снята\s*)?"
                            r"[.!?]?\s*",
                            sentence,
                            re.I,
                        ):
                            # The receipt proves an observed past status, not
                            # its date or relation to an unobserved event. Only
                            # the bounded unqualified construction is understood;
                            # additional qualifiers need independent review.
                            unresolved = "unsupported_card_time"
                    elif family == "deposit_close":
                        values = {"status": "closed"}
                        kind = (
                            "completed_action"
                            if actor or "закрыл" in predicate
                            else "current_state"
                        )
                    elif family == "dispute":
                        completed = predicate.startswith(
                            ("заверш", "возвращ", "вернул")
                        )
                        values = {
                            "status": "refunded"
                            if predicate.startswith(("возвращ", "вернул"))
                            else "completed"
                            if completed
                            else "under_review"
                        }
                        kind = (
                            "completed_action"
                            if predicate.startswith(("возвращ", "вернул"))
                            or (completed and actor)
                            else "current_state"
                            if completed
                            else "accepted_request"
                            if actor
                            or re.search(r"\bнов(?:ый|ые|ого)\s+спор", sentence, re.I)
                            or "открыл" in predicate
                            or "ировал" in predicate
                            else "current_state"
                        )
                    else:
                        kind = "completed_action"
                    if kind in {"current_state", "historical"}:
                        identity = {
                            "card_block": "card_id",
                            "deposit_close": "deposit_id",
                            "dispute": "dispute_id",
                        }.get(family)
                        if identity in resources:
                            resources = {identity: resources[identity]}
                    amounts = [
                        Decimal(
                            m.group(1)
                            .replace(" ", "")
                            .replace("\u00a0", "")
                            .replace("\u202f", "")
                            .replace(",", ".")
                        )
                        for m in MONEY.finditer(sentence)
                    ]
                    if len(set(amounts)) > 1:
                        unresolved = "ambiguous_amount"
                    if family in {"transfer", "deposit_close", "dispute"}:
                        unresolved = _amount_qualifier_issue(sentence) or unresolved
                    amount = (
                        amounts[0]
                        if amounts
                        and family in {"transfer", "deposit_close", "dispute"}
                        else None
                    )
                    if family == "deposit_close" and amount is not None:
                        kind = "completed_action"
                    result.append(
                        ClientClaim(
                            family=family,
                            kind=kind,
                            resources=resources,
                            values=values,
                            amount=amount,
                            emitted_position=position,
                            channel=entry.channel,
                            text=sentence,
                            unresolved_reason=unresolved,
                        )
                    )
                if not matched:
                    fallback = re.search(
                        r"\b(?:успеш\w*|заверш\w*|выполн(?!яется\b|яются\b)\w*)\b"
                        r"(?!\s+(?:календарн\w*\s+)?(?:день|дня|дней|дни)\b)",
                        sentence,
                        re.I,
                    )
                    directive = bool(
                        fallback
                        and fallback.group().lower() in {"завершить", "выполнить"}
                        and re.search(
                            r"\b(?:нужно|необходимо|следует)\s+$",
                            sentence[: fallback.start()],
                            re.I,
                        )
                    )
                    if (
                        fallback
                        and not directive
                        and not _quoted(
                            entry.text, sentence_match.start() + fallback.start()
                        )
                        and not re.search(r"\b(?:не|если|когда)\b", sentence, re.I)
                    ):
                        result.append(
                            ClientClaim(
                                family=family,
                                kind="unresolved",
                                resources={},
                                values={},
                                emitted_position=_position(
                                    entry, sentence_match.start() + fallback.end()
                                ),
                                channel=entry.channel,
                                text=sentence,
                                unresolved_reason="unsupported_claim_form",
                            )
                        )
    return result
