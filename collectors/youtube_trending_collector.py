"""
youtube_trending_collector.py
------------------------------
YouTube Data API v3 の「急上昇（most popular）」からゲームカテゴリの動画を取得する。
regionCode を切り替えて日本＋海外の両方の話題を拾う（両方バランス方針）。

videos.list(chart=mostPopular) は1回あたり約1ユニットと軽量。
videoCategoryId=20 が "Gaming" カテゴリ。
"""

import requests

YOUTUBE_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
GAMING_CATEGORY_ID = "20"


def fetch_trending_games(api_key: str, region_code: str = "JP", max_results: int = 20) -> list[dict]:
    """
    指定リージョンのゲームカテゴリ急上昇動画を取得する。

    戻り値の例:
    [
        {
            "region": "JP",
            "title": "新作○○を実況してみた",
            "channel": "○○チャンネル",
            "view_count": 523000,
            "like_count": 41000,
            "published_at": "2026-07-05T09:00:00Z",
            "video_id": "xxxx",
        },
        ...
    ]
    """
    params = {
        "part": "snippet,statistics",
        "chart": "mostPopular",
        "videoCategoryId": GAMING_CATEGORY_ID,
        "regionCode": region_code,
        "maxResults": min(max_results, 50),
        "key": api_key,
    }
    try:
        resp = requests.get(YOUTUBE_VIDEOS_URL, params=params, timeout=10)
        resp.raise_for_status()
    except requests.exceptions.HTTPError as e:
        raise RuntimeError(
            f"YouTube急上昇取得に失敗しました (region={region_code}, HTTP {resp.status_code})。"
            f"YOUTUBE_API_KEY・クォータ残量を確認してください。\nレスポンス: {resp.text}"
        ) from e
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"YouTube APIへの接続に失敗しました: {e}") from e

    results = []
    for item in resp.json().get("items", []):
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        vid = item.get("id")
        results.append(
            {
                "region": region_code,
                "title": snippet.get("title"),
                "channel": snippet.get("channelTitle"),
                "view_count": int(stats.get("viewCount", 0)) if stats.get("viewCount") else None,
                "like_count": int(stats.get("likeCount", 0)) if stats.get("likeCount") else None,
                "published_at": snippet.get("publishedAt"),
                "video_id": vid,
                "url": f"https://www.youtube.com/watch?v={vid}" if vid else None,
                "thumbnail": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg" if vid else None,
            }
        )
    return results


def fetch_multi_region(api_key: str, regions: list[str], max_results: int = 20) -> list[dict]:
    """複数リージョンをまとめて取得して1つのリストに結合する。"""
    out = []
    for region in regions:
        try:
            out.extend(fetch_trending_games(api_key, region, max_results))
        except Exception as e:
            print(f"[WARN] YouTube急上昇({region})取得失敗: {e}")
    return out


if __name__ == "__main__":
    import os

    key = os.environ.get("YOUTUBE_API_KEY", "")
    if not key:
        print("YOUTUBE_API_KEY が未設定です。")
    else:
        for v in fetch_multi_region(key, ["JP", "US"], 10):
            print(f"[{v['region']}] {v['view_count']:>9} views  {v['title']}  ({v['channel']})")
