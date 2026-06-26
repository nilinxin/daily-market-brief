from __future__ import annotations

from dataclasses import dataclass
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


def fetch_quotes(items: Iterable[dict], errors: list[str]) -> list[MarketQuote]:
    quotes: list[MarketQuote] = []
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 market-brief-bot/1.0"})
    for item in items:
        name = item["name"]
        symbol = item["symbol"]
        try:
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
            previous_close = float(closes[-2]) if len(closes) >= 2 else None

            change = None
            if previous_close and previous_close != 0:
                change = (close - previous_close) / previous_close * 100

            quotes.append(
                MarketQuote(
                    name=name,
                    symbol=symbol,
                    price=_format_price(close),
                    change_percent=_format_change(change),
                    raw_change_percent=change,
                    note=_note(change),
                    source="Yahoo Finance public chart endpoint",
                )
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}（{symbol}）行情抓取失败：{_brief_error(exc)}")
            quotes.append(
                MarketQuote(
                    name=name,
                    symbol=symbol,
                    price="未取得",
                    change_percent="未取得",
                    raw_change_percent=None,
                    note="数据暂缺",
                    source="Yahoo Finance public chart endpoint",
                )
            )
    return quotes
