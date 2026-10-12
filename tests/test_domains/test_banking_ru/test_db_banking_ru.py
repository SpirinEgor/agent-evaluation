"""Валидатор данных домена banking_ru.

Ловит расхождения между db.json, tasks.json и спецификацией домена до того,
как на них будет потрачен платный прогон модели: конвенции идентификаторов,
ссылочную целостность и сходимость производных чисел.
"""

import pytest

from tau2.domains.banking_ru.data_model import BankingDB
from tau2.domains.banking_ru.environment import get_tasks
from tau2.domains.banking_ru.utils import BANKING_RU_DB_PATH

ID_PATTERNS = {
    "customers": r"^[a-z]+_[a-z]_\d{4}$",
    "accounts": r"^acc_\d{4}$",
    "cards": r"^card_\d{4}[a-z]?$",
    "transactions": r"^txn_\d{6}$",
    "disputes": r"^dsp_\d{4}$",
    "subscriptions": r"^sub_\d{4}[a-z]?$",
    "autopayments": r"^ap_\d{4}$",
    "deposits": r"^dep_\d{4}$",
    "loans": r"^ln_\d{4}$",
    "cases": r"^case_\d{4}$",
    "devices": r"^dev_\d{4}$",
    "documents": r"^doc_\d{4}_\d$",
    "articles": r"^kb_\d{3}$",
}


@pytest.fixture(scope="module")
def db() -> BankingDB:
    return BankingDB.load(BANKING_RU_DB_PATH)


def test_collection_keys_match_object_ids(db: BankingDB):
    for collection in ID_PATTERNS:
        for key, value in getattr(db, collection).items():
            assert key == value.id, (
                f"{collection}: ключ {key} не совпадает с id {value.id}"
            )


def test_referential_integrity(db: BankingDB):
    for account in db.accounts.values():
        assert account.customer_id in db.customers
    for card in db.cards.values():
        assert card.customer_id in db.customers
        assert card.account_id in db.accounts
        assert card.id in db.card_limits, f"У карты {card.id} нет записи лимитов"
    for transaction in db.transactions.values():
        assert transaction.customer_id in db.customers
        assert transaction.account_id in db.accounts
        if transaction.card_id is not None:
            assert transaction.card_id in db.cards
        if transaction.dispute_id is not None:
            assert transaction.dispute_id in db.disputes
    for dispute in db.disputes.values():
        assert dispute.transaction_id in db.transactions
    for deposit in db.deposits.values():
        assert deposit.payout_account_id in db.accounts
    for customer in db.customers.values():
        assert customer.tariff_id in db.tariffs


def test_task_split_covers_all_tasks():
    from tau2.domains.banking_ru.environment import get_tasks_split

    splits = get_tasks_split()
    assert "base" in splits, "Сплит base обязателен: он используется по умолчанию"
    all_ids = {task.id for task in get_tasks(task_split_name=None)}
    assert set(splits["base"]) == all_ids
    assert set(splits["train"]) <= all_ids, "train ссылается на несуществующие задачи"
    assert len(splits["train"]) == 10, "train — фиксированные 10 открытых задач"
    assert set(splits["test"]) <= all_ids, "test ссылается на несуществующие задачи"
    assert not set(splits["test"]) & set(splits["train"]), (
        "train и test не пересекаются: test — закрытый набор"
    )
    assert set(splits["train"]) | set(splits["test"]) == all_ids, (
        "каждая задача принадлежит ровно одному из сплитов train/test"
    )
