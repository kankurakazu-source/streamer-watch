"""
twitch_collector.py
--------------------
Twitch Helix APIを使って、対象配信者の「配信中かどうか」「視聴者数」
「配信タイトル」を取得する。

事前準備:
1. https://dev.twitch.tv/console/apps でアプリ登録
2. Client ID / Client Secret を環境変数 TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET に設定
"""

import requests
import time
from datetime import datetime, timezone

TWITCH_AUTH_URL = "https://id.twitch.tv/oauth2/token"
TWITCH_STREAMS_URL = "https://api.twitch.tv/helix/streams"

_token_cache = {"token": None, "expires_at": 0}


def get_app_access_token(client_id: str, client_secret: str) -> str:
    """
    Twitch App Access Tokenを取得（キャッシュ付き）。
    トークンは通常60日程度有効だが、余裕をもって再取得する。
    """
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"]:
        return _token_cache["token"]

    try:
        resp = requests.post(
            TWITCH_AUTH_URL,
            params={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "client_credentials",
            },
            timeout=10,
        )
        resp.raise_for_status()
    except requests.exceptions.HTTPError as e:
        # 401/403 の多くは Client ID / Secret の誤りが原因。原因を分かりやすく伝える。
        raise RuntimeError(
            f"Twitchアクセストークン取得に失敗しました "
            f"(HTTP {resp.status_code})。TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET を確認してください。"
            f"\nレスポンス: {resp.text}"
        ) from e
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Twitch認証サーバへの接続に失敗しました: {e}") from e

    payload = resp.json()

    _token_cache["token"] = payload["access_token"]
    # expires_inは秒単位。安全マージンとして300秒早めに切れたことにする
    _token_cache["expires_at"] = now + payload.get("expires_in", 3600) - 300
    return _token_cache["token"]


def fetch_stream_status(logins: list[str], client_id: str, client_secret: str) -> dict:
    """
    複数のTwitchログイン名について、現在の配信状況を一括取得する。

    戻り値の例:
    {
        "kato_junichi0817": {
            "is_live": True,
            "viewer_count": 45230,
            "title": "雑談",
            "started_at": "2026-07-05T10:00:00Z",
            "game_name": "Just Chatting"
        },
        "some_offline_user": {
            "is_live": False
        }
    }
    """
    if not logins:
        return {}

    token = get_app_access_token(client_id, client_secret)
    headers = {
        "Client-ID": client_id,
        "Authorization": f"Bearer {token}",
    }

    # Helix APIは一度に最大100件のuser_loginを受け付ける
    params = [("user_login", login) for login in logins if login]

    try:
        resp = requests.get(TWITCH_STREAMS_URL, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
    except requests.exceptions.HTTPError as e:
        raise RuntimeError(
            f"Twitch streams取得に失敗しました (HTTP {resp.status_code})。\nレスポンス: {resp.text}"
        ) from e
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Twitch APIへの接続に失敗しました: {e}") from e

    live_data = {item["user_login"]: item for item in resp.json().get("data", [])}

    result = {}
    for login in logins:
        if not login:
            continue
        if login in live_data:
            item = live_data[login]
            result[login] = {
                "is_live": True,
                "viewer_count": item["viewer_count"],
                "title": item["title"],
                "started_at": item["started_at"],
                "game_name": item["game_name"],
            }
        else:
            result[login] = {"is_live": False}

    return result


if __name__ == "__main__":
    # 単体テスト用（実行にはAPIキーが必要）
    import os

    cid = os.environ.get("TWITCH_CLIENT_ID", "")
    secret = os.environ.get("TWITCH_CLIENT_SECRET", "")
    if not cid or not secret:
        print("TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET が未設定です。")
    else:
        test_logins = ["kato_junichi0817"]
        data = fetch_stream_status(test_logins, cid, secret)
        print(data)
