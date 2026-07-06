"""
test_apis.py
------------
Twitch / YouTube / Anthropic の各APIキーが正しく機能するかを確認する
「実機スモークテスト」スクリプト。

使い方:
    1. `.env` に各APIキーを記入する（.env.example をコピーして作成）
    2. pip install -r requirements.txt
    3. python test_apis.py

このスクリプトは読み取り専用の軽いAPI呼び出しのみを行い、
X(Twitter)への投稿など外部への書き込みは一切行わない。
Anthropic APIはデフォルトでは「キーの有無」だけ確認し、
課金が発生する実呼び出しはスキップする（--call-anthropic で実行可能）。
"""

import sys

import requests

# Windowsのコンソール(cp932)でも日本語が文字化けしないよう標準出力をUTF-8化
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import config
from collectors import twitch_collector, youtube_collector

# 端末によっては ✓/✗ が表示できないので簡素な記号にしておく
OK = "[OK]  "
NG = "[FAIL]"
SKIP = "[SKIP]"


def test_twitch() -> bool:
    print("\n=== Twitch ===")
    if not config.TWITCH_CLIENT_ID or not config.TWITCH_CLIENT_SECRET:
        print(f"{SKIP} TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET が未設定です。")
        return False

    # 実在が確実な加藤純一のログイン名で疎通確認する
    test_login = "kato_junichi0817"
    try:
        data = twitch_collector.fetch_stream_status(
            [test_login], config.TWITCH_CLIENT_ID, config.TWITCH_CLIENT_SECRET
        )
    except Exception as e:
        print(f"{NG} Twitch API呼び出しに失敗しました:\n{e}")
        return False

    status = data.get(test_login, {})
    live = "配信中" if status.get("is_live") else "オフライン"
    print(f"{OK} 認証・疎通OK（{test_login} は現在 {live}）")
    if status.get("is_live"):
        print(f"       視聴者数: {status.get('viewer_count')} / タイトル: {status.get('title')}")
    return True


def test_youtube() -> bool:
    print("\n=== YouTube ===")
    if not config.YOUTUBE_API_KEY:
        print(f"{SKIP} YOUTUBE_API_KEY が未設定です。")
        return False

    # まずキー自体が有効か軽量エンドポイント(i18nLanguages, 1ユニット)で確認する
    try:
        resp = requests.get(
            "https://www.googleapis.com/youtube/v3/i18nLanguages",
            params={"part": "snippet", "key": config.YOUTUBE_API_KEY},
            timeout=10,
        )
        resp.raise_for_status()
    except requests.exceptions.HTTPError:
        print(
            f"{NG} YOUTUBE_API_KEY が無効か、YouTube Data API v3が未有効化の可能性があります "
            f"(HTTP {resp.status_code})。\n       レスポンス: {resp.text}"
        )
        return False
    except requests.exceptions.RequestException as e:
        print(f"{NG} YouTube APIへの接続に失敗しました: {e}")
        return False

    print(f"{OK} APIキー有効・疎通OK")

    # channel_id または @handle が設定済みの配信者について、ハンドル解決＋ライブ判定を試す
    # （search.listは1回あたり約100ユニット消費するので設定済みのものだけ実行）
    configured = [
        s for s in config.STREAMERS
        if s.get("youtube_channel_id") or s.get("youtube_handle")
    ]
    if not configured:
        print(f"{SKIP} youtube_channel_id / youtube_handle が未設定のため、ライブ判定テストはスキップします。")
        return True

    for s in configured:
        target = s.get("youtube_channel_id") or s.get("youtube_handle")
        try:
            resolved = youtube_collector.resolve_channel_id(target, config.YOUTUBE_API_KEY)
            if not resolved:
                print(f"       {s['display_name']}: ハンドル {target} を解決できませんでした（要確認）")
                continue
            yt = youtube_collector.fetch_live_status(target, config.YOUTUBE_API_KEY)
            live = "配信中" if yt.get("is_live") else "オフライン"
            extra = f" / 同接: {yt.get('concurrent_viewers')}" if yt.get("is_live") else ""
            print(f"       {s['display_name']} ({resolved}): {live}{extra}")
        except Exception as e:
            print(f"       {s['display_name']}: 取得失敗 -> {e}")
    return True


def test_anthropic(call: bool) -> bool:
    print("\n=== Anthropic ===")
    if not config.ANTHROPIC_API_KEY:
        print(f"{SKIP} ANTHROPIC_API_KEY が未設定です。")
        return False
    if not call:
        print(f"{OK} キーは設定済み（課金回避のため実呼び出しはスキップ。--call-anthropic で実行可能）")
        return True

    # 最小構成のメッセージで疎通確認（わずかに課金されます）
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "Content-Type": "application/json",
                "x-api-key": config.ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 8,
                "messages": [{"role": "user", "content": "ping"}],
            },
            timeout=30,
        )
        resp.raise_for_status()
    except requests.exceptions.HTTPError:
        print(f"{NG} Anthropic API呼び出しに失敗 (HTTP {resp.status_code}): {resp.text}")
        return False
    except requests.exceptions.RequestException as e:
        print(f"{NG} Anthropic APIへの接続に失敗しました: {e}")
        return False

    print(f"{OK} 認証・疎通OK")
    return True


def main():
    call_anthropic = "--call-anthropic" in sys.argv
    print("APIスモークテストを開始します（読み取り専用・投稿は行いません）")
    results = {
        "Twitch": test_twitch(),
        "YouTube": test_youtube(),
        "Anthropic": test_anthropic(call_anthropic),
    }
    print("\n=== 結果まとめ ===")
    for name, ok in results.items():
        print(f"  {name}: {'成功/設定OK' if ok else '未設定または失敗'}")


if __name__ == "__main__":
    main()
