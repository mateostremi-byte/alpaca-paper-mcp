from __future__ import annotations
import asyncio
import json
import os
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

class RiskControls:
    """Fail-closed guardrails for the 200 dollar paper-trading experiment."""
    def __init__(self) -> None:
        self.capital = Decimal(os.getenv("TRADING_CAPITAL_LIMIT", "200"))
        self.max_position = Decimal(os.getenv("MAX_POSITION_NOTIONAL", "50"))
        self.max_risk = Decimal(os.getenv("MAX_RISK_PER_TRADE", "4"))
        self.daily_loss = Decimal(os.getenv("DAILY_LOSS_LIMIT", "10"))
        self.max_orders = int(os.getenv("MAX_ORDERS_PER_DAY", "5"))
        self.log_path = Path(os.getenv("TRADE_LOG_PATH", "/tmp/alpaca_trade_log.jsonl"))
        self.lock = asyncio.Lock()

    @staticmethod
    def dec(value: Any) -> Decimal | None:
        try:
            return Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return None

    async def _orders_today(self, client: Any) -> list[dict[str, Any]]:
        midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        response = await client.get("/v2/orders", params={"status": "all", "after": midnight, "direction": "asc", "limit": "500"})
        response.raise_for_status()
        value = response.json()
        return value if isinstance(value, list) else []

    async def _exposure(self, client: Any) -> Decimal:
        response = await client.get("/v2/positions")
        response.raise_for_status()
        total = Decimal("0")
        for position in response.json() or []:
            value = self.dec(position.get("market_value"))
            if value is not None and value > 0:
                total += value
        response = await client.get("/v2/orders", params={"status": "open", "side": "buy", "limit": "500"})
        response.raise_for_status()
        for order in response.json() or []:
            value = self.dec(order.get("notional"))
            if value is None:
                qty, price = self.dec(order.get("qty")), self.dec(order.get("limit_price"))
                if qty is not None and price is not None:
                    value = qty * price
            if value is not None and value > 0:
                total += value
        return total

    async def _loss(self, client: Any) -> Decimal:
        response = await client.get("/v2/account/portfolio/history", params={"period": "1D", "timeframe": "1Min", "extended_hours": "true"})
        if response.is_error:
            return Decimal("0")
        values = (response.json() or {}).get("profit_loss") or []
        values = [self.dec(value) for value in values]
        values = [value for value in values if value is not None]
        return max(Decimal("0"), -values[-1]) if values else Decimal("0")

    async def preflight(self, client: Any, body: dict[str, Any], asset_class: str) -> dict[str, Any] | None:
        async with self.lock:
            if os.getenv("ALPACA_PAPER_TRADE", "true").lower() not in {"true", "1", "yes"}:
                return {"message": "Paper mode is required."}
            if asset_class != "stock":
                return {"message": "Only stock and ETF orders are enabled for this experiment."}
            try:
                if len(await self._orders_today(client)) >= self.max_orders:
                    return {"message": "Daily order limit reached: " + str(self.max_orders) + "."}
                if await self._loss(client) >= self.daily_loss:
                    return {"message": "Daily loss stop reached: " + str(self.daily_loss) + " dollars."}
                if str(body.get("side", "")).lower() != "buy":
                    return None
                notional = self.dec(body.get("notional"))
                if notional is None:
                    qty, price = self.dec(body.get("qty")), self.dec(body.get("limit_price"))
                    if qty is not None and price is not None:
                        notional = qty * price
                if notional is None:
                    return {"message": "Buys require notional or qty plus limit_price."}
                if notional > self.max_position:
                    return {"message": "Buy exceeds the position limit: " + str(self.max_position) + " dollars."}
                if await self._exposure(client) + notional > self.capital:
                    return {"message": "Buy would exceed the 200 dollar total exposure limit."}
                entry = self.dec(body.get("limit_price"))
                stop = self.dec((body.get("stop_loss") or {}).get("stop_price"))
                if entry is None or stop is None or stop >= entry:
                    return {"message": "Every buy requires a limit price and stop-loss below entry."}
                risk = notional * (entry - stop) / entry
                if risk > self.max_risk:
                    return {"message": "Planned loss exceeds the " + str(self.max_risk) + " dollar trade-risk limit."}
            except Exception as exc:
                return {"message": "Risk check failed closed: " + type(exc).__name__ + "."}
        return None

    async def record(self, body: dict[str, Any], result: dict[str, Any]) -> None:
        event = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "symbol": body.get("symbol"), "side": body.get("side"), "notional": body.get("notional"), "qty": body.get("qty"), "type": body.get("type"), "reason": "order_submitted", "result_status": result.get("status") if isinstance(result, dict) else None}
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, default=str) + "\n")
        except OSError:
            pass
