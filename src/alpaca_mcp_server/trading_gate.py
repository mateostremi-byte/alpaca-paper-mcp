"""Transport-level quarantine until the experiment journal is integrated.

This is deliberately not a trade authorizer. There is no environment-variable
override: incomplete controls must not become a switch that enables trading.
All generated tools and handwritten overrides share this request hook.
"""

PAPER_HOST = "paper-api.alpaca.markets"
DATA_HOST = "data.alpaca.markets"


async def require_safe_request(request):
    """Reject credentials sent to other hosts and every broker mutation."""
    url = request.url
    if url.scheme != "https" or url.host not in {PAPER_HOST, DATA_HOST}:
        raise RuntimeError("Only official Alpaca paper and market-data hosts are allowed.")
    if url.port not in {None, 443}:
        raise RuntimeError("Unexpected Alpaca API port.")
    if request.method.upper() not in {"GET", "HEAD"}:
        raise RuntimeError(
            "Trading locked: durable audit journal and complete risk checks "
            "have not passed end-to-end verification. No request was sent."
        )
