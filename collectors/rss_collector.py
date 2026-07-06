"""
rss_collector.py
----------------
国内外ゲームメディアのRSSから最新ニュース見出しを収集する。
新作・アップデート・セール・業界ニュースの「速報」素材と、考察の「裏取り」に使う。

feedparser を利用。各フィードは失敗しても全体を止めず、取れた分だけ返す。
"""

import re
from datetime import datetime, timezone, timedelta

import feedparser

_TAG_RE = re.compile(r"<[^>]+>")


def _clean(text: str, limit: int = 160) -> str:
    """HTMLタグを除去して短く整える。"""
    if not text:
        return ""
    text = _TAG_RE.sub("", text)
    text = " ".join(text.split())
    return text[:limit]


def _entry_datetime(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            try:
                return datetime(*t[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
    return None


def fetch_feed(name: str, url: str, limit: int = 8) -> list[dict]:
    """1フィードから最新エントリを取得する。失敗時は空リスト。"""
    try:
        parsed = feedparser.parse(url)
    except Exception as e:
        print(f"[WARN] RSS取得失敗 {name}: {e}")
        return []

    if getattr(parsed, "bozo", 0) and not parsed.entries:
        print(f"[WARN] RSS解析不良 {name}: {getattr(parsed, 'bozo_exception', '')}")
        return []

    rows = []
    for entry in parsed.entries[:limit]:
        dt = _entry_datetime(entry)
        rows.append(
            {
                "source": name,
                "title": _clean(entry.get("title", ""), 200),
                "summary": _clean(entry.get("summary", ""), 160),
                "link": entry.get("link", ""),
                "published": dt.isoformat() if dt else None,
                "_dt": dt,
            }
        )
    return rows


def fetch_all(feeds: list[dict], per_feed_limit: int = 8, recent_hours: int | None = None) -> list[dict]:
    """
    複数フィードをまとめて取得し、新しい順に並べて返す。
    recent_hours を指定すると、その時間内の記事だけに絞る（速報候補の抽出用）。
    """
    all_rows = []
    for feed in feeds:
        all_rows.extend(fetch_feed(feed["name"], feed["url"], per_feed_limit))

    if recent_hours is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=recent_hours)
        all_rows = [r for r in all_rows if r["_dt"] is None or r["_dt"] >= cutoff]

    # 新しい順（日時不明は末尾）
    all_rows.sort(key=lambda r: r["_dt"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    # 内部用の _dt を落として返す
    for r in all_rows:
        r.pop("_dt", None)
    return all_rows


if __name__ == "__main__":
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    feeds = [
        {"name": "4Gamer", "url": "https://www.4gamer.net/rss/index.xml"},
        {"name": "AUTOMATON", "url": "https://automaton-media.com/feed/"},
        {"name": "IGN", "url": "https://feeds.feedburner.com/ign/all"},
    ]
    for r in fetch_all(feeds, per_feed_limit=5, recent_hours=None)[:15]:
        print(f"[{r['source']}] {r['published']}  {r['title'][:60]}")
