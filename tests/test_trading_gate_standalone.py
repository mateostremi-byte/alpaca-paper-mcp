"""Network-free quarantine regression tests; run with stdlib unittest."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from urllib.parse import urlsplit


def load_module(name):
    path = Path(__file__).parents[1] / "src/alpaca_mcp_server" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = load_module("trading_gate")
risk = load_module("risk_controls")


def request(method, url):
    parsed = urlsplit(url)
    return SimpleNamespace(method=method, url=SimpleNamespace(
        scheme=parsed.scheme, host=parsed.hostname, port=parsed.port))


class GateTests(unittest.IsolatedAsyncioTestCase):
    async def test_paper_reads_allowed(self):
        await gate.require_safe_request(request("GET", "https://paper-api.alpaca.markets/v2/account"))

    async def test_data_reads_allowed(self):
        await gate.require_safe_request(request("GET", "https://data.alpaca.markets/v2/stocks/bars"))

    async def test_every_write_path_blocked(self):
        for method, path in [("POST", "/v2/orders"), ("PATCH", "/v2/orders/id"),
                             ("DELETE", "/v2/positions"), ("DELETE", "/v2/positions/AAPL"),
                             ("POST", "/v2/positions/id/exercise"),
                             ("PATCH", "/v2/account/configurations"),
                             ("DELETE", "/v2/orders"), ("POST", "/v2/locates"),
                             ("PUT", "/future-endpoint")]:
            with self.subTest(method=method, path=path):
                with self.assertRaisesRegex(RuntimeError, "Trading locked"):
                    await gate.require_safe_request(request(method, "https://paper-api.alpaca.markets" + path))

    async def test_live_and_untrusted_hosts_blocked(self):
        for url in ["https://api.alpaca.markets/v2/account", "http://paper-api.alpaca.markets",
                    "https://paper-api.alpaca.markets.evil.example", "https://example.org",
                    "https://paper-api.alpaca.markets:8443"]:
            with self.subTest(url=url), self.assertRaises(RuntimeError):
                await gate.require_safe_request(request("GET", url))

    def test_nonfinite_numbers_rejected(self):
        for value in ["NaN", "Infinity", "-Infinity", None, "garbage"]:
            self.assertIsNone(risk.RiskControls.dec(value))

    async def test_missing_loss_data_rejected(self):
        class Response:
            def raise_for_status(self): pass
            def json(self): return {"profit_loss": []}
        class Client:
            async def get(self, *args, **kwargs): return Response()
        with self.assertRaises(ValueError):
            await risk.RiskControls()._loss(Client())

    async def test_loss_api_failure_not_zero(self):
        class Response:
            def raise_for_status(self): raise RuntimeError("API unavailable")
        class Client:
            async def get(self, *args, **kwargs): return Response()
        with self.assertRaises(RuntimeError):
            await risk.RiskControls()._loss(Client())


if __name__ == "__main__":
    unittest.main()
