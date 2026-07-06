"""
youtube_collector.py
---------------------
YouTube Data API v3を使って、対象チャンネルの「配信中かどうか」
「同時接続数」を取得する。

事前準備:
1. Google Cloud ConsoleでYouTube Data API v3を有効化
2. APIキーを発行し、環境変数 YOUTUBE_API_KEY に設定

注意:
YouTube Data APIは1日あたりのクォータ制限（デフォルト10,000ユニット/日）がある。
search.list は100ユニット消費するため、対象数×実行頻度に応じて
quotaを消費しすぎないよう注意する（後述のmain.pyでは1日数回の実行を想定）。
"""

import requests

YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
YOUTUBE_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
YOUTUBE_CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"

# @handle -> UCチャンネルID の解決結果を使い回すためのキャッシュ（プロセス内）
_channel_id_cache: dict[str, str] = {}


def resolve_channel_id(identifier: str, api_key: str) -> str | None:
    """
    YouTubeの識別子(UCチャンネルID または @ハンドル)をUCチャンネルIDに正規化する。

    - 既にUCで始まるIDならそのまま返す
    - @ハンドルなら channels.list?forHandle= で解決（1ユニットと軽量）
    解決できなければ None。
    """
    if not identifier:
        return None
    identifier = identifier.strip()

    # 既にチャンネルID形式ならそのまま使う
    if identifier.startswith("UC") and len(identifier) == 24:
        return identifier

    handle = identifier if identifier.startswith("@") else "@" + identifier
    if handle in _channel_id_cache:
        return _channel_id_cache[handle]

    try:
        resp = requests.get(
            YOUTUBE_CHANNELS_URL,
            params={"part": "id", "forHandle": handle, "key": api_key},
            timeout=10,
        )
        resp.raise_for_status()
    except requests.exceptions.HTTPError as e:
        raise RuntimeError(
            f"YouTubeチャンネルID解決に失敗しました (handle={handle}, HTTP {resp.status_code})。"
            f"\nレスポンス: {resp.text}"
        ) from e
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"YouTube APIへの接続に失敗しました: {e}") from e

    items = resp.json().get("items", [])
    if not items:
        # ハンドルが間違っている / チャンネルが存在しない
        return None

    channel_id = items[0]["id"]
    _channel_id_cache[handle] = channel_id
    return channel_id


def fetch_live_status(channel: str, api_key: str) -> dict:
    """
    指定チャンネルが現在ライブ配信中かどうかと、同時接続数を取得する。
    channel は UCチャンネルID または @ハンドルのどちらでもよい（内部で正規化する）。

    戻り値の例:
    {
        "is_live": True,
        "video_id": "xxxxxxxxxxx",
        "title": "配信タイトル",
        "concurrent_viewers": 12000
    }
    """
    channel_id = resolve_channel_id(channel, api_key)
    if not channel_id:
        return {"is_live": False}

    # Step 1: チャンネルの現在のライブ配信を検索
    search_params = {
        "part": "snippet",
        "channelId": channel_id,
        "eventType": "live",
        "type": "video",
        "key": api_key,
    }
    try:
        resp = requests.get(YOUTUBE_SEARCH_URL, params=search_params, timeout=10)
        resp.raise_for_status()
    except requests.exceptions.HTTPError as e:
        # 403 はAPIキー不正・API未有効化・クォータ超過が主な原因。
        raise RuntimeError(
            f"YouTube search取得に失敗しました (HTTP {resp.status_code})。"
            f"YOUTUBE_API_KEY・API有効化状況・クォータ残量を確認してください。"
            f"\nレスポンス: {resp.text}"
        ) from e
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"YouTube APIへの接続に失敗しました: {e}") from e

    items = resp.json().get("items", [])

    if not items:
        return {"is_live": False}

    video_id = items[0]["id"]["videoId"]
    title = items[0]["snippet"]["title"]

    # Step 2: 同時接続数を取得
    video_params = {
        "part": "liveStreamingDetails",
        "id": video_id,
        "key": api_key,
    }
    try:
        video_resp = requests.get(YOUTUBE_VIDEOS_URL, params=video_params, timeout=10)
        video_resp.raise_for_status()
    except requests.exceptions.HTTPError as e:
        raise RuntimeError(
            f"YouTube videos取得に失敗しました (HTTP {video_resp.status_code})。"
            f"\nレスポンス: {video_resp.text}"
        ) from e
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"YouTube APIへの接続に失敗しました: {e}") from e

    video_items = video_resp.json().get("items", [])

    concurrent_viewers = None
    if video_items:
        details = video_items[0].get("liveStreamingDetails", {})
        concurrent_viewers = details.get("concurrentViewers")

    return {
        "is_live": True,
        "video_id": video_id,
        "title": title,
        "concurrent_viewers": int(concurrent_viewers) if concurrent_viewers else None,
    }


if __name__ == "__main__":
    import os

    key = os.environ.get("YOUTUBE_API_KEY", "")
    if not key:
        print("YOUTUBE_API_KEY が未設定です。")
    else:
        # テスト用チャンネルID（例として適当なIDを入れて実行すること）
        test_channel_id = ""
        if test_channel_id:
            print(fetch_live_status(test_channel_id, key))
        else:
            print("test_channel_id を設定してから実行してください。")
