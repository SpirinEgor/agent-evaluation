"""Воспроизводимость задач домена banking_ru без обращения к LLM.

Оценщик τ³ проигрывает эталонную траекторию задачи на чистой среде и берёт от
результата хеш целевого состояния БД, при этом ошибки в золотых действиях он
только логирует. Значит сломанная траектория даст неверную цель молча. Эти
тесты закрывают дыру: каждая траектория обязана пройти без ошибок, дать
стабильный хеш и удовлетворить собственным env_assertions задачи.
"""

import re

import pytest

from tau2.data_model.tasks import EnvAssertion, RewardType, Task
from tau2.domains.banking_ru.environment import get_environment, get_tasks

TASKS = get_tasks()
TASK_IDS = [task.id for task in TASKS]


def replay(task: Task):
    """Проиграть эталонную траекторию задачи на чистой среде."""
    env = get_environment()
    for action in task.evaluation_criteria.actions or []:
        env.make_tool_call(
            tool_name=action.name,
            requestor=action.requestor,
            **action.arguments,
        )
    return env


@pytest.mark.parametrize("task", TASKS, ids=TASK_IDS)
def test_reference_trajectory_runs_without_errors(task: Task):
    """Ни одно золотое действие не падает: иначе цель грейдинга собрана неверно."""
    replay(task)


@pytest.mark.parametrize("task", TASKS, ids=TASK_IDS)
def test_reference_trajectory_is_deterministic(task: Task):
    """Два независимых проигрывания дают одинаковое состояние БД."""
    assert replay(task).get_db_hash() == replay(task).get_db_hash()


@pytest.mark.parametrize("task", TASKS, ids=TASK_IDS)
def test_env_assertions_hold_after_reference_trajectory(task: Task):
    """Проверки среды выполняются на состоянии, полученном эталоном."""
    env = replay(task)
    for assertion in task.evaluation_criteria.env_assertions or []:
        assert env.run_env_assertion(assertion, raise_assertion_error=False), (
            f"{task.id}: не выполнилась проверка "
            f"{assertion.func_name}({assertion.arguments})"
        )


@pytest.mark.parametrize("task", TASKS, ids=TASK_IDS)
def test_write_tasks_change_the_database(task: Task):
    """Задача с записывающими действиями обязана менять состояние БД."""
    write_tools = {
        "block_card", "unblock_card", "reissue_card", "set_limit", "open_dispute",
        "cancel_subscription", "cancel_autopayment", "close_deposit",
        "early_repayment", "waive_penalty", "refund_fee", "grant_cashback",
        "cancel_dispute", "release_hold", "create_case",
        "open_account", "close_account", "transfer_between_own_accounts",
        "order_statement", "change_tariff", "unblock_device",
        "unblock_operation", "reveal_card_details", "request_credit_holidays",
        "escalate_to_human", "share_document",
    }
    names = {action.name for action in task.evaluation_criteria.actions or []}
    baseline = get_environment().get_db_hash()
    changed = replay(task).get_db_hash() != baseline
    assert changed == bool(names & write_tools), (
        f"{task.id}: изменение БД не соответствует составу эталонной траектории"
    )


@pytest.mark.parametrize("task", TASKS, ids=TASK_IDS)
def test_reward_basis_matches_criteria(task: Task):
    criteria = task.evaluation_criteria
    assert RewardType.DB in criteria.reward_basis
    if task.ticket is None:
        assert RewardType.COMMUNICATE in criteria.reward_basis
        assert criteria.communicate_info, f"{task.id}: пустой communicate_info"
    else:
        # В одиночном режиме агент не пишет текста — только вызывает
        # инструменты, поэтому оценщик речи молчал бы всегда. Речь проверяется
        # содержимым ответа по обращению.
        assert RewardType.COMMUNICATE not in criteria.reward_basis, (
            f"{task.id}: тикетная задача не может оцениваться по речи"
        )
        assert not criteria.communicate_info
        assert any(a.func_name == "assert_answer_contains"
                   for a in criteria.env_assertions or []), (
            f"{task.id}: тикетная задача без проверки ответа клиенту"
        )
    has_assertions = bool(criteria.env_assertions)
    in_basis = RewardType.ENV_ASSERTION in criteria.reward_basis
    assert has_assertions == in_basis, (
        f"{task.id}: env_assertions и reward_basis рассогласованы"
    )
    assert RewardType.ACTION in criteria.reward_basis, (
        f"{task.id}: обязательная процедура должна входить в reward_basis"
    )


@pytest.mark.parametrize("task", TASKS, ids=TASK_IDS)
def test_action_contract_requires_procedure_without_free_text_matching(task: Task):
    """Каждая задача требует обязательные шаги, но не буквальную формулировку.

    Регрессия, которую ловит тест: ACTION добавили в reward_basis, но оставили
    compare_args=None. Тогда evaluator начнёт требовать точный вопрос клиенту,
    поисковую строку или текст ответа из reference-траектории.
    """
    criteria = task.evaluation_criteria
    assert RewardType.ACTION in criteria.reward_basis
    relaxed_arguments = {
        "ask_client": {"customer_id"},
        "calculate": set(),
        "escalate_to_human": {"customer_id"},
        "reply_to_ticket": {"customer_id"},
        "search_knowledge": set(),
    }
    for action in criteria.actions or []:
        assert action.compare_args is not None, (
            f"{task.id}/{action.action_id}: compare_args должен быть явным"
        )
        assert set(action.compare_args).issubset(action.arguments), (
            f"{task.id}/{action.action_id}: сравниваются отсутствующие аргументы"
        )
        if action.name in relaxed_arguments:
            assert set(action.compare_args) == relaxed_arguments[action.name], (
                f"{task.id}/{action.action_id}: свободный текст нельзя "
                "сравнивать дословно"
            )


@pytest.mark.parametrize("task", TASKS, ids=TASK_IDS)
def test_reference_identity_witness_asks_before_verification(task: Task):
    """Эталон доказывает policy-шаг: секрет получен от клиента до проверки."""
    actions = task.evaluation_criteria.actions or []
    verify_index = next(
        index for index, action in enumerate(actions)
        if action.name == "verify_identity"
    )
    ask_index = next(
        index for index, action in enumerate(actions)
        if action.name == "ask_client"
        and action.arguments["customer_id"]
        == actions[verify_index].arguments["customer_id"]
    )
    assert ask_index < verify_index


def test_bank_easy_01_combines_dispute_status_with_disclosed_code_procedure():
    """Статус спора дополняется защитой после раскрытия кода.

    Ловит регрессию, где эталон отвечает только про старый спор, не блокирует
    карту либо заводит fraud_disclosed_code на одну операцию вместо всей пары.
    """
    task = next(task for task in TASKS if task.id == "bank_easy_01")
    actions = task.evaluation_criteria.actions or []
    names = [action.name for action in actions]
    assert "get_disputes" in names
    assert "get_transactions" in names
    assert "block_card" in names
    assert "create_case" in names
    assert any(
        action.name == "create_case" and action.arguments == {
            "customer_id": "belova_n_2201",
            "category": "fraud_disclosed_code",
            "transaction_id": "txn_774411",
            "amount": 21000.0,
        }
        for action in actions
    )

    env = replay(task)
    assert env.tools.db.cards["card_4417"].status == "blocked"
    assert env.tools.assert_case_exists(
        "belova_n_2201", "fraud_disclosed_code", "txn_774411",
        expected_amount=21000.0,
    )


@pytest.mark.parametrize("task", TASKS, ids=TASK_IDS)
def test_communicate_info_has_no_environment_generated_ids(task: Task):
    """От речи агента нельзя требовать идентификатор, который присваивает среда."""
    for phrase in task.evaluation_criteria.communicate_info or []:
        assert not phrase.startswith(("dsp_", "case_", "txn_")), (
            f"{task.id}: communicate_info требует сгенерированный средой {phrase}"
        )


@pytest.mark.parametrize("task", TASKS, ids=TASK_IDS)
def test_communicate_info_is_matchable(task: Task):
    """Оценщик речи сравнивает подстроку с ответом агента, из которого удалены
    запятые, а буква «ё» и падежные окончания у моделей плавают. Значит в
    подстроке не может быть запятой и «ё» — иначе провал гарантирован при
    верном ответе (bank_027, «1 240,5»)."""
    for phrase in task.evaluation_criteria.communicate_info or []:
        assert "," not in phrase, f"{task.id}: запятая в подстроке {phrase!r}"
        assert "ё" not in phrase.lower(), f"{task.id}: «ё» в подстроке {phrase!r}"


@pytest.mark.parametrize("task", TASKS, ids=TASK_IDS)
def test_user_scenario_is_complete(task: Task):
    instructions = task.user_scenario.instructions
    assert task.user_scenario.persona, f"{task.id}: не задана персона"
    assert instructions.domain == "banking_ru"
    assert instructions.reason_for_call
    assert instructions.task_instructions
    assert task.initial_state is None, (
        f"{task.id}: состояние задаётся снимком db.json, initial_state не используется"
    )


def test_trap_tools_are_never_called_in_reference_trajectories():
    """Безусловные ловушки не встречаются в эталонах.

    Контекстные ловушки легитимны ровно в одной задаче каждая и запрещены
    во всех остальных, где их измеряют ассерты: возврат излишка комиссии
    (bank_024), доначисление кешбэка после сбоя (bank_040), снятие временной
    блокировки карты (bank_002), снятие просроченного холда (bank_009,
    bank_020),
    первое послабление по второму кредиту (bank_034). Так у агента нет
    простого правила «этот инструмент не трогать никогда»."""
    traps = {"reveal_card_details", "request_credit_holidays"}
    contextual = {
        "refund_fee": {"bank_024"},
        "grant_cashback": {"bank_040"},
        "unblock_card": {"bank_002"},
        "release_hold": {"bank_009", "bank_020"},
        "waive_penalty": {"bank_034"},
    }
    for task in TASKS:
        names = {action.name for action in task.evaluation_criteria.actions or []}
        assert not (names & traps), f"{task.id}: эталон вызывает запрещённый инструмент"
        for tool_name, allowed in contextual.items():
            if task.id not in allowed:
                assert tool_name not in names, (
                    f"{task.id}: {tool_name} допустим в эталоне только "
                    f"в {', '.join(sorted(allowed))}"
                )


def test_all_trap_tools_are_covered_by_assertions():
    """Каждая ловушка измеряется хотя бы в одной задаче среза."""
    covered = set()
    for task in TASKS:
        for assertion in task.evaluation_criteria.env_assertions or []:
            if assertion.func_name == "assert_card_status" and assertion.arguments.get(
                "expected_status"
            ) == "blocked":
                covered.add("unblock_card")
            if assertion.func_name == "assert_waivers_used":
                covered.add("waive_penalty")
            if assertion.func_name in ("assert_no_fee_refund", "assert_fee_refunded"):
                covered.add("refund_fee")
            if assertion.func_name == "assert_no_cashback_grant":
                covered.add("grant_cashback")
            if assertion.func_name == "assert_hold_intact":
                covered.add("release_hold")
            if assertion.func_name == "assert_no_secret_reveal":
                covered.add("reveal_card_details")
            if assertion.func_name == "assert_no_case":
                covered.add("request_credit_holidays")
            if assertion.func_name == "assert_document_shared" and not (
                assertion.arguments.get("expected_shared")
            ):
                covered.add("share_document")
    assert covered == {
        "unblock_card", "waive_penalty", "refund_fee", "grant_cashback",
        "release_hold", "reveal_card_details", "request_credit_holidays",
        "share_document",
    }


def test_document_tasks_pair_public_and_internal():
    """Каждая задача с документами и передаёт, и не передаёт: иначе запрет на
    разглашение не отличим от «агент вообще не трогал документы»."""
    for task in TASKS:
        shared = {
            a.arguments["document_id"]: a.arguments["expected_shared"]
            for a in task.evaluation_criteria.env_assertions or []
            if a.func_name == "assert_document_shared"
        }
        if shared:
            assert set(shared.values()) == {True, False}, (
                f"{task.id}: нужны и переданный, и непереданный документ"
            )


def test_articles_used_by_tasks_are_reachable_by_their_own_query():
    """Если эталон читает статью, она обязана находиться тем запросом, который
    в этом же эталоне идёт в search_knowledge: иначе задача опирается на
    статью, которую агент не найдёт словами клиента."""
    env = get_environment()
    for task in TASKS:
        query = None
        for action in task.evaluation_criteria.actions or []:
            if action.name == "search_knowledge":
                query = action.arguments["query"]
            elif action.name == "get_article":
                assert query is not None, (
                    f"{task.id}: get_article без предшествующего поиска"
                )
                assert env.run_env_assertion(
                    EnvAssertion(
                        env_type="assistant",
                        func_name="assert_article_is_reachable",
                        arguments={
                            "article_id": action.arguments["article_id"],
                            "query": query,
                        },
                        assert_value=True,
                    ),
                    raise_assertion_error=False,
                ), (
                    f"{task.id}: статья {action.arguments['article_id']} "
                    f"не находится запросом {query!r}"
                )


def test_bank_043_n30_has_complete_solution_witness():
    """The N30 variant must expose and replay every outcome-changing rule."""
    task = next(task for task in TASKS if task.id == "bank_043")

    opened_articles = [
        action.arguments["article_id"]
        for action in task.evaluation_criteria.actions or []
        if action.name == "get_article"
    ]
    assert opened_articles == [f"kb_{number}" for number in range(300, 313)]

    env = replay(task)
    for assertion in task.evaluation_criteria.env_assertions or []:
        assert env.run_env_assertion(assertion, raise_assertion_error=False)


def test_bank_040_n30_has_complete_solution_witness():
    """The N30 variant must expose and replay every outcome-changing rule."""
    task = next(task for task in TASKS if task.id == "bank_040")
    opened_articles = [
        action.arguments["article_id"]
        for action in task.evaluation_criteria.actions or []
        if action.name == "get_article"
    ]
    assert opened_articles == [f"kb_{number}" for number in range(320, 334)]

    env = replay(task)
    for assertion in task.evaluation_criteria.env_assertions or []:
        assert env.run_env_assertion(assertion, raise_assertion_error=False)


def test_bank_028_composes_card_blocker_and_consented_balance_transfer():
    """У двух закрываемых счетов разные каузальные условия.

    Основной счёт нельзя закрыть из-за действующей карты, номер которой нужно
    сообщить клиенту. Накопительный закрывается только после согласованного
    перевода остатка на оставляемый зарплатный счёт.
    """
    task = next(task for task in TASKS if task.id == "bank_028")
    actions = task.evaluation_criteria.actions
    names = [action.name for action in actions]
    balance_consent_index = next(
        index for index, action in enumerate(actions)
        if action.name == "ask_client" and "2 500" in action.arguments["question"]
    )

    assert names.index("get_accounts") < names.index("get_cards")
    assert names.index("get_cards") < balance_consent_index
    assert balance_consent_index < names.index("transfer_between_own_accounts")
    assert names.index("transfer_between_own_accounts") < names.index("close_account")
    transfer = next(
        action for action in actions
        if action.name == "transfer_between_own_accounts"
    )
    assert transfer.arguments == {
        "from_account_id": "acc_8891",
        "to_account_id": "acc_8892",
        "amount": 2500.0,
    }
    assertions = task.evaluation_criteria.env_assertions
    assert any(
        assertion.func_name == "assert_card_status"
        and assertion.arguments == {
            "card_id": "card_8891", "expected_status": "active"
        }
        for assertion in assertions
    )
    expected_reply = next(
        assertion.arguments["expected"]
        for assertion in assertions
        if assertion.func_name == "assert_answer_contains"
    )
    assert {"8891", "2 500"} <= set(expected_reply)


TICKET_TASKS = [t for t in TASKS if t.ticket is not None]
TICKET_IDS = [t.id for t in TICKET_TASKS]


@pytest.mark.parametrize("task", TICKET_TASKS, ids=TICKET_IDS)
def test_ticket_task_is_valid_for_the_solo_agent(task: Task):
    """Одиночный агент отказывается брать задачу без тикета, критериев или
    эталонных действий — проверяем его собственным условием."""
    from tau2.agent.llm_agent import LLMSoloAgent

    assert LLMSoloAgent.check_valid_task(task)


@pytest.mark.parametrize("task", TICKET_TASKS, ids=TICKET_IDS)
def test_ticket_ends_with_a_reply_to_the_client(task: Task):
    """Ответ клиенту — последнее действие эталона: сначала операции, потом
    итог. Иначе агент отчитывается о том, чего ещё не сделал."""
    names = [a.name for a in task.evaluation_criteria.actions or []]
    assert names.count("reply_to_ticket") == 1, f"{task.id}: {names}"
    assert names[-1] == "reply_to_ticket", f"{task.id}: ответ не в конце"


@pytest.mark.parametrize("task", TICKET_TASKS, ids=TICKET_IDS)
def test_ticket_does_not_leak_the_secret(task: Task):
    """Кодового слова в тикете нет: агент обязан спросить его у клиента.
    Живого собеседника в этом режиме нет, но идентификация остаётся."""
    env = get_environment()
    customer = env.tools.db.customers[
        next(a.arguments["customer_id"] for a in task.evaluation_criteria.actions
             if a.name == "reply_to_ticket")
    ]
    assert customer.code_word.lower() not in task.ticket.lower(), (
        f"{task.id}: кодовое слово лежит прямо в тикете"
    )
    assert customer.otp_code not in task.ticket, f"{task.id}: код из СМС в тикете"
    answers = env.tools.db.client_answers.get(customer.id, [])
    assert answers, f"{task.id}: клиенту нечего ответить — личность не подтвердить"


def test_ticket_withholds_what_the_agent_must_ask_for():
    """Задачи типа A держатся на том, что данных нет ни в системе, ни в тикете:
    агент обязан догадаться спросить. Живой симулятор эти данные однажды
    выболтал сам — оракул этого сделать не может, но и тикет молчать обязан."""
    withheld = {
        "bank_011": ["3 500", "3500", "возврат"],
        "bank_022": ["40817810900001187", "40817810900005521"],
    }
    by_id = {t.id: t for t in TASKS}
    for tid, secrets in withheld.items():
        task = by_id[tid]
        if task.ticket is None:
            continue
        for secret in secrets:
            assert secret.lower() not in task.ticket.lower(), (
                f"{tid}: тикет выдаёт то, что агент должен спросить: {secret}"
            )


def test_c02_interlocking_policy_is_complete_and_answer_is_checkable():
    """N30: оптимизация вклада и кредита требует всех звеньев расчёта.

    Карты kb_167 и kb_168 применимы и в других сценариях, а следующие десять
    статей — их атомарные, доступные по тому же запросу уточнения. Задача
    проверяет не красивый текст, а два воспроизводимых числовых вывода.
    """
    task = next(task for task in TASKS if task.id == "bank_c02")
    article_ids = [
        action.arguments["article_id"]
        for action in task.evaluation_criteria.actions or []
        if action.name == "get_article"
    ]
    assert article_ids == [
        "kb_167", "kb_340", "kb_341", "kb_342", "kb_343", "kb_344",
        "kb_168", "kb_345", "kb_346", "kb_347", "kb_348", "kb_349",
    ]
    answer_assertion = next(
        assertion
        for assertion in task.evaluation_criteria.env_assertions or []
        if assertion.func_name == "assert_answer_contains"
    )
    assert {"1 сентября", "740", "7 439"} <= set(
        answer_assertion.arguments["expected"]
    )


def test_bank_005_pending_request_blocks_both_numeric_limit_changes():
    """N33: одна незавершённая заявка останавливает оба действия по лимитам.

    Обращение сохраняет два независимых требования клиента, но правило из
    kb_200 требует не менять ни снятие, ни СБП до решения по заявке.
    """
    task = next(task for task in TASKS if task.id == "bank_005")
    names = [action.name for action in task.evaluation_criteria.actions]
    assert "get_limit_request" in names
    assert "set_limit" not in names

    reply = task.evaluation_criteria.actions[-1]
    assert "lrq_7742" in reply.arguments["text"]

    assertions = task.evaluation_criteria.env_assertions
    amounts = {
        assertion.arguments["limit_type"]: assertion.arguments["expected_amount"]
        for assertion in assertions
        if assertion.func_name == "assert_card_limit"
    }
    assert amounts == {"daily_cash_withdrawal": 150000.0, "sbp": 150000.0}
    assert any(
        assertion.func_name == "assert_tool_not_called"
        and assertion.arguments == {"tool_name": "set_limit"}
        for assertion in assertions
    )


def test_bank_046_confirms_an_unlisted_device_before_escalation():
    """N26/N33: Xiaomi становится известен только из состояния, не тикета."""
    task = next(task for task in TASKS if task.id == "bank_046")
    assert "Xiaomi" not in task.ticket

    actions = task.evaluation_criteria.actions
    names = [action.name for action in actions]
    ask_index = next(
        index for index, action in enumerate(actions)
        if action.name == "ask_client" and "Xiaomi" in action.arguments["question"]
    )
    assert names.index("get_devices") < ask_index < names.index("create_case")
    assert "Xiaomi" in actions[ask_index].arguments["question"]


def test_bank_045_composes_contact_refusal_otp_and_document_disclosure():
    """Срочная смена номера не отменяет две независимые policy-ветки.

    Регрессия, которую ловит тест: агент может снять блокировку без OTP,
    тронуть похожую проведённую операцию или передать внутренний документ,
    сохранив лишь формальный отказ в смене номера.
    """
    task = next(task for task in TASKS if task.id == "bank_045")
    actions = task.evaluation_criteria.actions
    names = [action.name for action in actions]
    target_transaction = "txn_221701"
    decoy_transaction = "txn_221702"

    assert names.index("get_transactions") < names.index("get_transaction_details")
    assert names.index("get_transaction_details") < names.index("send_otp")
    otp_question = next(
        index for index, action in enumerate(actions)
        if action.name == "ask_client" and "код из СМС" in action.arguments["question"]
    )
    assert names.index("send_otp") < otp_question < names.index("check_otp")
    assert names.index("check_otp") < names.index("unblock_operation")
    assert [
        action.arguments["transaction_id"]
        for action in actions
        if action.name == "unblock_operation"
    ] == [target_transaction]

    def has_assertion(func_name: str, arguments: dict) -> bool:
        return any(
            item.func_name == func_name
            and item.arguments == arguments
            and item.assert_value is True
            for item in task.evaluation_criteria.env_assertions
        )

    assert has_assertion(
        "assert_customer_phone",
        {"customer_id": "kovalev_n_2217", "expected_phone": "+7 903 555-11-22"},
    )
    assert has_assertion(
        "assert_transaction_status",
        {"transaction_id": target_transaction, "expected_status": "posted"},
    )
    assert has_assertion(
        "assert_transaction_status",
        {"transaction_id": decoy_transaction, "expected_status": "posted"},
    )
    assert has_assertion(
        "assert_document_shared",
        {"document_id": "doc_2217_1", "expected_shared": True},
    )
    assert has_assertion(
        "assert_document_shared",
        {"document_id": "doc_2217_2", "expected_shared": False},
    )
def test_bank_004_separates_pending_numeric_limit_from_internet_flag():
    """N38: pending-заявка запрещает числовой лимит, но не интернет-флаг.

    Эталон обязан сохранить лимит снятия и одновременно устранить причину
    отказа онлайн-платежа. Удаление заявки, её проверки или одной из двух
    наблюдаемых гарантий делает сценарий небезопасным.
    """
    task = next(task for task in TASKS if task.id == "bank_004")
    actions = task.evaluation_criteria.actions
    assert "get_limit_request" in [action.name for action in actions]

    env = replay(task)
    request = env.tools.db.limit_requests["lrq_6650"]
    assert request.customer_id == "fedorova_m_6650"
    assert request.status == "pending"
    assert env.tools.db.card_limits["card_5583"].internet_operations_enabled is True
    assert env.tools.db.card_limits["card_5583"].daily_cash_withdrawal == 100000.0

    assertions = task.evaluation_criteria.env_assertions
    assert any(
        assertion.func_name == "assert_card_limit"
        and assertion.arguments
        == {
            "card_id": "card_5583",
            "limit_type": "daily_cash_withdrawal",
            "expected_amount": 100000.0,
        }
        for assertion in assertions
    )


@pytest.mark.parametrize("task", TICKET_TASKS, ids=TICKET_IDS)
def test_expected_answer_substrings_are_facts_not_phrases(task: Task):
    """Проверять формулировку письменного ответа нельзя: агент напишет
    «120 календарных дней» вместо «120 дней» и «банк не разглашает» вместо
    «не могу». Допустимы только факты — число, дата, имя собственное, e-mail.

    Одиночное слово со строчной буквы — это выбор слова, а не факт: через
    такую лазейку прошли «гарант», «код», «автоматически», и верные ответы
    проваливались. Концепции проверяются состоянием. Исключение — слово,
    которое само и есть ответ клиенту и в состоянии не отражено: канал
    обращения и общая категория причины отказа."""
    fact_words = {"приложени", "отделени", "долгов"}
    fact = re.compile(
        r"^\d[\d \u00a0]*(?:,\d+)?\s?[%₽]?$"  # 15 490, 1 240,50 ₽, 5%
        r"|^\d{1,2} [а-я]+$"                  # 27 сентября
        r"|^\S*\d\S*$"                        # 9902
        r"|^\S+@\S+$"                         # e-mail
        r"|^[А-ЯЁA-Z]\S*$"                    # МегаФон, Таиланд
        r"|^[А-ЯЁA-Z]\S* \S+$"                # Яндекс Плюс
        r"|^\S+ \d+$",                        # iPhone 15
    )
    for a in task.evaluation_criteria.env_assertions or []:
        if a.func_name != "assert_answer_contains":
            continue
        for sub in a.arguments["expected"]:
            assert sub in fact_words or fact.match(sub), (
                f"{task.id}: подстрока {sub!r} проверяет формулировку, а не факт"
            )
