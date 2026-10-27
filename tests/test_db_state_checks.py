"""DB obligations cover business state omitted by a domain's semantic hash."""

from types import SimpleNamespace


def _check(**updates):
    return SimpleNamespace(
        **{
            "check_id": "reservations-closed",
            "collection": "cases",
            "where": {"customer_id": "own", "reward_period": "2026-07"},
            "field": "adjustment_reservations",
            "equals": [],
            **updates,
        }
    )


def test_state_check_rejects_hidden_remaining_reservation():
    from tau2.evaluator.db_state_checks import state_check_failures

    state = {
        "cases": {
            "case": {
                "customer_id": "own",
                "reward_period": "2026-07",
                "adjustment_reservations": [
                    {"permission_id": "permission", "amount": 5}
                ],
            }
        }
    }
    assert state_check_failures(state, [_check()]) == ["reservations-closed"]


def test_state_check_accepts_closed_scope_and_leaves_other_scopes_alone():
    from tau2.evaluator.db_state_checks import state_check_failures

    state = {
        "cases": {
            "own": {
                "customer_id": "own",
                "reward_period": "2026-07",
                "adjustment_reservations": [],
            },
            "other-period": {
                "customer_id": "own",
                "reward_period": "2026-06",
                "adjustment_reservations": [1],
            },
            "other-owner": {
                "customer_id": "other",
                "reward_period": "2026-07",
                "adjustment_reservations": [1],
            },
        }
    }
    assert state_check_failures(state, [_check()]) == []


def test_state_check_cannot_pass_a_missing_collection_or_field():
    from tau2.evaluator.db_state_checks import state_check_failures

    assert state_check_failures({}, [_check()]) == ["reservations-closed"]
    assert state_check_failures(
        {
            "cases": {
                "case": {
                    "customer_id": "own",
                    "reward_period": "2026-07",
                }
            }
        },
        [_check()],
    ) == ["reservations-closed"]


def test_no_state_checks_keeps_legacy_hash_semantics():
    from tau2.evaluator.db_state_checks import state_check_failures

    assert state_check_failures({}, []) == []
