# Paper experiment: NOT cleared to trade

The current patch is a containment change, not a finished trading risk engine.
Every broker mutation is blocked at the shared HTTP request hook. Account and
market-data reads remain available. No configuration toggle unlocks trading.

Verified with network-free stdlib regression tests:

    python -m unittest discover -s tests -p test_trading_gate_standalone.py -v

The tests cover read access, blocking broker writes (including replacement,
close-position, exercise and account configuration), live/untrusted hosts,
nonfinite numbers, unavailable loss data and failed loss API calls. These are
unit tests, not proof of deployment or end-to-end readiness.

## Still required before replacing the lock with an authorizer

- Durable free journal selected and connected with explicit approval. Never put
  account logs or credentials in this public repository. Local /tmp is not durable.
- Atomic journal reservation spanning validation, submission and reconciliation;
  unique client order IDs; uncertain outcomes stop retries and new orders.
- Full $200 experiment ledger independent of the account's larger paper balance.
- $100 absolute per-trade cap; clarify any stricter $50 operating cap.
- Five daily orders, New York timezone, counting convention and reserved exit
  capacity. Never silently make a sixth order or leave a position unprotected.
- Actual asset verification, long-only sizing, aggregate pending exposure and
  holdings checks. No short sales, margin spending, crypto or options.
- User-confirmed daily/cumulative loss and profit thresholds; distinguish profit
  from total account value. Stops do not guarantee the maximum realized loss.
- Real trade rationale, quote timestamp, requested quantity/price, broker IDs,
  actual fills, fees, realized/unrealized P&L and reconciliation in the journal.
- Mocked HTTP and full MCP tests, restart/concurrency/failure tests, and verified
  paper deployment. No real orders used as negative tests.

Render was last observed pointing at the upstream repository, not this fork.
Do not describe these changes as active in Render without verifying deployment.
No paid resources or trading automation have been created by this patch.
