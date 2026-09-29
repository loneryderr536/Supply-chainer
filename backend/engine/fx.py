"""
Currency conversion for display. All engine costs are computed in USD; this module converts
them for the dashboard and reports. Live rates come from open.er-api.com (no key) and are cached;
when offline, approximate reference rates are used and labelled as such.
"""
import threading
import time
from typing import Any, Dict

import requests

SUPPORTED = ["USD", "EUR", "GBP", "INR", "CNY", "JPY", "AED", "SGD"]
SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "INR": "₹", "CNY": "CN¥", "JPY": "¥", "AED": "AED ", "SGD": "S$"}
# Approximate reference rates (USD -> currency) used only when live rates are unavailable.
REFERENCE_RATES = {"USD": 1.0, "EUR": 0.92, "GBP": 0.79, "INR": 83.5, "CNY": 7.2, "JPY": 150.0,
                   "AED": 3.6725, "SGD": 1.35}
LIVE_URL = "https://open.er-api.com/v6/latest/USD"


class FXRates:
    def __init__(self, ttl_s: int = 12 * 3600, timeout: float = 5.0, live: bool = True):
        self.ttl_s = ttl_s
        self.timeout = timeout
        self.live = live
        self._lock = threading.Lock()
        self._cache: Dict[str, Any] = {}

    def rates(self) -> Dict[str, Any]:
        with self._lock:
            if self._cache and time.time() - self._cache["fetched_at"] < self.ttl_s:
                return self._cache
        result = None
        if self.live:
            try:
                data = requests.get(LIVE_URL, timeout=self.timeout).json()
                if data.get("result") == "success":
                    result = {"base": "USD", "rates": {c: data["rates"][c] for c in SUPPORTED if c in data["rates"]},
                              "source": "live (open.er-api.com)", "as_of": data.get("time_last_update_utc"),
                              "fetched_at": time.time()}
            except Exception:
                result = None
        if result is None:
            # Don't cache the fallback for long, so live rates are picked up once reachable.
            result = {"base": "USD", "rates": dict(REFERENCE_RATES), "source": "approximate reference rates (offline)",
                      "as_of": None, "fetched_at": time.time() - self.ttl_s + 600}
        result["symbols"] = {c: SYMBOLS[c] for c in result["rates"]}
        with self._lock:
            self._cache = result
        return result

    def convert(self, usd: float, currency: str) -> float:
        return usd * self.rates()["rates"].get(currency, 1.0)

    def format(self, usd: float, currency: str, ascii_safe: bool = False) -> str:
        """`ascii_safe` swaps symbols a basic PDF font can't draw (₹) for the ISO code."""
        cur = currency if currency in SUPPORTED else "USD"
        value = self.convert(usd, cur)
        decimals = 0 if cur in ("JPY", "INR") or abs(value) >= 1000 else 2
        symbol = SYMBOLS[cur]
        if ascii_safe:
            try:
                symbol.encode("cp1252")
            except UnicodeEncodeError:
                symbol = f"{cur} "
        return f"{symbol}{value:,.{decimals}f}"
