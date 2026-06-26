from __future__ import annotations

from collections import Counter
from statistics import mean

from src.fetch_market import MarketQuote
from src.fetch_news import NewsItem


def _average_change(quotes: list[MarketQuote]) -> float | None:
    values = [quote.raw_change_percent for quote in quotes if quote.raw_change_percent is not None]
    if not values:
        return None
    return mean(values)


def summarize_sectors(topic_news: dict[str, list[NewsItem]]) -> str:
    counts = Counter({topic: len(items) for topic, items in topic_news.items()})
    active = [topic for topic, count in counts.most_common() if count > 0]
    if not active:
        return "从已抓取新闻看，暂未出现特别集中的板块线索。"
    top = "、".join(active[:5])
    return f"从新闻密度看，{top} 的消息出现较多，可作为盘前重点观察方向。"


def analyze_us_sentiment(us_indices: list[MarketQuote], global_watch: list[MarketQuote], hot_stocks: list[MarketQuote]) -> str:
    index_avg = _average_change(us_indices)
    stock_avg = _average_change(hot_stocks)
    futures = [item for item in global_watch if "期货" in item.name]
    futures_avg = _average_change(futures)

    if index_avg is None:
        return "核心指数数据暂缺，市场情绪需要结合后续行情再判断。"

    parts: list[str] = []
    if index_avg >= 0.8:
        parts.append("三大指数整体偏强，风险偏好有改善迹象")
    elif index_avg <= -0.8:
        parts.append("三大指数整体承压，市场更偏谨慎")
    else:
        parts.append("三大指数整体波动不大，市场情绪偏中性")

    if futures_avg is not None:
        if futures_avg > 0.3:
            parts.append("期货端延续偏强")
        elif futures_avg < -0.3:
            parts.append("期货端有回落压力")
        else:
            parts.append("期货端暂未给出明显方向")

    if stock_avg is not None:
        if stock_avg > index_avg + 0.5:
            parts.append("热门科技股表现强于指数，资金仍偏向成长方向")
        elif stock_avg < index_avg - 0.5:
            parts.append("热门科技股弱于指数，可能存在资金轮动")

    return "；".join(parts) + "。"


def build_us_outlook(us_indices: list[MarketQuote], global_watch: list[MarketQuote]) -> str:
    index_avg = _average_change(us_indices)
    futures_avg = _average_change([item for item in global_watch if "期货" in item.name])
    if index_avg is None:
        return "由于指数数据暂缺，今日走势更适合等待盘前期货和重要新闻进一步确认。"
    if index_avg > 0.5 and (futures_avg is None or futures_avg >= -0.2):
        return "若盘前期货继续保持稳定，市场可能延续偏强或震荡上行；但若涨幅集中在少数权重股，盘中仍可能出现分化。"
    if index_avg < -0.5 and (futures_avg is None or futures_avg <= 0.2):
        return "若盘前期货未能修复，市场可能延续震荡或回调；若利率、美元或避险资产走弱，跌幅也可能收窄。"
    return "当前信号偏混合，指数更可能在消息和权重股表现之间震荡，重点观察盘前期货、科技龙头和大宗商品方向。"


def build_a_share_external(us_indices: list[MarketQuote], global_watch: list[MarketQuote]) -> str:
    lines = []
    us_avg = _average_change(us_indices)
    if us_avg is None:
        lines.append("- 美股核心指数数据暂缺，对 A 股情绪影响需要开盘前再确认。")
    elif us_avg > 0.5:
        lines.append("- 美股整体偏强，可能对 A 股风险偏好形成正面影响。")
    elif us_avg < -0.5:
        lines.append("- 美股整体偏弱，A 股开盘前需注意外部风险偏好降温。")
    else:
        lines.append("- 美股整体变化不大，对 A 股影响偏中性。")

    for item in global_watch:
        lines.append(f"- {item.name}：{item.change_percent}，{item.note}。")
    return "\n".join(lines)


def build_china_sector_analysis(topic_news: dict[str, list[NewsItem]]) -> str:
    lines = []
    for topic, items in topic_news.items():
        if items:
            lines.append(f"- {topic}：消息数量较多，盘前可观察是否形成板块共振。")
        else:
            lines.append(f"- {topic}：暂未抓取到明显新增催化，更多看个股公告和资金承接。")
    return "\n".join(lines)


def build_opening_watch(topic_news: dict[str, list[NewsItem]], global_watch: list[MarketQuote]) -> tuple[str, str, str, str, str]:
    active_topics = [topic for topic, items in topic_news.items() if items]
    positive_assets = [item.name for item in global_watch if item.raw_change_percent is not None and item.raw_change_percent > 0.5]
    weak_assets = [item.name for item in global_watch if item.raw_change_percent is not None and item.raw_change_percent < -0.5]

    gap_up = "、".join(active_topics[:4]) if active_topics else "暂未看到明确一致方向，先观察竞价强度。"
    diverge = "高位热门题材、近期连续上涨方向" if active_topics else "缺少新增消息支撑的轮动题材。"
    if weak_assets:
        risk = f"{'、'.join(weak_assets[:4])} 走弱相关方向，以及开盘快速冲高但量能不足的个股。"
    else:
        risk = "短线涨幅较大、只受情绪推动而缺少新增消息的方向。"

    focus = "、".join((active_topics + positive_assets)[:6]) or "AI、半导体、算力、数据中心、有色金属、电力等主线的盘中承接。"
    warning = "关注外盘波动、汇率变化、大宗商品回落、热门题材高开低走，以及新闻来源延迟带来的信息偏差。"
    return gap_up, diverge, risk, focus, warning
