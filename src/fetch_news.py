from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Iterable
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup


@dataclass
class NewsItem:
    title: str
    link: str
    source: str
    region: str
    published: datetime | None


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


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:  # noqa: BLE001
        return None


def _child_text(element: ElementTree.Element, names: tuple[str, ...]) -> str:
    for child in element:
        tag = child.tag.split("}")[-1].lower()
        if tag in names and child.text:
            return child.text.strip()
        if tag == "link" and "href" in child.attrib and "link" in names:
            return child.attrib["href"].strip()
    return ""


def _iter_feed_entries(xml_text: str) -> list[dict[str, str]]:
    root = ElementTree.fromstring(xml_text)
    entries: list[dict[str, str]] = []
    for element in root.iter():
        tag = element.tag.split("}")[-1].lower()
        if tag not in {"item", "entry"}:
            continue
        entries.append(
            {
                "title": _child_text(element, ("title",)),
                "link": _child_text(element, ("link", "guid")),
                "published": _child_text(element, ("pubdate", "published", "updated")),
            }
        )
    return entries
    return None


def fetch_rss_news(feeds: Iterable[dict], limit_per_feed: int, max_age_hours: int, errors: list[str]) -> list[NewsItem]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    news: list[NewsItem] = []
    seen: set[str] = set()
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 market-brief-bot/1.0"})
    for feed in feeds:
        try:
            response = session.get(feed["url"], timeout=20)
            response.raise_for_status()
            entries = _iter_feed_entries(response.text)
            for entry in entries[:limit_per_feed]:
                title = " ".join(entry.get("title", "").split())
                link = entry.get("link", feed["url"])
                published = _parse_date(entry.get("published"))
                if not title or link in seen:
                    continue
                if published and published < cutoff:
                    continue
                seen.add(link)
                news.append(
                    NewsItem(
                        title=title,
                        link=link,
                        source=feed["name"],
                        region=feed.get("region", "global"),
                        published=published,
                    )
                )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{feed['name']} 新闻抓取失败：{_brief_error(exc)}")
    return news


def fetch_web_headlines(sources: Iterable[dict], errors: list[str], limit_per_source: int = 12) -> list[NewsItem]:
    headers = {
        "User-Agent": "Mozilla/5.0 market-brief-bot/1.0",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
    }
    items: list[NewsItem] = []
    seen: set[str] = set()
    for source in sources:
        try:
            response = requests.get(source["url"], headers=headers, timeout=15)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            count = 0
            for anchor in soup.find_all("a", href=True):
                title = " ".join(anchor.get_text(" ", strip=True).split())
                if len(title) < 8:
                    continue
                link = anchor["href"]
                if link.startswith("/"):
                    link = source["url"].split("/", 3)[:3]
                    link = "/".join(link) + anchor["href"]
                if not link.startswith("http") or link in seen:
                    continue
                seen.add(link)
                items.append(
                    NewsItem(
                        title=title,
                        link=link,
                        source=source["name"],
                        region=source.get("region", "cn"),
                        published=None,
                    )
                )
                count += 1
                if count >= limit_per_source:
                    break
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{source['name']} 网页新闻抓取失败：{_brief_error(exc)}")
    return items


def filter_topic_news(news: Iterable[NewsItem], topics: dict[str, list[str]], limit: int) -> dict[str, list[NewsItem]]:
    result: dict[str, list[NewsItem]] = {}
    for topic, keywords in topics.items():
        matched: list[NewsItem] = []
        lowered_keywords = [keyword.lower() for keyword in keywords]
        for item in news:
            title_lower = item.title.lower()
            if any(keyword in title_lower for keyword in lowered_keywords):
                matched.append(item)
            if len(matched) >= limit:
                break
        result[topic] = matched
    return result


def filter_china_news(news: Iterable[NewsItem], limit: int = 12) -> list[NewsItem]:
    china_keywords = [
        "中国",
        "A股",
        "人民币",
        "央行",
        "证监会",
        "政策",
        "产业",
        "公告",
        "财报",
        "机构",
        "半导体",
        "机器人",
        "算力",
        "数据中心",
        "电力",
        "军工",
        "消费电子",
    ]
    picked: list[NewsItem] = []
    for item in news:
        if item.region == "cn" or any(keyword.lower() in item.title.lower() for keyword in china_keywords):
            picked.append(item)
        if len(picked) >= limit:
            break
    return picked
