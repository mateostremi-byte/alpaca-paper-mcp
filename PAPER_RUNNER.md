# Alpaca PAPER Agent Runner

This runner is an independent scheduled process. It does not depend on a ChatGPT
conversation staying open.

## Decision flow

1. Deterministic market checks rank only SCHB, SCHG, and SCHA signals whose
   completed one-hour and five-minute trends agree and that pass price,
   momentum, and spread rules.
2. A read-only news agent classifies recent Alpaca news.
3. Separate bull and bear agents argue the evidence.
4. A read-only risk agent returns a strict structured verdict and invalidation
   price after seeing the fixed position, loss, and stop-distance policy.
5. A deterministic gate checks the complete agent output again.
6. Only `paper_runner.py` can submit an Alpaca PAPER order.

TradeRank's public facts feed is included only as benchmark context. Rankings are
never translated into an order signal.

## Required environment

- `RUNNER_ENABLED=true`
- `ALPACA_PAPER_TRADE=true`
- `ALPACA_API_KEY`
- `ALPACA_SECRET_KEY`
- `OPENAI_API_KEY`
- `OPENAI_MODEL` (an account-enabled model that supports Structured Outputs)

Store secrets in Render. Do not commit them or paste them into chat.

## Fixed safeguards

- PAPER account only
- $200 experiment capital
- $50 maximum position
- One whole share per entry
- One open position and one new entry per day
- $4 maximum planned loss per trade
- $10 daily loss stop
- Five broker order records per day, including attached children
- Long-only, no margin, no overnight position
- Atomic OTO entry with a protective stop
- Thesis-based invalidation must fit inside the 3% maximum stop distance; the
  runner rejects the trade instead of moving the stop to force it to fit
- Any missing, refused, malformed, or contradictory agent result fails closed

The cron command is `python paper_runner.py`. Invoke it every five minutes; the
runner internally limits decision scans to 9:45 AM and each `:00`/`:30` through
3:30 PM America/New_York, with flattening beginning at 3:55 PM.
