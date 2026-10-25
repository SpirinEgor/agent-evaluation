import pytest

pytest.importorskip("mcp")

from tau2.platform.__main__ import parse_args


def test_parse_args_defaults_and_overrides():
    settings, host, port = parse_args([])
    assert (settings.domain, settings.split, host, port) == (
        "banking_ru",
        "base",
        "127.0.0.1",
        8010,
    )
    assert settings.reveal_checks is False
    settings, host, port = parse_args(
        ["--split", "test", "--port", "9000", "--reveal-checks", "--max-calls", "30"]
    )
    assert settings.split == "test" and port == 9000
    assert settings.reveal_checks is True and settings.max_calls == 30


@pytest.mark.parametrize("domain,want", [("banking_ru", (100, 0)), ("mock", (60, 5))])
def test_cli_resolves_domain_budgets_and_preserves_overrides(domain, want):
    settings, _, _ = parse_args(["--domain", domain])
    assert (settings.max_calls, settings.max_errors) == want
    explicit, _, _ = parse_args(
        ["--domain", domain, "--max-calls", "7", "--max-errors", "2"]
    )
    assert (explicit.max_calls, explicit.max_errors) == (7, 2)
