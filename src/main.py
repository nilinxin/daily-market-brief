from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.analyze import (
    analyze_us_sentiment,
    build_a_share_external,
    build_china_sector_analysis,
    build_opening_watch,
    build_us_outlook,
    summarize_sectors,
)
from src.fetch_market import configured_market_sources, fetch_quotes
from src.fetch_news import dedupe_news, fetch_rss_news, fetch_web_headlines, filter_china_news, filter_topic_news
from src.notifier import notify_report_ready
from src.render_markdown import render_report


ROOT = Path(__file__).resolve().parents[1]


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def setup_logging() -> None:
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(log_dir / "market-brief.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def build_context(mode: str) -> dict:
    errors: list[str] = []
    watchlists = load_json(ROOT / "config" / "watchlists.json")
    sources_config = load_json(ROOT / "config" / "sources.json")

    timezone_name = watchlists.get("timezone", "Asia/Shanghai")
    now = datetime.now(ZoneInfo(timezone_name))
    report_date = now.strftime("%Y-%m-%d")

    logging.info("Fetching market quotes")
    us_indices = fetch_quotes(watchlists["us_indices"], errors)
    global_watch = fetch_quotes(watchlists["global_watch"], errors)
    hot_stocks = fetch_quotes(watchlists["hot_us_stocks"], errors)

    limits = sources_config.get("limits", {})
    logging.info("Fetching RSS news")
    rss_news = fetch_rss_news(
        sources_config.get("rss_feeds", []),
        limit_per_feed=int(limits.get("news_per_feed", 20)),
        max_age_hours=int(limits.get("max_news_age_hours", 36)),
        errors=errors,
    )
    logging.info("Fetching web headlines")
    web_news = fetch_web_headlines(sources_config.get("web_sources", []), errors)
    all_news = dedupe_news(rss_news + web_news)

    topic_news = filter_topic_news(
        all_news,
        watchlists.get("focus_topics", {}),
        limit=int(limits.get("topic_items", 5)),
    )
    china_news = filter_china_news(all_news)

    likely_gap_up, likely_diverge, risk_chasing, focus_today, risk_warning = build_opening_watch(topic_news, global_watch)

    source_links = [
        {"name": "Yahoo Finance", "url": "https://finance.yahoo.com/"},
    ]
    for name in configured_market_sources():
        if name == "Finnhub free API":
            source_links.append({"name": name, "url": "https://finnhub.io/docs/api"})
        elif name == "Alpha Vantage free API":
            source_links.append({"name": name, "url": "https://www.alphavantage.co/documentation/"})
        elif name == "Twelve Data free API":
            source_links.append({"name": name, "url": "https://twelvedata.com/docs"})
        elif name == "Tiingo free API":
            source_links.append({"name": name, "url": "https://www.tiingo.com/documentation/end-of-day"})
    source_links.extend({"name": feed["name"], "url": feed["url"]} for feed in sources_config.get("rss_feeds", []))
    source_links.extend({"name": item["name"], "url": item["url"]} for item in sources_config.get("web_sources", []))

    return {
        "mode": mode,
        "report_title": _report_title(mode),
        "report_date": report_date,
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "us_indices": us_indices,
        "global_watch": global_watch,
        "hot_stocks": hot_stocks,
        "topic_news": topic_news,
        "china_news": china_news,
        "sector_summary": summarize_sectors(topic_news),
        "us_sentiment": analyze_us_sentiment(us_indices, global_watch, hot_stocks),
        "us_outlook": build_us_outlook(us_indices, global_watch),
        "a_share_external": build_a_share_external(us_indices, global_watch),
        "china_sector_analysis": build_china_sector_analysis(topic_news),
        "likely_gap_up": likely_gap_up,
        "likely_diverge": likely_diverge,
        "risk_chasing": risk_chasing,
        "focus_today": focus_today,
        "risk_warning": risk_warning,
        "sources": source_links,
        "errors": _summarize_errors(errors),
    }


def write_report(context: dict) -> Path:
    reports_dir = ROOT / "reports"
    reports_dir.mkdir(exist_ok=True)
    report_date = context["report_date"]
    if context["mode"] == "us":
        filename = f"{report_date}-us-market-brief.md"
    elif context["mode"] == "cn":
        filename = f"{report_date}-a-share-preopen-brief.md"
    else:
        filename = f"{report_date}-market-brief.md"
    content = render_report(ROOT / "templates", context)
    report_path = reports_dir / filename
    report_path.write_text(content, encoding="utf-8")
    return report_path


def _email_subject(mode: str, report_date: str) -> str:
    if mode == "us":
        return f"美股每日简报 - {report_date}"
    if mode == "cn":
        return f"A股盘前简报 - {report_date}"
    return f"每日市场简报 - {report_date}"


def _report_title(mode: str) -> str:
    if mode == "us":
        return "美股每日简报"
    if mode == "cn":
        return "A股盘前简报"
    return "每日市场简报"


def _summarize_errors(errors: list[str]) -> list[str]:
    if len(errors) <= 12:
        return errors

    market_errors = [error for error in errors if "行情抓取失败" in error]
    news_errors = [error for error in errors if "新闻抓取失败" in error or "网页新闻抓取失败" in error]
    other_errors = [error for error in errors if error not in market_errors and error not in news_errors]
    summary: list[str] = []
    if market_errors:
        common_reason = _common_reason(market_errors)
        summary.append(f"行情数据：{len(market_errors)} 项未取得（{common_reason}）。")
    if news_errors:
        common_reason = _common_reason(news_errors)
        summary.append(f"新闻数据：{len(news_errors)} 个来源未取得（{common_reason}）。")
    summary.extend(other_errors[:5])
    return summary


def _common_reason(errors: list[str]) -> str:
    reasons = [error.rsplit("：", 1)[-1] for error in errors if "：" in error]
    if not reasons:
        return "原因见日志"
    return max(set(reasons), key=reasons.count)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate daily market brief.")
    parser.add_argument("--mode", choices=["full", "us", "cn"], default="full")
    args = parser.parse_args()

    setup_logging()
    try:
        context = build_context(args.mode)
        report_path = write_report(context)
        notify_report_ready(str(report_path), subject=_email_subject(context["mode"], context["report_date"]))
        logging.info("Report written to %s", report_path)
    except Exception:
        logging.exception("Market brief generation failed")
        raise


if __name__ == "__main__":
    main()
