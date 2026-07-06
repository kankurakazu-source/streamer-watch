"""
storage.py
----------
収集したデータをSQLiteに蓄積し、「前回実行時との比較」
（同接の急増、オフライン→ライブへの変化など）を可能にする。

急増検知ができると「〇〇の同接が前回比+300%」のような
バズりやすい投稿ネタを自動で拾えるようになる。
"""

import os
import sqlite3
import json
from datetime import datetime, timezone, timedelta
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    streamer_name TEXT NOT NULL,
    platform TEXT NOT NULL,          -- 'twitch' or 'youtube'
    is_live INTEGER NOT NULL,
    viewer_count INTEGER,
    title TEXT,
    game_name TEXT,
    recorded_at TEXT NOT NULL        -- ISO8601
);

CREATE INDEX IF NOT EXISTS idx_snapshots_streamer_time
    ON snapshots (streamer_name, recorded_at);
"""

# ゲーム方針用: ソース横断でゲームの指標（Steam同接・Twitch視聴者数など）を時系列保存し、
# 前回比の急増を検知するためのテーブル。
SCHEMA_GAMES = """
CREATE TABLE IF NOT EXISTS game_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,            -- 'steam' or 'twitch' など
    game_key TEXT NOT NULL,          -- ソース内で一意なキー（Steam appid、Twitchゲーム名など）
    game_name TEXT,                  -- 表示名
    metric TEXT NOT NULL,            -- 'player_count' / 'viewers' など
    value INTEGER,
    recorded_at TEXT NOT NULL        -- ISO8601
);

CREATE INDEX IF NOT EXISTS idx_game_snapshots_key_time
    ON game_snapshots (source, game_key, metric, recorded_at);
"""

# 記事方針用: 生成・公開した記事を記録する。
# 重複トピックの回避（topic_key）と、トップページ記事一覧の再生成（一覧取得）に使う。
SCHEMA_ARTICLES = """
CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,       -- ファイル名/URL用のスラッグ
    title TEXT NOT NULL,
    category TEXT,
    topic_key TEXT,                  -- 重複検知用の正規化キー（主役ゲーム名など）
    excerpt TEXT,                    -- 一覧カード用の抜粋
    image_url TEXT,                  -- サムネイル画像URL（Steam公式アート等。無ければ空）
    created_at TEXT NOT NULL         -- ISO8601
);

CREATE INDEX IF NOT EXISTS idx_articles_created ON articles (created_at);
"""


@contextmanager
def get_connection(db_path: str):
    conn = sqlite3.connect(db_path)
    try:
        yield conn
    finally:
        conn.close()


def init_db(db_path: str):
    # DBファイルの置き場所（例: data/）が無いと sqlite3.connect が失敗するため先に作成する
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with get_connection(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.executescript(SCHEMA_GAMES)
        conn.executescript(SCHEMA_ARTICLES)
        conn.commit()


def save_snapshot(db_path: str, streamer_name: str, platform: str, status: dict):
    """
    1配信者・1プラットフォーム分のスナップショットを保存する。
    status は twitch_collector / youtube_collector が返す辞書を想定。
    """
    now = datetime.now(timezone.utc).isoformat()
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO snapshots
                (streamer_name, platform, is_live, viewer_count, title, game_name, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                streamer_name,
                platform,
                1 if status.get("is_live") else 0,
                status.get("viewer_count") or status.get("concurrent_viewers"),
                status.get("title"),
                status.get("game_name"),
                now,
            ),
        )
        conn.commit()


def get_previous_snapshot(db_path: str, streamer_name: str, platform: str) -> dict | None:
    """
    直前(1つ前)のスナップショットを取得する。急増率の比較に使う。
    """
    with get_connection(db_path) as conn:
        cur = conn.execute(
            """
            SELECT viewer_count, recorded_at, is_live
            FROM snapshots
            WHERE streamer_name = ? AND platform = ?
            ORDER BY recorded_at DESC
            LIMIT 1 OFFSET 1
            """,
            (streamer_name, platform),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {"viewer_count": row[0], "recorded_at": row[1], "is_live": bool(row[2])}


def calc_growth_rate(current: int | None, previous: int | None) -> float | None:
    """
    視聴者数の増加率を計算する。データが無ければNoneを返す。
    """
    if not current or not previous or previous == 0:
        return None
    return round((current - previous) / previous * 100, 1)


# ============================================
# ゲーム方針用: game_snapshots の保存・急増検知
# ============================================
def save_game_metric(db_path: str, source: str, game_key: str, game_name: str,
                     metric: str, value: int | None):
    """1ゲーム・1指標のスナップショットを保存する。"""
    now = datetime.now(timezone.utc).isoformat()
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO game_snapshots (source, game_key, game_name, metric, value, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (source, str(game_key), game_name, metric, value, now),
        )
        conn.commit()


def get_previous_game_metric(db_path: str, source: str, game_key: str, metric: str) -> int | None:
    """直前(1つ前)のスナップショット値を返す。無ければNone。"""
    with get_connection(db_path) as conn:
        cur = conn.execute(
            """
            SELECT value FROM game_snapshots
            WHERE source = ? AND game_key = ? AND metric = ?
            ORDER BY recorded_at DESC
            LIMIT 1 OFFSET 1
            """,
            (source, str(game_key), metric),
        )
        row = cur.fetchone()
        return row[0] if row and row[0] is not None else None


def get_prev_day_metric(db_path: str, source: str, game_key: str, metric: str,
                       min_age_hours: int = 18) -> int | None:
    """
    「前日」の比較用に、min_age_hours 時間以上前で最も新しいスナップショット値を返す。
    毎日運用なら実質24時間前付近の値になり、「前日比」を正確に言えるようにする。
    十分に古いデータが無ければ None（=前日比は主張しない）。
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=min_age_hours)).isoformat()
    with get_connection(db_path) as conn:
        cur = conn.execute(
            """
            SELECT value FROM game_snapshots
            WHERE source = ? AND game_key = ? AND metric = ? AND recorded_at <= ?
            ORDER BY recorded_at DESC
            LIMIT 1
            """,
            (source, str(game_key), metric, cutoff),
        )
        row = cur.fetchone()
        return row[0] if row and row[0] is not None else None


# ============================================
# 記事方針用: articles の保存・一覧取得・重複検知
# ============================================
def save_article(db_path: str, slug: str, title: str, category: str,
                topic_key: str, excerpt: str, image_url: str) -> None:
    """公開した記事を1件記録する（同一slugは上書き）。"""
    now = datetime.now(timezone.utc).isoformat()
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO articles (slug, title, category, topic_key, excerpt, image_url, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(slug) DO UPDATE SET
                title=excluded.title, category=excluded.category,
                topic_key=excluded.topic_key, excerpt=excluded.excerpt,
                image_url=excluded.image_url
            """,
            (slug, title, category, topic_key, excerpt, image_url, now),
        )
        conn.commit()


def list_articles(db_path: str, limit: int = 12) -> list[dict]:
    """新しい順に記事メタデータを返す（トップページ記事一覧の再生成用）。"""
    with get_connection(db_path) as conn:
        cur = conn.execute(
            """
            SELECT slug, title, category, topic_key, excerpt, image_url, created_at
            FROM articles ORDER BY created_at DESC LIMIT ?
            """,
            (limit,),
        )
        cols = ["slug", "title", "category", "topic_key", "excerpt", "image_url", "created_at"]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def recent_article_topics(db_path: str, days: int = 21) -> list[dict]:
    """直近days日以内に公開したタイトル/トピックを返す（重複回避のプロンプト用）。"""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with get_connection(db_path) as conn:
        cur = conn.execute(
            """
            SELECT title, topic_key FROM articles
            WHERE created_at >= ? ORDER BY created_at DESC
            """,
            (cutoff,),
        )
        return [{"title": r[0], "topic_key": r[1]} for r in cur.fetchall()]
