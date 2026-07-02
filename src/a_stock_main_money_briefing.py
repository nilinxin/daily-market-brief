from __future__ import annotations

import argparse
import json
import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean
from typing import Any
from zoneinfo import ZoneInfo

import requests
from jinja2 import Environment, FileSystemLoader

from src.notifier import notify_report_ready


ROOT = Path(__file__).resolve().parents[1]
EASTMONEY_QUOTE_HOSTS = (
    "https://82.push2.eastmoney.com/api/qt",
    "https://20.push2.eastmoney.com/api/qt",
    "https://33.push2.eastmoney.com/api/qt",
    "https://push2.eastmoney.com/api/qt",
)
EASTMONEY_HISTORY = "https://push2his.eastmoney.com/api/qt"
SOURCE_LINKS = [
    {"name": "东方财富行情中心", "url": "https://quote.eastmoney.com/"},
    {"name": "东方财富数据中心", "url": "https://data.eastmoney.com/"},
    {"name": "上海证券交易所公告", "url": "https://www.sse.com.cn/disclosure/listedinfo/announcement/"},
    {"name": "深圳证券交易所公告", "url": "https://www.szse.cn/disclosure/listed/notice/"},
]
RISK_WORDS = ("立案", "处罚", "退市", "终止上市", "重大诉讼", "冻结", "违规", "预亏", "减持")


@dataclass
class SourceStatus:
    name: str
    success: bool
    detail: str


@dataclass
class Candidate:
    code: str
    name: str
    sector: str
    price: float
    change: float
    amount: float
    turnover: float
    volume_ratio: float
    market_cap: float
    pe: float
    current_main_flow: float
    current_flow_ratio: float
    sector_change: float = 0.0
    sector_flow: float = 0.0
    ret_3d: float = 0.0
    ret_5d: float = 0.0
    ret_10d: float = 0.0
    ret_20d: float = 0.0
    flow_3d: float = 0.0
    flow_5d: float = 0.0
    flow_10d: float = 0.0
    positive_flow_days_5: int = 0
    ma5: float = 0.0
    ma10: float = 0.0
    ma20: float = 0.0
    ma60: float = 0.0
    range_position_60d: float = 0.0
    support: float = 0.0
    resistance: float = 0.0
    upper_shadow: float = 0.0
    risk_notice: str = ""
    sector_score: int = 0
    money_score: int = 0
    technical_score: int = 0
    lag_score: int = 0
    volume_score: int = 0
    risk_deduction: int = 0
    total_score: int = 0
    reasons: str = ""
    technical_position: str = ""
    risk_level: str = ""
    observation: str = ""
    strategy: str = ""
    supplementary_note: str = ""


class MarketClient:
    def __init__(self, timeout: int = 12) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": "Mozilla/5.0 daily-market-brief/1.0", "Referer": "https://quote.eastmoney.com/"}
        )

    def json(self, url: str, params: dict[str, Any], attempts: int = 3) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
                response.raise_for_status()
                return response.json()
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt < attempts - 1:
                    time.sleep(0.8 * (attempt + 1))
        raise RuntimeError(_brief_error(last_error or RuntimeError("unknown error")))


def _brief_error(exc: Exception) -> str:
    text = str(exc).splitlines()[0]
    return f"{exc.__class__.__name__}: {text[:100]}"


def _num(value: Any, default: float | None = None) -> float | None:
    if value in (None, "", "-"):
        return default
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _secid(code: str) -> str:
    return f"{1 if code.startswith('6') else 0}.{code}"


def _safe_mean(values: list[float]) -> float:
    return mean(values) if values else 0.0


def _return(prices: list[float], days: int) -> float:
    if len(prices) <= days or prices[-days - 1] <= 0:
        return 0.0
    return (prices[-1] / prices[-days - 1] - 1) * 100


def _format_money(value: float) -> str:
    sign = "-" if value < 0 else ""
    value = abs(value)
    if value >= 100_000_000:
        return f"{sign}{value / 100_000_000:.2f}亿元"
    return f"{sign}{value / 10_000:.0f}万元"


def quote_json(client: MarketClient, path: str, params: dict[str, Any], attempts: int = 1) -> dict[str, Any]:
    errors: list[str] = []
    for host in EASTMONEY_QUOTE_HOSTS:
        try:
            return client.json(f"{host}/{path}", params, attempts=attempts)
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
    raise RuntimeError("；".join(errors[-2:]) or "所有公开行情节点均不可用")


def fetch_stock_snapshot(client: MarketClient) -> list[dict[str, Any]]:
    params = {
            "pn": 1,
            "pz": 100,
            "po": 1,
            "np": 1,
            "fltt": 2,
            "invt": 2,
            "fid": "f6",
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
            "fields": "f2,f3,f5,f6,f8,f9,f10,f12,f14,f15,f16,f17,f18,f20,f21,f62,f100,f124,f184",
        }
    payload = quote_json(client, "clist/get", params)
    data = payload.get("data", {}) or {}
    rows = list(data.get("diff") or [])
    total = int(data.get("total") or len(rows))
    pages = max(1, math.ceil(total / 100))
    if pages <= 1:
        return rows

    def fetch_page(page: int) -> list[dict[str, Any]]:
        page_params = dict(params)
        page_params["pn"] = page
        try:
            page_payload = quote_json(MarketClient(min(client.timeout, 8)), "clist/get", page_params)
            return page_payload.get("data", {}).get("diff") or []
        except Exception:  # noqa: BLE001
            return []

    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = [executor.submit(fetch_page, page) for page in range(2, pages + 1)]
        for future in as_completed(futures):
            rows.extend(future.result())
    return rows


def fetch_indices(client: MarketClient) -> list[dict[str, Any]]:
    names = {"000001": "上证指数", "399001": "深证成指", "399006": "创业板指"}
    payload = quote_json(
        client,
        "ulist.np/get",
        {
            "fltt": 2,
            "invt": 2,
            "fields": "f2,f3,f6,f12,f14,f124",
            "secids": "1.000001,0.399001,0.399006",
        },
    )
    return [
        {
            "name": names.get(str(row.get("f12")), str(row.get("f14") or "指数")),
            "price": _num(row.get("f2")),
            "change": _num(row.get("f3")),
            "amount": _num(row.get("f6"), 0.0),
        }
        for row in payload.get("data", {}).get("diff") or []
    ]


def fetch_limit_pool(client: MarketClient, now: datetime) -> tuple[int, int]:
    payload = client.json(
        "https://push2ex.eastmoney.com/getTopicZTPool",
        {
            "ut": "7eea3edcaed734bea9cbfc24409ed989",
            "d": now.strftime("%Y%m%d"),
            "Pageindex": 0,
            "pagesize": 300,
            "sort": "fbt:asc",
        },
        attempts=1,
    )
    rows = (payload.get("data") or {}).get("pool") or []
    streaks = [int(_num(row.get("lbc"), 1) or 1) for row in rows]
    return len(rows), max(streaks, default=0)


def fetch_boards(client: MarketClient) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    boards: list[dict[str, Any]] = []
    for board_type, fs in (("行业", "m:90+t:2"), ("概念", "m:90+t:3")):
        for descending in (True, False):
            try:
                payload = quote_json(
                    client,
                    "clist/get",
                    {
                        "pn": 1,
                        "pz": 120,
                        "po": 1 if descending else 0,
                        "np": 1,
                        "fltt": 2,
                        "invt": 2,
                        "fid": "f62",
                        "fs": fs,
                        "fields": "f2,f3,f6,f12,f14,f62,f184",
                    },
                    attempts=2,
                )
            except Exception:  # noqa: BLE001
                continue
            for row in payload.get("data", {}).get("diff") or []:
                raw_flow = _num(row.get("f62"))
                raw_change = _num(row.get("f3"))
                if raw_flow is None or raw_change is None:
                    continue
                boards.append(
                    {
                        "type": board_type,
                        "code": str(row.get("f12") or ""),
                        "name": str(row.get("f14") or "未知板块"),
                        "change": raw_change,
                        "flow": raw_flow,
                        "flow_ratio": _num(row.get("f184"), 0.0) or 0.0,
                    }
                )
    boards = list({(item["type"], item["code"]): item for item in boards}.values())
    if not boards or all(item["flow"] == 0 for item in boards):
        return [], []
    inflow = [item for item in sorted(boards, key=lambda item: (item["flow"], item["change"]), reverse=True) if item["flow"] > 0][:10]
    outflow = [item for item in sorted(boards, key=lambda item: (item["flow"], item["change"])) if item["flow"] < 0][:10]
    return inflow, outflow


def fetch_concept_members(client: MarketClient, boards: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    concepts = [item for item in boards if item["type"] == "概念" and item["code"]][:6]
    memberships: dict[str, dict[str, Any]] = {}
    for board in concepts:
        payload = quote_json(
            client,
            "clist/get",
            {
                "pn": 1,
                "pz": 500,
                "po": 1,
                "np": 1,
                "fltt": 2,
                "invt": 2,
                "fid": "f6",
                "fs": f"b:{board['code']}",
                "fields": "f12",
            },
        )
        for row in payload.get("data", {}).get("diff") or []:
            code = str(row.get("f12") or "")
            if code and code not in memberships:
                memberships[code] = board
    return memberships


def fetch_kline(client: MarketClient, code: str) -> list[dict[str, float | str]]:
    payload = client.json(
        f"{EASTMONEY_HISTORY}/stock/kline/get",
        {
            "secid": _secid(code),
            "klt": 101,
            "fqt": 1,
            "lmt": 80,
            "end": 20500101,
            "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        },
        attempts=2,
    )
    result = []
    for line in payload.get("data", {}).get("klines") or []:
        parts = line.split(",")
        if len(parts) >= 11:
            result.append(
                {
                    "date": parts[0],
                    "open": float(parts[1]),
                    "close": float(parts[2]),
                    "high": float(parts[3]),
                    "low": float(parts[4]),
                    "volume": float(parts[5]),
                    "amount": float(parts[6]),
                    "amplitude": float(parts[7]),
                    "change": float(parts[8]),
                    "turnover": float(parts[10]),
                }
            )
    return result


def fetch_flow(client: MarketClient, code: str) -> list[dict[str, float | str]]:
    payload = client.json(
        f"{EASTMONEY_HISTORY}/stock/fflow/daykline/get",
        {
            "lmt": 12,
            "klt": 101,
            "secid": _secid(code),
            "fields1": "f1,f2,f3,f7",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63",
        },
        attempts=2,
    )
    result = []
    for line in payload.get("data", {}).get("klines") or []:
        parts = line.split(",")
        if len(parts) >= 13:
            result.append({"date": parts[0], "main": float(parts[1]), "close": float(parts[11]), "change": float(parts[12])})
    return result


def fetch_risk_notice(client: MarketClient, code: str, now: datetime) -> str:
    payload = client.json(
        "https://np-anotice-stock.eastmoney.com/api/security/ann",
        {
            "sr": -1,
            "page_size": 12,
            "page_index": 1,
            "ann_type": "A",
            "client_source": "web",
            "stock_list": code,
        },
    )
    cutoff = now.date() - timedelta(days=30)
    for item in payload.get("data", {}).get("list") or []:
        title = str(item.get("title_ch") or item.get("title") or "")
        date_text = str(item.get("notice_date") or "")[:10]
        try:
            notice_date = datetime.strptime(date_text, "%Y-%m-%d").date()
        except ValueError:
            continue
        if notice_date >= cutoff and any(word in title for word in RISK_WORDS):
            return f"{date_text} {title}"
    return ""


def fetch_previous_activity(client: MarketClient, now: datetime) -> tuple[dict[str, float], dict[str, tuple[float, float]], str]:
    start = (now.date() - timedelta(days=7)).strftime("%Y-%m-%d")
    end = (now.date() - timedelta(days=1)).strftime("%Y-%m-%d")
    common = {
        "columns": "ALL",
        "pageNumber": 1,
        "pageSize": 500,
        "sortColumns": "TRADE_DATE",
        "sortTypes": -1,
        "source": "WEB",
        "client": "WEB",
        "filter": f"(TRADE_DATE>='{start}')(TRADE_DATE<='{end}')",
    }
    billboard_payload = client.json(
        "https://datacenter-web.eastmoney.com/api/data/v1/get",
        {**common, "reportName": "RPT_DAILYBILLBOARD_DETAILSNEW"},
    )
    block_payload = client.json(
        "https://datacenter-web.eastmoney.com/api/data/v1/get",
        {**common, "reportName": "RPT_DATA_BLOCKTRADE"},
    )
    billboard: dict[str, float] = {}
    blocks: dict[str, tuple[float, float]] = {}
    dates: list[str] = []
    for row in billboard_payload.get("result", {}).get("data") or []:
        code = str(row.get("SECURITY_CODE") or "")
        value = _num(row.get("BILLBOARD_NET_AMT"), 0.0) or 0.0
        billboard[code] = billboard.get(code, 0.0) + value
        dates.append(str(row.get("TRADE_DATE") or "")[:10])
    for row in block_payload.get("result", {}).get("data") or []:
        code = str(row.get("SECURITY_CODE") or "")
        amount = _num(row.get("DEAL_AMT"), 0.0) or 0.0
        premium = _num(row.get("PREMIUM_RATIO"), 0.0) or 0.0
        prior_amount, prior_weighted = blocks.get(code, (0.0, 0.0))
        blocks[code] = (prior_amount + amount, prior_weighted + amount * premium)
        dates.append(str(row.get("TRADE_DATE") or "")[:10])
    return billboard, blocks, max((item for item in dates if item), default="最近交易日")


def build_market_summary(snapshot: list[dict[str, Any]], indices: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [row for row in snapshot if _num(row.get("f2")) is not None and _num(row.get("f3")) is not None]
    rises = sum(1 for row in valid if (_num(row.get("f3"), 0.0) or 0.0) > 0)
    falls = sum(1 for row in valid if (_num(row.get("f3"), 0.0) or 0.0) < 0)
    flats = len(valid) - rises - falls
    limit_up = sum(1 for row in valid if (_num(row.get("f3"), 0.0) or 0.0) >= 9.5)
    limit_down = sum(1 for row in valid if (_num(row.get("f3"), 0.0) or 0.0) <= -9.5)
    amount = sum(_num(row.get("f6"), 0.0) or 0.0 for row in valid)
    main_flow = sum(_num(row.get("f62"), 0.0) or 0.0 for row in valid)
    ratios = [_num(row.get("f10")) for row in valid]
    ratio_values = [value for value in ratios if value is not None and 0 < value < 20]
    mood = "偏强" if rises > falls * 1.25 else "偏弱" if falls > rises * 1.25 else "分化"
    posture = "可适度寻找回踩低吸观察机会" if mood == "偏强" else "以低吸埋伏和等待确认为主" if mood == "分化" else "继续观望，降低进攻预期"
    return {
        "available": len(valid) >= 1000,
        "indices": indices,
        "rises": rises,
        "falls": falls,
        "flats": flats,
        "limit_up": limit_up,
        "limit_down": limit_down,
        "amount": amount,
        "main_flow": main_flow,
        "average_volume_ratio": _safe_mean(ratio_values),
        "mood": mood,
        "posture": posture,
    }


def is_risky_name(name: str) -> bool:
    upper = name.upper().replace(" ", "")
    return "ST" in upper or "退" in name or "风险" in name


def make_initial_candidates(
    snapshot: list[dict[str, Any]],
    config: dict[str, Any],
    slot: str,
    board_map: dict[str, dict[str, Any]],
    concept_map: dict[str, dict[str, Any]] | None = None,
) -> list[Candidate]:
    amount_floor = float(config["minimum_amount"][slot])
    turn_min, turn_max = config["healthy_turnover"][slot]
    candidates: list[tuple[float, Candidate]] = []
    for row in snapshot:
        code = str(row.get("f12") or "")
        name = str(row.get("f14") or "")
        if len(code) != 6 or not code.startswith(("0", "3", "6")) or is_risky_name(name):
            continue
        price = _num(row.get("f2"))
        change = _num(row.get("f3"))
        amount = _num(row.get("f6"), 0.0) or 0.0
        turnover = _num(row.get("f8"), 0.0) or 0.0
        volume_ratio = _num(row.get("f10"), 0.0) or 0.0
        market_cap = _num(row.get("f20"), 0.0) or 0.0
        pe = _num(row.get("f9"), 0.0) or 0.0
        flow = _num(row.get("f62"), 0.0) or 0.0
        if price is None or change is None or price <= 0 or amount < amount_floor or market_cap < config["minimum_market_cap"]:
            continue
        if not config["minimum_daily_rise"] <= change <= config["maximum_daily_rise"]:
            continue
        if turnover < turn_min * 0.5 or turnover > turn_max * 1.5:
            continue
        if volume_ratio <= 0 or volume_ratio > float(config["maximum_volume_ratio"]):
            continue
        industry = str(row.get("f100") or "未分类")
        concept = (concept_map or {}).get(code)
        board = concept or board_map.get(industry, {})
        sector = f"{concept['name']}（概念）" if concept else industry
        flow_ratio = flow / amount * 100 if amount else 0.0
        quick = max(-5.0, min(10.0, flow_ratio)) * 3 + max(-3.0, min(5.0, float(board.get("change", 0.0)))) * 2
        quick += min(6.0, amount / amount_floor) + (3 if turn_min <= turnover <= turn_max else 0)
        candidates.append(
            (
                quick,
                Candidate(
                    code=code,
                    name=name,
                    sector=sector,
                    price=price,
                    change=change,
                    amount=amount,
                    turnover=turnover,
                    volume_ratio=volume_ratio,
                    market_cap=market_cap,
                    pe=pe,
                    current_main_flow=flow,
                    current_flow_ratio=flow_ratio,
                    sector_change=float(board.get("change", 0.0)),
                    sector_flow=float(board.get("flow", 0.0)),
                ),
            )
        )
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in candidates[: int(config["candidate_pool_size"])]]


def enrich_candidate(candidate: Candidate, client: MarketClient, config: dict[str, Any]) -> Candidate | None:
    klines = fetch_kline(client, candidate.code)
    flows = fetch_flow(client, candidate.code)
    if len(klines) < 60 or len(flows) < 10:
        return None
    closes = [float(item["close"]) for item in klines]
    highs = [float(item["high"]) for item in klines]
    lows = [float(item["low"]) for item in klines]
    current_close = candidate.price
    if klines[-1]["date"] != datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d"):
        closes.append(current_close)
        highs.append(current_close)
        lows.append(current_close)
    candidate.ret_3d = _return(closes, 3)
    candidate.ret_5d = _return(closes, 5)
    candidate.ret_10d = _return(closes, 10)
    candidate.ret_20d = _return(closes, 20)
    if candidate.ret_5d > config["maximum_five_day_rise"] or candidate.ret_10d > config["maximum_ten_day_rise"]:
        return None
    mains = [float(item["main"]) for item in flows]
    candidate.flow_3d = sum(mains[-3:])
    candidate.flow_5d = sum(mains[-5:])
    candidate.flow_10d = sum(mains[-10:])
    candidate.positive_flow_days_5 = sum(1 for value in mains[-5:] if value > 0)
    candidate.ma5 = _safe_mean(closes[-5:])
    candidate.ma10 = _safe_mean(closes[-10:])
    candidate.ma20 = _safe_mean(closes[-20:])
    candidate.ma60 = _safe_mean(closes[-60:])
    low60, high60 = min(lows[-60:]), max(highs[-60:])
    candidate.range_position_60d = (current_close - low60) / (high60 - low60) * 100 if high60 > low60 else 50.0
    candidate.support = max(min(candidate.ma10, candidate.ma20), min(lows[-10:]))
    candidate.resistance = max(highs[-20:])
    last = klines[-1]
    last_high = float(last["high"])
    last_open = float(last["open"])
    last_close = float(last["close"])
    candidate.upper_shadow = (last_high - max(last_open, last_close)) / last_close * 100 if last_close else 0.0
    return score_candidate(candidate, config)


def score_candidate(candidate: Candidate, config: dict[str, Any]) -> Candidate:
    candidate.sector_score = min(20, max(0, round(8 + candidate.sector_change * 2 + (4 if candidate.sector_flow > 0 else 0))))
    money = 0
    money += 8 if candidate.current_flow_ratio >= 3 else 6 if candidate.current_flow_ratio >= 1 else 3 if candidate.current_main_flow > 0 else 0
    money += 6 if candidate.flow_3d > 0 else 0
    money += 6 if candidate.flow_5d > 0 else 0
    money += min(5, candidate.positive_flow_days_5)
    candidate.money_score = min(25, money)
    tech = 0
    distance20 = abs(candidate.price / candidate.ma20 - 1) * 100 if candidate.ma20 else 99
    tech += 8 if distance20 <= 3 else 5 if distance20 <= 6 else 2
    tech += 5 if candidate.ma5 >= candidate.ma10 >= candidate.ma20 else 3 if candidate.price >= candidate.ma20 else 0
    tech += 4 if 20 <= candidate.range_position_60d <= 70 else 2 if candidate.range_position_60d < 85 else 0
    tech += 3 if candidate.price >= candidate.ma10 else 1
    candidate.technical_score = min(20, tech)
    lag = 0
    lag += 8 if candidate.sector_change - candidate.change >= 1 else 5 if candidate.change <= candidate.sector_change else 2
    lag += 7 if candidate.range_position_60d <= 55 else 4 if candidate.range_position_60d <= 72 else 0
    candidate.lag_score = min(15, lag)
    vr_min, vr_max = config["healthy_volume_ratio"]
    volume = 5 if vr_min <= candidate.volume_ratio <= vr_max else 2 if 0.5 <= candidate.volume_ratio <= 3 else 0
    volume += 3 if 0.4 <= candidate.turnover <= 9 else 1
    volume += 2 if candidate.amount >= config["minimum_amount"]["1330"] else 1
    candidate.volume_score = min(10, volume)
    risk = 0
    if candidate.range_position_60d > 85:
        risk += 4
    if candidate.ret_10d > 25 or candidate.ret_20d > 40:
        risk += 3
    if candidate.volume_ratio > 3 or candidate.upper_shadow > 3:
        risk += 2
    if candidate.flow_5d < 0:
        risk += 3
    if candidate.pe <= 0:
        risk += 2
    if candidate.risk_notice:
        risk += 4
    candidate.risk_deduction = min(10, risk)
    candidate.total_score = max(
        0,
        candidate.sector_score
        + candidate.money_score
        + candidate.technical_score
        + candidate.lag_score
        + candidate.volume_score
        + 10
        - candidate.risk_deduction,
    )
    candidate.technical_position = _technical_position(candidate)
    candidate.risk_level = "低" if candidate.risk_deduction <= 2 else "中" if candidate.risk_deduction <= 5 else "高"
    candidate.observation = f"支撑区 {candidate.support * 0.99:.2f}-{candidate.support * 1.02:.2f}；压力位 {candidate.resistance:.2f}"
    candidate.strategy = "等待回踩支撑区缩量企稳" if candidate.price > candidate.support * 1.02 else "支撑附近观察资金是否继续流入"
    candidate.reasons = _candidate_reason(candidate)
    return candidate


def _technical_position(candidate: Candidate) -> str:
    if candidate.range_position_60d > 85:
        return "60日高位，谨慎追高"
    if abs(candidate.price / candidate.ma20 - 1) <= 0.03:
        return "靠近20日均线支撑"
    if candidate.ma5 >= candidate.ma10 >= candidate.ma20:
        return "均线偏多但尚未加速"
    return "中低位震荡观察"


def _candidate_reason(candidate: Candidate) -> str:
    flow_text = "近5日资金净流入" if candidate.flow_5d > 0 else "当日资金转为流入"
    sector_text = "强势板块内相对滞涨" if candidate.sector_change > candidate.change else "板块资金有承接"
    high_text = "尚未处于60日高位" if candidate.range_position_60d < 75 else "位置偏高需等待回踩"
    return f"{flow_text}，{sector_text}，{high_text}；{candidate.strategy}，不适合明显高开后追涨。"


def _board_stage(board: dict[str, Any]) -> str:
    change, flow = board["change"], board["flow"]
    if flow > 0 and 0 < change <= 1.5:
        return "低位启动"
    if flow > 0 and change > 2.5:
        return "加速上涨"
    if flow < 0 and change > 0:
        return "高位分歧"
    if flow > 0 and change < 0:
        return "回调企稳"
    if flow < 0 and change < -1:
        return "退潮阶段"
    return "补涨扩散"


def _freshness(snapshot: list[dict[str, Any]], now: datetime) -> tuple[bool, str]:
    timestamps = [int(value) for row in snapshot if (value := _num(row.get("f124"))) is not None]
    if not timestamps:
        return False, "实时行情缺少更新时间"
    latest = datetime.fromtimestamp(max(timestamps), ZoneInfo("Asia/Shanghai"))
    return latest.date() == now.date(), latest.strftime("%Y-%m-%d %H:%M:%S")


def _confidence(statuses: list[SourceStatus]) -> str:
    core_names = {"A股实时行情", "三大指数", "行业与概念板块资金", "候选股历史行情与资金", "近期风险公告筛查"}
    core = [item for item in statuses if item.name in core_names]
    success_count = sum(1 for item in core if item.success)
    if len(core) == len(core_names) and success_count == len(core_names):
        return "高"
    return "中" if success_count >= 3 else "低"


def build_report(slot: str, allow_stale: bool = False) -> tuple[dict[str, Any], bool]:
    config = json.loads((ROOT / "config" / "main_money_watchlist.json").read_text(encoding="utf-8"))
    now = datetime.now(ZoneInfo(config["timezone"]))
    statuses: list[SourceStatus] = []
    errors: list[str] = []
    client = MarketClient(int(config["request_timeout_seconds"]))
    snapshot: list[dict[str, Any]] = []
    try:
        snapshot = fetch_stock_snapshot(client)
        valid_quotes = sum(1 for row in snapshot if _num(row.get("f2")) is not None)
        statuses.append(
            SourceStatus(
                "A股实时行情",
                valid_quotes >= 4000,
                f"取得 {len(snapshot)} 只股票，其中 {valid_quotes} 只有有效盘中价格",
            )
        )
    except Exception as exc:  # noqa: BLE001
        errors.append(f"A股实时行情：{exc}")
        statuses.append(SourceStatus("A股实时行情", False, str(exc)))
    fresh, quote_time = _freshness(snapshot, now) if snapshot else (False, "暂缺")
    if snapshot and not fresh and not allow_stale:
        return {"skipped": True, "reason": f"当天无有效交易行情，最近更新时间 {quote_time}"}, True
    try:
        indices = fetch_indices(client)
        statuses.append(SourceStatus("三大指数", len(indices) == 3, f"取得 {len(indices)} 项"))
    except Exception as exc:  # noqa: BLE001
        indices = []
        errors.append(f"三大指数：{exc}")
        statuses.append(SourceStatus("三大指数", False, str(exc)))
    try:
        pool_count, max_streak = fetch_limit_pool(client, now)
        statuses.append(SourceStatus("涨停池与连板高度", pool_count > 0, f"涨停池 {pool_count} 只，最高 {max_streak} 连板"))
    except Exception as exc:  # noqa: BLE001
        pool_count, max_streak = 0, 0
        statuses.append(SourceStatus("涨停池与连板高度", False, str(exc)))
    try:
        inflow_boards, outflow_boards = fetch_boards(client)
        statuses.append(SourceStatus("行业与概念板块资金", bool(inflow_boards), f"流入/流出各 {len(inflow_boards)}/{len(outflow_boards)} 项"))
    except Exception as exc:  # noqa: BLE001
        inflow_boards, outflow_boards = [], []
        errors.append(f"板块资金：{exc}")
        statuses.append(SourceStatus("行业与概念板块资金", False, str(exc)))
    try:
        billboard, block_trades, activity_date = fetch_previous_activity(client, now)
        statuses.append(
            SourceStatus(
                "龙虎榜与大宗交易",
                True,
                f"使用 {activity_date} 等最近已披露数据，龙虎榜 {len(billboard)} 只、大宗交易 {len(block_trades)} 只",
            )
        )
    except Exception as exc:  # noqa: BLE001
        billboard, block_trades, activity_date = {}, {}, "暂缺"
        errors.append(f"龙虎榜与大宗交易：{exc}")
        statuses.append(SourceStatus("龙虎榜与大宗交易", False, str(exc)))
    board_map = {item["name"]: item for item in inflow_boards + outflow_boards}
    try:
        concept_map = fetch_concept_members(client, inflow_boards)
        statuses.append(SourceStatus("强势概念成分", bool(concept_map), f"匹配 {len(concept_map)} 只成分股"))
    except Exception as exc:  # noqa: BLE001
        concept_map = {}
        statuses.append(SourceStatus("强势概念成分", False, str(exc)))
    initial = make_initial_candidates(snapshot, config, slot, board_map, concept_map) if snapshot and inflow_boards else []
    enriched: list[Candidate] = []
    history_failures = 0
    if initial:
        with ThreadPoolExecutor(max_workers=int(config["history_workers"])) as executor:
            futures = {
                executor.submit(
                    enrich_candidate,
                    item,
                    MarketClient(min(8, int(config["request_timeout_seconds"]))),
                    config,
                ): item
                for item in initial
            }
            for future in as_completed(futures):
                try:
                    result = future.result()
                    if result is not None:
                        enriched.append(result)
                except Exception:  # noqa: BLE001
                    history_failures += 1
    statuses.append(
        SourceStatus(
            "候选股历史行情与资金",
            bool(enriched),
            f"完整 {len(enriched)} 只，失败或被排除 {len(initial) - len(enriched)} 只",
        )
    )
    for item in enriched:
        notes: list[str] = []
        if item.code in billboard:
            net = billboard[item.code]
            notes.append(f"{activity_date}附近龙虎榜净额 {_format_money(net)}")
        if item.code in block_trades:
            amount, weighted = block_trades[item.code]
            premium = weighted / amount if amount else 0.0
            notes.append(f"大宗交易 {_format_money(amount)}，平均溢价率 {premium:+.2f}%")
        item.supplementary_note = "；".join(notes) or "最近披露数据中未发现匹配记录"
    notice_successes = 0
    notice_failures = 0
    notice_targets = sorted(enriched, key=lambda item: item.total_score, reverse=True)[:20]
    if notice_targets:
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = {
                executor.submit(fetch_risk_notice, MarketClient(int(config["request_timeout_seconds"])), item.code, now): item
                for item in notice_targets
            }
            for future in as_completed(futures):
                item = futures[future]
                try:
                    item.risk_notice = future.result()
                    score_candidate(item, config)
                    notice_successes += 1
                except Exception:  # noqa: BLE001
                    notice_failures += 1
    statuses.append(
        SourceStatus(
            "近期风险公告筛查",
            notice_successes > 0,
            f"完成 {notice_successes} 只，失败 {notice_failures} 只" if notice_targets else "无候选股需要筛查",
        )
    )
    selected = []
    if inflow_boards:
        selected = sorted(
            [item for item in enriched if item.total_score >= int(config["minimum_score"])],
            key=lambda item: (item.total_score, item.money_score, item.amount),
            reverse=True,
        )[: int(config["maximum_results"])]
    statuses.extend(
        [
            SourceStatus("北向资金实时净流入", False, "当前披露口径下盘中数据暂缺"),
            SourceStatus("ETF份额变化", False, "盘中稳定数据暂缺，未计入评分"),
        ]
    )
    for board in inflow_boards:
        board["stage"] = _board_stage(board)
        board["note"] = "资金与涨幅同步" if board["flow"] > 0 and board["change"] > 0 else "资金承接但走势仍需确认"
    for board in outflow_boards:
        board["risk_reason"] = "资金净流出且板块走弱" if board["change"] < 0 else "上涨但资金流出，注意分歧"
    context = {
        "title": "A股主力低吸与待涨机会简报",
        "report_date": now.strftime("%Y-%m-%d"),
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "slot": slot,
        "slot_label": "10:30盘中版" if slot == "1030" else "13:30盘中版",
        "quote_time": quote_time,
        "market": {**build_market_summary(snapshot, indices), "pool_count": pool_count, "max_streak": max_streak},
        "inflow_boards": inflow_boards[:8],
        "outflow_boards": outflow_boards[:8],
        "stocks": selected,
        "top3": selected[:3],
        "statuses": statuses,
        "errors": errors,
        "confidence": _confidence(statuses),
        "sources": SOURCE_LINKS,
        "history_failures": history_failures,
        "candidate_count": len(initial),
        "qualified_count": len(selected),
        "skipped": False,
    }
    return context, False


def write_outputs(context: dict[str, Any]) -> tuple[Path, Path]:
    reports = ROOT / "reports"
    data_dir = reports / "data"
    reports.mkdir(exist_ok=True)
    data_dir.mkdir(exist_ok=True)
    stem = f"a_stock_main_money_briefing_{context['report_date']}_{context['slot']}"
    env = Environment(loader=FileSystemLoader(ROOT / "templates"), trim_blocks=True, lstrip_blocks=True)
    content = env.get_template("a_stock_main_money_briefing.md.j2").render(**context)
    report_path = reports / f"{stem}.md"
    report_path.write_text(content, encoding="utf-8")
    audit = {
        "report_date": context["report_date"],
        "generated_at": context["generated_at"],
        "slot": context["slot"],
        "confidence": context["confidence"],
        "source_status": [asdict(item) for item in context["statuses"]],
        "candidates": [asdict(item) for item in context["stocks"]],
        "sources": context["sources"],
    }
    audit_path = data_dir / f"main_money_watchlist_{context['report_date']}_{context['slot']}.json"
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    return report_path, audit_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the independent A-share main-money watchlist brief.")
    parser.add_argument("--slot", choices=["1030", "1330"], required=True)
    parser.add_argument("--allow-stale", action="store_true", help="Generate with latest data for manual verification only.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    context, skipped = build_report(args.slot, allow_stale=args.allow_stale)
    if skipped:
        logging.info("No report sent: %s", context["reason"])
        return
    report_path, audit_path = write_outputs(context)
    subject = f"A股主力低吸与待涨机会简报（{context['slot_label']}）- {context['report_date']}"
    notify_report_ready(str(report_path), subject=subject)
    logging.info("Report written to %s; audit data written to %s", report_path, audit_path)


if __name__ == "__main__":
    main()
