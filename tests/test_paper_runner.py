from datetime import datetime, timezone
from decimal import Decimal

from paper_runner import ceil_cent, flatten_orders, floor_cent, is_flatten_time, is_scan_time, sma
from trading_agents import technical_agent


def test_rounding_is_conservative():
    assert ceil_cent(Decimal("10.001")) == Decimal("10.01")
    assert floor_cent(Decimal("9.999")) == Decimal("9.99")


def test_sma():
    assert sma([Decimal(i) for i in range(1, 21)]) == Decimal("10.5")


def test_nested_orders_count_once():
    orders = [{"id": "parent", "legs": [{"id": "child"}, {"id": "child"}]}]
    assert [item["id"] for item in flatten_orders(orders)] == ["parent", "child"]


def test_decision_cadence_and_flatten_time():
    assert is_scan_time(datetime(2026, 9, 16, 13, 45, tzinfo=timezone.utc))
    assert is_scan_time(datetime(2026, 9, 16, 14, 0, tzinfo=timezone.utc))
    assert not is_scan_time(datetime(2026, 9, 16, 14, 5, tzinfo=timezone.utc))
    assert is_flatten_time(datetime(2026, 9, 16, 19, 55, tzinfo=timezone.utc))


def test_technical_agent_only_ranks_qualified_candidates():
    signals = {
        "SCHB": {"qualifies": True, "session_return": Decimal("0.01")},
        "SCHG": {"qualifies": True, "session_return": Decimal("0.02")},
        "SCHA": {"qualifies": False, "session_return": Decimal("0.50")},
    }
    result = technical_agent(signals)
    assert result["candidate"] == "SCHG"
    assert result["qualified"] == ["SCHG", "SCHB"]
