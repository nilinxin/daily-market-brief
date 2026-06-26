from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import os
from typing import Iterable

import requests


@dataclass
class MarketQuote:
    name: str
    symbol: str
    price: str
    change_percent: str
    raw_change_percent: float | None
    note: str
    source: str


class ProviderBudget:
    def __init__(self) -> None:
        self.alpha_vantage_calls = 0
        self.alpha_vantage_limit = 5


def _format_price(value: float | None) -> str:
    if value is None:
        return "未取得"
    if abs(value) >= 100:
        return f"{value:,.2f}"
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _format_change(value: float | None) -> str:
    if value is None:
        return "未取得"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%"


def _note(change_percent: float | None) -> str:
    if change_percent is None:
        return "数据暂缺"
    if change_percent >= 1.2:
        return "明显走强"
    if change_percent >= 0.3:
        return "温和上涨"
    if change_percent <= -1.2:
        return "明显回落"
    if change_percent <= -0.3:
        return "小幅走弱"
    return "基本持平"


def _brief_error(exc: Exception) -> str:
    name = exc.__class__.__name__
    if "Proxy" in name or "proxy" in str(exc).lower():
        return "网络代理或连接失败"
    if "Timeout" in name or "timeout" in str(exc).lower():
        return "请求超时"
    text = str(exc).splitlines()[0]
    if len(text) > 60:
        text = text[:60] + "..."
    return f"{name}: {text}"


def _quote_from_values(name: str, symbol: str, price: float, previous_close: float | None, source: str, item_type: str) -> MarketQuote:
    change = None
    if previous_close and previous_close != 0:
        change = (price - previous_close) / previous_close * 100

    note = _note(change)
    if _is_suspicious_change(change, item_type):
        note = "波动异常，需核对"
        change = None

    return MarketQuote(
        name=name,
        symbol=symbol,
        price=_format_price(price),
        change_percent=_format_change(change),
        raw_change_percent=change,
        note=note,
        source=source,
    )


def _empty_quote(name: str, symbol: str, source: str = "未取得") -> MarketQuote:
    return MarketQuote(
        name=name,
        symbol=symbol,
        price="未取得",
        change_percent="未取得",
        raw_change_percent=None,
        note="数据暂缺",
        source=source,
    )


def _is_suspicious_change(change: float | None, item_type: str) -> bool:
    if change is None:
        return False
    limit_by_type = {
        "stock": 30,
        "index": 8,
        "future": 12,
        "forex": 3,
        "commodity": 15,
    }
    return abs(change) > limit_by_type.get(item_type, 20)


def _is_us_stock(item: dict) -> bool:
    symbol = item["symbol"]
    return item.get("type") == "stock" and symbol.isalpha()


def _fetch_yahoo_quote(session: requests.Session, item: dict) -> MarketQuote:
    name = item["name"]
    symbol = item["symbol"]
    item_type = item.get("type", "unknown")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    response = session.get(url, params={"range": "5d", "interval": "1d"}, timeout=20)
    response.raise_for_status()
    payload = response.json()
    result = payload.get("chart", {}).get("result") or []
    if not result:
        raise ValueError("no chart data returned")

    chart = result[0]
    quote = (chart.get("indicators", {}).get("quote") or [{}])[0]
    closes = [value for value in quote.get("close", []) if value is not None]
    if not closes:
        raise ValueError("no close price returned")

    close = float(closes[-1])
    meta = chart.get("meta", {})
    previous_close = float(closes[-2]) if len(closes) >= 2 else _meta_previous_close(meta)
    return _quote_from_values(name, symbol, close, previous_close, "Yahoo Finance", item_type)


def _meta_previous_close(meta: dict) -> float | None:
    for key in ("previousClose", "chartPreviousClose", "regularMarketPreviousClose"):
        value = meta.get(key)
        if value:
            return float(value)
    return None


def _fetch_finnhub_quote(session: requests.Session, item: dict) -> MarketQuote:
    token = os.getenv("FINNHUB_API_KEY", "").strip()
    if not token or not _is_us_stock(item):
        raise ValueError("skipped")
    response = session.get(
        "https://finnhub.io/api/v1/quote",
        params={"symbol": item["symbol"], "token": token},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    price = float(payload.get("c") or 0)
    previous_close = float(payload.get("pc") or 0)
    if price <= 0:
        raise ValueError("no quote returned")
    return _quote_from_values(item["name"], item["symbol"], price, previous_close, "Finnhub free API", item.get("type", "stock"))


def _fetch_alpha_vantage_quote(session: requests.Session, item: dict, budget: ProviderBudget) -> MarketQuote:
    token = os.getenv("ALPHA_VANTAGE_API_KEY", "").strip()
    if not token or not _is_us_stock(item) or budget.alpha_vantage_calls >= budget.alpha_vantage_limit:
        raise ValueError("skipped")
    budget.alpha_vantage_calls += 1
    response = session.get(
        "https://www.alphavantage.co/query",
        params={"function": "GLOBAL_QUOTE", "symbol": item["symbol"], "apikey": token},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if "Note" in payload or "Information" in payload:
        raise ValueError("rate limit or unavailable")
    quote = payload.get("Global Quote") or {}
    price = float(quote.get("05. price") or 0)
    previous_close = float(quote.get("08. previous close") or 0)
    if price <= 0:
        raise ValueError("no quote returned")
    return _quote_from_values(item["name"], item["symbol"], price, previous_close, "Alpha Vantage free API", item.get("type", "stock"))


def _fetch_twelve_data_quote(session: requests.Session, item: dict) -> MarketQuote:
    token = os.getenv("TWELVE_DATA_API_KEY", "").strip()
    symbol = item.get("twelve_symbol") or (item["symbol"] if _is_us_stock(item) else "")
    if not token or not symbol:
        raise ValueError("skipped")
    response = session.get(
        "https://api.twelvedata.com/quote",
        params={"symbol": symbol, "apikey": token},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") == "error":
        raise ValueError(payload.get("message", "quote unavailable"))
    price = float(payload.get("close") or payload.get("price") or 0)
    previous_close = float(payload.get("previous_close") or 0)
    if price <= 0:
        raise ValueError("no quote returned")
    return _quote_from_values(item["name"], item["symbol"], price, previous_close, "Twelve Data free API", item.get("type", "unknown"))


def _fetch_tiingo_quote(session: requests.Session, item: dict) -> MarketQuote:
    token = os.getenv("TIINGO_API_KEY", "").strip()
    if not token or not _is_us_stock(item):
        raise ValueError("skipped")
    start = (date.today() - timedelta(days=12)).isoformat()
    response = session.get(
        f"https://api.tiingo.com/tiingo/daily/{item['symbol'].lower()}/prices",
        params={"startDate": start, "token": token},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list) or not payload:
        raise ValueError("no quote returned")
    closes = [float(row["close"]) for row in payload if row.get("close") is not None]
    if not closes:
        raise ValueError("no close price returned")
    previous_close = closes[-2] if len(closes) >= 2 else None
    return _quote_from_values(item["name"], item["symbol"], closes[-1], previous_close, "Tiingo free API", item.get("type", "stock"))


def _provider_names() -> list[str]:
    names = ["Yahoo Finance"]
    if os.getenv("FINNHUB_API_KEY", "").strip():
        names.append("Finnhub free API")
    if os.getenv("ALPHA_VANTAGE_API_KEY", "").strip():
        names.append("Alpha Vantage free API")
    if os.getenv("TWELVE_DATA_API_KEY", "").strip():
        names.append("Twelve Data free API")
    if os.getenv("TIINGO_API_KEY", "").strip():
        names.append("Tiingo free API")
    return names


def fetch_quotes(items: Iterable[dict], errors: list[str]) -> list[MarketQuote]:
    quotes: list[MarketQuote] = []
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 market-brief-bot/1.0"})
    budget = ProviderBudget()
    for item in items:
        name = item["name"]
        symbol = item["symbol"]
        provider_errors: list[str] = []
        for provider in (
            lambda: _fetch_yahoo_quote(session, item),
            lambda: _fetch_finnhub_quote(session, item),
            lambda: _fetch_twelve_data_quote(session, item),
            lambda: _fetch_tiingo_quote(session, item),
            lambda: _fetch_alpha_vantage_quote(session, item, budget),
        ):
            try:
                quotes.append(provider())
                break
            except Exception as exc:  # noqa: BLE001
                if str(exc) != "skipped":
                    provider_errors.append(_brief_error(exc))
        else:
            if provider_errors:
                errors.append(f"{name}（{symbol}）行情抓取失败：{provider_errors[0]}")
            quotes.append(_empty_quote(name, symbol, " / ".join(_provider_names())))
    return quotes


def configured_market_sources() -> list[str]:
    return _provider_names()
