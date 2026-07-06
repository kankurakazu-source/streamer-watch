"""
x_mentions_collector.py
-------------------------
X API v2の「recent search」エンドポイントを使って、
配信者名の直近言及ツイート・言及数を取得する。

【重要な注意】
X API v2のBasicプラン（月額$200程度〜）以上の契約が必要。
無料プランでは検索エンドポイントが使えないため、契約状況に応じて
このモジュールをスキップする設計にしている(main.py側で制御)。

契約しない場合の代替案:
- 手動でXの検索窓を使い、話題を目視確認する（コスト0円、1日5分程度）
- この関数はダミーデータを返す設計にしておき、後から有効化できるようにする
"""

import requests

X_SEARCH_URL = "https://api.twitter.com/2/tweets/search/recent"


def fetch_mention_summary(display_name: str, bearer_token: str, max_results: int = 30) -> dict:
    """
    指定した配信者名について、直近の言及ツイートを取得し、
    件数と代表的な投稿をまとめて返す。

    戻り値の例:
    {
        "query": "加藤純一",
        "mention_count": 27,
        "sample_texts": ["...", "...", "..."]  # 上位数件のみ（要約用途）
    }
    """
    if not bearer_token:
        # API未契約の場合はNoneを返し、呼び出し側で「取得不可」として扱う
        return {"query": display_name, "mention_count": None, "sample_texts": []}

    headers = {"Authorization": f"Bearer {bearer_token}"}
    params = {
        "query": f'"{display_name}" -is:retweet lang:ja',
        "max_results": min(max_results, 100),
        "tweet.fields": "public_metrics,created_at",
    }

    resp = requests.get(X_SEARCH_URL, headers=headers, params=params, timeout=10)
    resp.raise_for_status()
    payload = resp.json()

    tweets = payload.get("data", [])
    # いいね数の多い順に並べ、代表的な投稿を抽出（下書き生成のヒントに使う）
    tweets_sorted = sorted(
        tweets,
        key=lambda t: t.get("public_metrics", {}).get("like_count", 0),
        reverse=True,
    )
    sample_texts = [t["text"] for t in tweets_sorted[:5]]

    return {
        "query": display_name,
        "mention_count": len(tweets),
        "sample_texts": sample_texts,
    }


if __name__ == "__main__":
    import os

    token = os.environ.get("X_BEARER_TOKEN", "")
    if not token:
        print("X_BEARER_TOKEN が未設定です。API未契約の場合はこのモジュールは使いません。")
    else:
        print(fetch_mention_summary("加藤純一", token))
