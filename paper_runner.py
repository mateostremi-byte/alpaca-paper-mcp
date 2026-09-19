"""Scheduled Alpaca PAPER runner for the $200 experiment.

This process is intentionally independent of ChatGPT automations. Render invokes
it every five minutes; the code itself decides whether the current tick is a
decision scan, an hourly report, or the end-of-day flattening pass.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from trading_agents import (
    AgentError,
    AgentTeam,
    agent_gate,
    technical_agent,
    traderank_context,
)

NY = ZoneInfo("America/New_York")
PAPER_BASE = "https://paper-api.alpaca.markets"
DATA_BASE = "https://data.alpaca.markets"
UNIVERSE = ("SCHB", "SCHG", "SCHA")
PREFIX = "mexp-"


class SafetyBlock(RuntimeError):
    """A fail-closed policy or verification failure."""


def money(value: Any) -> Decimal:
    return Decimal(str(value or "0"))


def ceil_cent(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_UP)


def floor_cent(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_DOWN)


def flatten_orders(orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()

    def visit(order: dict[str, Any]) -> None:
        order_id = str(order.get("id") or "")
        if order_id and order_id not in seen:
            seen.add(order_id)
            result.append(order)
        for leg in order.get("legs") or []:
            visit(leg)

    for item in orders:
        visit(item)
    return result


def sma(values: list[Decimal], length: int = 20) -> Decimal:
    if len(values) < length:
        raise SafetyBlock(f"Need at least {length} completed bars")
    return sum(values[-length:]) / Decimal(length)


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def regular_session_bar(row: dict[str, Any]) -> bool:
    local = parse_timestamp(str(row["t"])).astimezone(NY)
    return (local.hour, local.minute) >= (9, 30) and (local.hour, local.minute) < (16, 0)


def completed_bars(
    rows: list[dict[str, Any]], now: datetime, duration: timedelta
) -> list[dict[str, Any]]:
    """Return regular-session bars whose full interval has elapsed."""
    return [
        row
        for row in rows
        if regular_session_bar(row) and parse_timestamp(str(row["t"])) + duration <= now
    ]


def trend_metrics(rows: list[dict[str, Any]], label: str) -> dict[str, Any]:
    if len(rows) < 21:
        raise SafetyBlock(f"Insufficient completed {label} bars")
    closes = [money(row["c"]) for row in rows]
    current_sma = sma(closes[-20:])
    prior_sma = sma(closes[-21:-1])
    close = closes[-1]
    return {
        "close": close,
        "sma20": current_sma,
        "prior_sma20": prior_sma,
        "bar_time": rows[-1]["t"],
        "bullish": close > current_sma and current_sma > prior_sma,
    }


def is_scan_time(now: datetime) -> bool:
    local = now.astimezone(NY)
    return (local.hour == 9 and local.minute == 45) or local.minute in {0, 30}


def is_hourly_report_time(now: datetime) -> bool:
    return now.astimezone(NY).minute == 0


def is_flatten_time(now: datetime) -> bool:
    local = now.astimezone(NY)
    return local.hour == 15 and local.minute >= 55


@dataclass(frozen=True)
class Policy:
    capital: Decimal = Decimal("200")
    max_position: Decimal = Decimal("50")
    max_risk: Decimal = Decimal("4")
    daily_loss: Decimal = Decimal("10")
    max_orders: int = 5
    max_positions: int = 1  # reserves capacity for a same-day exit
    stop_pct: Decimal = Decimal("0.03")


class Alpaca:
    def __init__(self, key: str, secret: str) -> None:
        headers = {
            "APCA-API-KEY-ID": key,
            "APCA-API-SECRET-KEY": secret,
            "User-Agent": "mateo-paper-runner/1.0",
        }
        self.trading = httpx.Client(base_url=PAPER_BASE, headers=headers, timeout=25)
        self.data = httpx.Client(base_url=DATA_BASE, headers=headers, timeout=25)

    @staticmethod
    def _json(response: httpx.Response) -> Any:
        response.raise_for_status()
        return response.json()

    def get(self, path: str, params: dict[str, str] | None = None) -> Any:
        return self._json(self.trading.get(path, params=params))

    def data_get(self, path: str, params: dict[str, str] | None = None) -> Any:
        return self._json(self.data.get(path, params=params))

    def news(self, symbol: str, now: datetime) -> list[dict[str, Any]]:
        payload = self.data_get(
            "/v1beta1/news",
            {
                "symbols": symbol,
                "start": (now - timedelta(days=2)).isoformat().replace("+00:00", "Z"),
                "end": now.isoformat().replace("+00:00", "Z"),
                "sort": "desc",
                "limit": "10",
                "include_content": "false",
            },
        )
        output: list[dict[str, Any]] = []
        for row in payload.get("news") or []:
            output.append(
                {
                    "headline": str(row.get("headline") or "")[:300],
                    "summary": str(row.get("summary") or "")[:800],
                    "source": str(row.get("source") or "")[:100],
                    "created_at": row.get("created_at"),
                    "url": row.get("url"),
                }
            )
        return output

    def post(self, path: str, body: dict[str, Any]) -> Any:
        return self._json(self.trading.post(path, json=body))

    def delete(self, path: str) -> None:
        response = self.trading.delete(path)
        if response.status_code not in {200, 204}:
            response.raise_for_status()


def wait_for_order_status(api: Alpaca, order_id: str, expected: set[str]) -> dict[str, Any]:
    last: dict[str, Any] = {}
    for _ in range(6):
        last = api.get(f"/v2/orders/{order_id}", {"nested": "true"})
        if str(last.get("status")) in expected:
            return last
        time.sleep(0.5)
    raise SafetyBlock(
        f"Order {order_id} did not reach a verified state; last={last.get('status')}"
    )


def emit(status: dict[str, Any]) -> None:
    print("PAPER_RUNNER_REPORT " + json.dumps(status, default=str, sort_keys=True), flush=True)


def require_environment() -> tuple[str, str]:
    if os.getenv("RUNNER_ENABLED", "false").lower() not in {"1", "true", "yes"}:
        raise SafetyBlock("RUNNER_ENABLED is false")
    if os.getenv("ALPACA_PAPER_TRADE", "true").lower() not in {"1", "true", "yes"}:
        raise SafetyBlock("ALPACA_PAPER_TRADE must be true")
    key = os.getenv("ALPACA_API_KEY", "").strip()
    secret = os.getenv("ALPACA_SECRET_KEY", "").strip()
    if not key or not secret:
        raise SafetyBlock("Missing Alpaca PAPER API credentials")
    return key, secret


def preflight(api: Alpaca, policy: Policy, now: datetime) -> dict[str, Any]:
    local = now.astimezone(NY)
    after = local.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    account = api.get("/v2/account")
    config = api.get("/v2/account/configurations")
    clock = api.get("/v2/clock")
    positions = api.get("/v2/positions") or []
    open_orders = api.get("/v2/orders", {"status": "open", "nested": "true", "limit": "500"}) or []
    today = api.get(
        "/v2/orders",
        {"status": "all", "after": after, "direction": "asc", "nested": "true", "limit": "500"},
    ) or []

    checks = {
        "status": account.get("status") == "ACTIVE",
        "trading_unblocked": not bool(account.get("trading_blocked")),
        "shorting_disabled": not bool(account.get("shorting_enabled")),
        "multiplier_one": str(account.get("multiplier")) == "1",
        "no_shorting": bool(config.get("no_shorting")),
        "max_margin_one": str(config.get("max_margin_multiplier")) == "1",
        "overnight_disabled": bool(config.get("disable_overnight_trading")),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise SafetyBlock("Broker safeguards failed: " + ", ".join(failed))

    flat_today = flatten_orders(today)
    if len(flat_today) >= policy.max_orders:
        raise SafetyBlock(f"Daily order limit reached ({len(flat_today)}/{policy.max_orders})")

    account_day_pl = money(account.get("equity")) - money(account.get("last_equity"))
    if account_day_pl <= -policy.daily_loss:
        raise SafetyBlock(f"Daily loss stop reached ({account_day_pl})")

    return {
        "account": account,
        "clock": clock,
        "positions": positions,
        "open_orders": open_orders,
        "today_orders": flat_today,
        "checks": checks,
        "account_day_pl": account_day_pl,
    }


def bars_and_quotes(api: Alpaca, now: datetime) -> dict[str, dict[str, Any]]:
    five_minute_start = (now - timedelta(days=7)).isoformat().replace("+00:00", "Z")
    hourly_start = (now - timedelta(days=45)).isoformat().replace("+00:00", "Z")
    end = now.isoformat().replace("+00:00", "Z")
    symbols = ",".join(UNIVERSE)
    five_minute_payload = api.data_get(
        "/v2/stocks/bars",
        {
            "symbols": symbols,
            "timeframe": "5Min",
            "start": five_minute_start,
            "end": end,
            "limit": "10000",
            "adjustment": "raw",
            "feed": "iex",
            "sort": "asc",
        },
    )
    hourly_payload = api.data_get(
        "/v2/stocks/bars",
        {
            "symbols": symbols,
            "timeframe": "1Hour",
            "start": hourly_start,
            "end": end,
            "limit": "10000",
            "adjustment": "raw",
            "feed": "iex",
            "sort": "asc",
        },
    )
    quote_payload = api.data_get(
        "/v2/stocks/quotes/latest", {"symbols": symbols, "feed": "iex"}
    )
    all_five_minute_bars = five_minute_payload.get("bars") or {}
    all_hourly_bars = hourly_payload.get("bars") or {}
    quotes = quote_payload.get("quotes") or {}
    output: dict[str, dict[str, Any]] = {}

    for symbol in UNIVERSE:
        five_minute_rows = completed_bars(
            all_five_minute_bars.get(symbol) or [], now, timedelta(minutes=5)
        )
        hourly_rows = completed_bars(
            all_hourly_bars.get(symbol) or [], now, timedelta(hours=1)
        )
        five_minute = trend_metrics(five_minute_rows, f"5-minute bars for {symbol}")
        hourly = trend_metrics(hourly_rows, f"hourly bars for {symbol}")
        local_day = now.astimezone(NY).date()
        session = [
            row
            for row in five_minute_rows
            if parse_timestamp(str(row["t"])).astimezone(NY).date() == local_day
        ]
        if not session:
            raise SafetyBlock(f"No completed current-session bars for {symbol}")
        session_open = money(session[0]["o"])
        close = five_minute["close"]
        quote = quotes.get(symbol) or {}
        bid, ask = money(quote.get("bp")), money(quote.get("ap"))
        if bid <= 0 or ask <= 0 or ask < bid:
            raise SafetyBlock(f"Invalid quote for {symbol}")
        midpoint = (bid + ask) / Decimal("2")
        spread_bps = ((ask - bid) / midpoint) * Decimal("10000")
        session_return = (close / session_open) - Decimal("1")
        timeframes_agree = bool(hourly["bullish"] and five_minute["bullish"])
        output[symbol] = {
            "close": close,
            "sma20": five_minute["sma20"],
            "prior_sma20": five_minute["prior_sma20"],
            "session_open": session_open,
            "session_return": session_return,
            "bid": bid,
            "ask": ask,
            "spread_bps": spread_bps,
            "bar_time": five_minute["bar_time"],
            "five_minute_trend": five_minute,
            "hourly_trend": hourly,
            "timeframes_agree": timeframes_agree,
            "qualifies": timeframes_agree
            and session_return > 0
            and ask <= Decimal("50")
            and spread_bps <= Decimal("10"),
        }
    return output


def cancel_protection_and_exit(api: Alpaca, state: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    open_orders = flatten_orders(state["open_orders"])
    for position in state["positions"]:
        symbol = position.get("symbol")
        if symbol not in UNIVERSE:
            raise SafetyBlock(f"Unattributable position present: {symbol}")
        qty = money(position.get("qty"))
        for order in open_orders:
            if order.get("symbol") == symbol and order.get("side") == "sell":
                api.delete(f"/v2/orders/{order['id']}")
                wait_for_order_status(api, str(order["id"]), {"canceled", "expired"})
                actions.append({"action": "cancel_protection", "order_id": order["id"], "symbol": symbol})
        client_id = f"{PREFIX}{now.astimezone(NY):%Y%m%d-%H%M}-{symbol}-exit"
        result = api.post(
            "/v2/orders",
            {
                "symbol": symbol,
                "side": "sell",
                "qty": str(qty),
                "type": "market",
                "time_in_force": "day",
                "extended_hours": False,
                "client_order_id": client_id,
            },
        )
        actions.append({"action": "exit", "symbol": symbol, "order_id": result.get("id")})
    return actions


def enter(
    api: Alpaca,
    symbol: str,
    signal: dict[str, Any],
    state: dict[str, Any],
    policy: Policy,
    now: datetime,
    invalidation_price: Decimal,
) -> dict[str, Any]:
    if state["positions"] or state["open_orders"]:
        raise SafetyBlock("An open position or pending order already exists")
    mexp_today = [o for o in state["today_orders"] if str(o.get("client_order_id") or "").startswith(PREFIX)]
    if any(o.get("side") == "buy" for o in mexp_today):
        raise SafetyBlock("One-entry-per-day reserve is already used")
    if len(state["today_orders"]) + 2 > policy.max_orders:
        raise SafetyBlock("Insufficient order slots for entry plus protective child")

    limit_price = ceil_cent(signal["ask"] * Decimal("1.001"))
    fixed_stop = floor_cent(limit_price * (Decimal("1") - policy.stop_pct))
    proposed_stop = floor_cent(invalidation_price)
    minimum_gap = Decimal("0.0025")
    if proposed_stop <= 0 or proposed_stop >= limit_price * (Decimal("1") - minimum_gap):
        raise SafetyBlock("Risk-agent invalidation price is not safely below entry")
    if proposed_stop < fixed_stop:
        raise SafetyBlock("Thesis invalidation requires a stop wider than policy permits")
    # Preserve the thesis-derived invalidation exactly; never move it merely to fit policy.
    stop_price = proposed_stop
    planned_loss = limit_price - stop_price
    if limit_price > policy.max_position or planned_loss > policy.max_risk:
        raise SafetyBlock("Entry sizing or planned loss exceeds policy")

    client_id = f"{PREFIX}{now.astimezone(NY):%Y%m%d-%H%M}-{symbol}-buy"
    result = api.post(
        "/v2/orders",
        {
            "symbol": symbol,
            "side": "buy",
            "qty": "1",
            "type": "limit",
            "time_in_force": "day",
            "limit_price": str(limit_price),
            "order_class": "oto",
            "stop_loss": {"stop_price": str(stop_price)},
            "extended_hours": False,
            "client_order_id": client_id,
        },
    )
    verified = wait_for_order_status(
        api,
        str(result.get("id") or ""),
        {"new", "accepted", "pending_new", "partially_filled", "filled"},
    )
    protective_legs = [
        leg
        for leg in verified.get("legs") or []
        if leg.get("side") == "sell" and leg.get("type") in {"stop", "stop_limit"}
    ]
    if len(protective_legs) != 1:
        # The order class was requested atomically. If Alpaca did not return the
        # child, stop immediately and surface the ambiguous state for review.
        raise SafetyBlock("Accepted entry lacks one verified protective stop child")
    return {
        "action": "entry_requested",
        "symbol": symbol,
        "limit_price": str(limit_price),
        "stop_price": str(stop_price),
        "planned_loss": str(planned_loss),
        "client_order_id": client_id,
        "order_id": result.get("id"),
        "status": verified.get("status"),
        "protective_order_id": protective_legs[0].get("id"),
    }


def run() -> int:
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    report: dict[str, Any] = {
        "timestamp_et": now.astimezone(NY).isoformat(),
        "mode": "PAPER",
        "verified": False,
        "action": "none",
    }
    try:
        key, secret = require_environment()
        api = Alpaca(key, secret)
        policy = Policy()
        state = preflight(api, policy, now)
        report.update(
            {
                "market_open": bool(state["clock"].get("is_open")),
                "positions": state["positions"],
                "open_orders": flatten_orders(state["open_orders"]),
                "orders_today": len(state["today_orders"]),
                "account_day_pl": str(state["account_day_pl"]),
                "broker_safeguards": state["checks"],
                "verified": True,
            }
        )

        if is_flatten_time(now) and state["positions"] and state["clock"].get("is_open"):
            report["actions"] = cancel_protection_and_exit(api, state, now)
            report["action"] = "flatten"
        elif is_scan_time(now) and state["clock"].get("is_open"):
            local = now.astimezone(NY)
            if (local.hour, local.minute) < (9, 45) or (local.hour, local.minute) > (15, 30):
                report["action"] = "outside_entry_window"
            else:
                signals = bars_and_quotes(api, now)
                report["signals"] = signals
                technical = technical_agent(signals)
                report["technical_agent"] = technical
                candidate = technical["candidate"]
                if candidate:
                    context = traderank_context()
                    news = api.news(candidate, now)
                    agents = AgentTeam.from_environment().analyze(
                        symbol=candidate,
                        technical=technical,
                        news=news,
                        traderank=context,
                        policy={
                            "side": "long",
                            "quantity": "1 whole share",
                            "experiment_capital": str(policy.capital),
                            "max_position_dollars": str(policy.max_position),
                            "max_planned_loss_dollars": str(policy.max_risk),
                            "max_stop_distance_pct": str(policy.stop_pct * Decimal("100")),
                            "atomic_protective_stop_required": True,
                        },
                    )
                    report["traderank_agent"] = context
                    report["agents"] = agents
                    risk = agents["risk"]
                    approved, gate_reason = agent_gate(
                        agents, ask=signals[candidate]["ask"]
                    )
                    report["agent_gate"] = {
                        "approved": approved,
                        "reason": gate_reason,
                    }
                    if not approved:
                        report["action"] = "risk_veto"
                    else:
                        report["order"] = enter(
                            api,
                            candidate,
                            signals[candidate],
                            state,
                            policy,
                            now,
                            Decimal(str(risk["invalidation_price"])),
                        )
                        report["action"] = "entry_requested"
                else:
                    report["action"] = "no_signal"
        elif not state["clock"].get("is_open"):
            report["action"] = "market_closed"
        else:
            report["action"] = "heartbeat"

        if is_hourly_report_time(now) or is_scan_time(now) or is_flatten_time(now):
            emit(report)
        return 0
    except SafetyBlock as exc:
        report["action"] = "safety_block"
        report["error"] = str(exc)
        emit(report)
        return 0
    except AgentError as exc:
        report["action"] = "agent_veto"
        report["error"] = str(exc)
        emit(report)
        return 0
    except Exception as exc:
        report["action"] = "error"
        report["error"] = f"{type(exc).__name__}: {exc}"
        emit(report)
        return 1


if __name__ == "__main__":
    sys.exit(run())
