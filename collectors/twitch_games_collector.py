"""
twitch_games_collector.py
--------------------------
Twitch Helix APIで「いまTwitchで見られているゲーム」を集計する。
上位の配信をまとめて取得し、ゲームごとに視聴者数・配信数を合算することで
「配信界隈で今ホットなゲーム」と、その勢いを掴む。

トークン取得は twitch_collector.get_app_access_token を再利用する。
"""

import requests

from . import twitch_collector

TWITCH_STREAMS_URL = "https://api.twitch.tv/helix/streams"


def fetch_hot_games(client_id: str, client_secret: str, pages: int = 2) -> dict:
    """
    現在の上位配信（1ページ最大100件）を pages ページ分取得し、
    ゲームごとに視聴者数・配信数・代表配信タイトルを集計して返す。

    戻り値の例:
    {
        "ELDEN RING NIGHTREIGN": {
            "viewers": 45230,          # 上位配信に含まれるこのゲームの合計視聴者
            "stream_count": 12,        # 上位配信に含まれる配信数
            "top_title": "第4回ナイトレイン愛好会",  # このゲームで最も視聴者が多い配信のタイトル
            "top_streamer": "布団ちゃん",
        },
        ...
    }
    ※ 全配信ではなく「上位配信」の集計なので厳密な総視聴者数ではないが、
      トレンド検知には十分な近似になる。
    """
    token = twitch_collector.get_app_access_token(client_id, client_secret)
    headers = {"Client-ID": client_id, "Authorization": f"Bearer {token}"}

    games: dict[str, dict] = {}
    cursor = None

    for _ in range(max(1, pages)):
        params = {"first": 100}
        if cursor:
            params["after"] = cursor

        try:
            resp = requests.get(TWITCH_STREAMS_URL, headers=headers, params=params, timeout=10)
            resp.raise_for_status()
        except requests.exceptions.HTTPError as e:
            raise RuntimeError(
                f"Twitch streams取得に失敗しました (HTTP {resp.status_code})。\nレスポンス: {resp.text}"
            ) from e
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Twitch APIへの接続に失敗しました: {e}") from e

        payload = resp.json()
        for item in payload.get("data", []):
            name = item.get("game_name") or "(不明)"
            viewers = item.get("viewer_count", 0)
            g = games.setdefault(
                name, {"viewers": 0, "stream_count": 0, "top_title": None, "top_streamer": None, "_top_v": -1}
            )
            g["viewers"] += viewers
            g["stream_count"] += 1
            if viewers > g["_top_v"]:
                g["_top_v"] = viewers
                g["top_title"] = item.get("title")
                g["top_streamer"] = item.get("user_name")

        cursor = payload.get("pagination", {}).get("cursor")
        if not cursor:
            break

    # 内部用の _top_v を落として返す
    for g in games.values():
        g.pop("_top_v", None)

    return games


def top_games(games: dict, limit: int = 10) -> list[dict]:
    """集計結果を視聴者数の多い順に並べ、上位 limit 件を返す（表示・AI投入用）。"""
    rows = [{"game": name, **data} for name, data in games.items()]
    rows.sort(key=lambda r: r["viewers"], reverse=True)
    return rows[:limit]


if __name__ == "__main__":
    import os

    cid = os.environ.get("TWITCH_CLIENT_ID", "")
    secret = os.environ.get("TWITCH_CLIENT_SECRET", "")
    if not cid or not secret:
        print("TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET が未設定です。")
    else:
        data = fetch_hot_games(cid, secret, pages=1)
        for row in top_games(data, 10):
            print(f"{row['viewers']:>7} viewers / {row['stream_count']:>3} streams  {row['game']}")
