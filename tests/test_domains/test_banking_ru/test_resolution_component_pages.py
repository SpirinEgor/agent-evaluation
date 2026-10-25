"""Large menus stay discoverable through owned, bounded, stable pages."""

from types import SimpleNamespace

import pytest

from tau2.domains.banking_ru import reward_resolution as resolution


class Row:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def model_dump(self):
        return self.__dict__.copy()


def tools(count=348, verified=True):
    offer = Row(
        id="offer",
        customer_id="c",
        case_id="case",
        period="2026-07",
        components=[Row(id=f"p{i}", incompatible_with=[]) for i in range(count)],
    )

    def require(cid):
        if not verified or cid != "c":
            raise ValueError("not verified")

    return SimpleNamespace(
        db=SimpleNamespace(reward_resolution_offers={"offer": offer}),
        _require_verified=require,
    )


def test_stable_pages_cover_every_component_once():
    t = tools()
    rows = []
    for offset in range(0, 348, 50):
        result = resolution.list_components(t, "offer", offset, 50)
        assert result["total"] == 348 and result["offer_id"] == "offer"
        assert result["next_offset"] == (offset + 50 if offset + 50 < 348 else None)
        rows.extend(result["components"])
    assert [r["id"] for r in rows] == [f"p{i}" for i in range(348)]


@pytest.mark.parametrize("offset,limit", [(-1, 50), (0, 0), (0, 101)])
def test_invalid_page_bounds_rejected(offset, limit):
    with pytest.raises(ValueError):
        resolution.list_components(tools(), "offer", offset, limit)


@pytest.mark.parametrize("offer,verified", [("offer", False), ("unknown", True)])
def test_no_unverified_or_unknown_offer_read(offer, verified):
    with pytest.raises(ValueError):
        resolution.list_components(tools(verified=verified), offer, 0, 50)


def test_empty_last_page_has_explicit_end():
    result = resolution.list_components(tools(), "offer", 348, 50)
    assert result["components"] == [] and result["next_offset"] is None
