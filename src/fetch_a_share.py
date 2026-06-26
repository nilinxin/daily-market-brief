from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable
import json

import requests


@dataclass
class AShareIndex:
    name: str
    price: str
    change_percent: str
    raw_change_percent: float | None
    amount: str
    breadth: str
    note: str


@dataclass
class AShareBoard:
    name: str
    change_percent: str
    note: str


A_SHARE_INDICES = [
    {"name": "上证指数", "secid": "1.000001"},
    {"name": "深证成指", "secid": "0.399001"},
    {"name": "创业板指", "secid": "0.399006"},
    {"name": "科创50", "secid": "1.000688"},
    {"name": "北证50", "secid": "0.899050"},
]


def _format_price(value: float | int | str | None) -> str:
    number = _to_float(value)
    if number is None:
        return "暂缺"
    return f"{number:,.2f}"


def _format_percent(value: float | int | str | None) -> str:
    number = _to_float(value)
    if number is None:
        return "暂缺"
    sign = "+" if number > 0 else ""
    return f"{sign}{number:.2f}%"


def _format_amount(value: float | int | str | None) -> str:
    number = _to_float(value)
    if number is None:
        return "暂缺"
    return f"{number / 100000000:.0f}亿"


def _to_float(value: float | int | str | None) -> float | None:
    if value in (None, "-", ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _note(change_percent: float | None) -> str:
    if change_percent is None:
        return "数据暂缺"
    if change_percent >= 1.0:
        return "明显走强"
    if change_percent >= 0.3:
        return "小幅走强"
    if change_percent <= -1.0:
        return "明显走弱"
    if change_percent <= -0.3:
        return "小幅走弱"
    return "窄幅震荡"


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


def _fetch_json(session: requests.Session, url: str, params: dict) -> dict:
    response = session.get(url, params=params, timeout=20)
    response.raise_for_status()
    text = response.text.strip()
    if text.startswith("jQuery"):
        text = text[text.find("(") + 1 : text.rfind(")")]
    return response.json() if text == response.text.strip() else json.loads(text)


def fetch_a_share_indices(errors: list[str]) -> list[AShareIndex]:
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 market-brief-bot/1.0"})
    try:
        payload = _fetch_json(
            session,
            "https://push2.eastmoney.com/api/qt/ulist.np/get",
            {
                "fltt": "2",
                "invt": "2",
                "fields": "f2,f3,f6,f12,f14,f104,f105,f106",
                "secids": ",".join(item["secid"] for item in A_SHARE_INDICES),
            },
        )
        rows = payload.get("data", {}).get("diff") or []
        by_name = {row.get("f14"): row for row in rows}
        return [_index_from_row(item["name"], by_name.get(item["name"], {})) for item in A_SHARE_INDICES]
    except Exception as exc:  # noqa: BLE001
        errors.append(f"A股指数行情抓取失败：{_brief_error(exc)}")
        return [_empty_index(item["name"]) for item in A_SHARE_INDICES]


def _index_from_row(name: str, row: dict) -> AShareIndex:
    change = _to_float(row.get("f3"))
    rise = row.get("f104")
    fall = row.get("f105")
    flat = row.get("f106")
    if rise in (None, "-") and fall in (None, "-"):
        breadth = "暂缺"
    else:
        breadth = f"涨{rise or 0} / 跌{fall or 0} / 平{flat or 0}"
    return AShareIndex(
        name=name,
        price=_format_price(row.get("f2")),
        change_percent=_format_percent(change),
        raw_change_percent=change,
        amount=_format_amount(row.get("f6")),
        breadth=breadth,
        note=_note(change),
    )


def _empty_index(name: str) -> AShareIndex:
    return AShareIndex(
        name=name,
        price="暂缺",
        change_percent="暂缺",
        raw_change_percent=None,
        amount="暂缺",
        breadth="暂缺",
        note="数据暂缺",
    )


def fetch_a_share_boards(errors: list[str]) -> tuple[list[AShareBoard], list[AShareBoard]]:
    strong = _fetch_boards(errors, sort_desc=True)
    weak = _fetch_boards(errors, sort_desc=False)
    return strong, weak


def _fetch_boards(errors: list[str], sort_desc: bool) -> list[AShareBoard]:
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 market-brief-bot/1.0"})
    try:
        payload = _fetch_json(
            session,
            "https://push2.eastmoney.com/api/qt/clist/get",
            {
                "pn": "1",
                "pz": "6",
                "po": "1" if sort_desc else "0",
                "np": "1",
                "fltt": "2",
                "invt": "2",
                "fid": "f3",
                "fs": "m:90+t:2",
                "fields": "f3,f12,f14",
            },
        )
        rows = payload.get("data", {}).get("diff") or []
        return [_board_from_row(row) for row in rows if row.get("f14")]
    except Exception as exc:  # noqa: BLE001
        label = "强势板块" if sort_desc else "弱势板块"
        errors.append(f"A股{label}抓取失败：{_brief_error(exc)}")
        return []


def _board_from_row(row: dict) -> AShareBoard:
    change = _to_float(row.get("f3"))
    return AShareBoard(
        name=str(row.get("f14") or "未知板块"),
        change_percent=_format_percent(change),
        note=_note(change),
    )


def summarize_index_bias(indices: Iterable[AShareIndex]) -> str:
    values = [item.raw_change_percent for item in indices if item.raw_change_percent is not None]
    if not values:
        return "主要指数数据暂缺，午后方向需要结合实时盘面再确认。"
    avg = sum(values) / len(values)
    positive = sum(1 for value in values if value > 0)
    negative = sum(1 for value in values if value < 0)
    if avg >= 0.5 and positive >= negative:
        return "上午指数整体偏强，午后重点看强势板块能否继续扩散。"
    if avg <= -0.5 and negative >= positive:
        return "上午指数整体承压，午后重点看权重和高位题材能否止跌。"
    return "上午指数分化不大，午后更可能围绕热点题材和成交量变化展开。"
