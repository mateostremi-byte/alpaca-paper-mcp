from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from paper_runner import (
    Policy,
    SafetyBlock,
    ceil_cent,
    completed_bars,
    enter,
    flatten_orders,
    floor_cent,
    is_flatten_time,
    is_scan_time,
    sma,
    trend_metrics,
)
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


def bar(timestamp, close="30", open_price="30"):
    return {"t": timestamp, "c": close, "o": open_price}


def test_incomplete_bars_are_excluded():
    now = datetime(2026, 9, 16, 14, 30, tzinfo=timezone.utc)
    rows = [
        bar("2026-09-16T14:20:00Z"),
        bar("2026-09-16T14:25:00Z"),
        bar("2026-09-16T14:30:00Z"),
    ]
    assert [row["t"] for row in completed_bars(rows, now, timedelta(minutes=5))] == [
        "2026-09-16T14:20:00Z",
        "2026-09-16T14:25:00Z",
    ]


def test_trend_metrics_requires_close_above_rising_sma():
    rows = [bar(f"2026-09-{day:02d}T14:30:00Z", str(day)) for day in range(1, 22)]
    assert trend_metrics(rows, "test")["bullish"] is True


class FakeApi:
    def __init__(self):
        self.posts = []

    def post(self, path, body):
        self.posts.append((path, body))
        return {"id": "entry-id"}

    def get(self, path, params=None):
        return {
            "id": "entry-id",
            "status": "new",
            "legs": [{"id": "stop-id", "side": "sell", "type": "stop"}],
        }


def empty_state():
    return {"positions": [], "open_orders": [], "today_orders": []}


def test_entry_rejects_invalidation_wider_than_policy_before_submission():
    api = FakeApi()
    with pytest.raises(SafetyBlock, match="wider than policy"):
        enter(
            api,
            "SCHB",
            {"ask": Decimal("30")},
            empty_state(),
            Policy(),
            datetime(2026, 9, 16, 14, 0, tzinfo=timezone.utc),
            Decimal("28"),
        )
    assert api.posts == []


def test_entry_preserves_valid_thesis_invalidation():
    api = FakeApi()
    result = enter(
        api,
        "SCHB",
        {"ask": Decimal("30")},
        empty_state(),
        Policy(),
        datetime(2026, 9, 16, 14, 0, tzinfo=timezone.utc),
        Decimal("29.25"),
    )
    assert result["stop_price"] == "29.25"
    assert api.posts[0][1]["stop_loss"] == {"stop_price": "29.25"}
