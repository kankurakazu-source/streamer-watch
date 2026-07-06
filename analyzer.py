"""
analyzer.py
-----------
収集したデータ(視聴者数・急増率・配信タイトルなど)をClaude APIに渡し、
「数字＋考察」形式のX投稿下書きを生成する。

生成された下書きは必ず人間がレビューしてから投稿すること。
このモジュール自体はXへの投稿は行わない（安全のため分離している）。
"""

import requests
import json

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"

SYSTEM_PROMPT = """\
あなたは日本の人気ストリーマー界隈(加藤純一、布団ちゃん、もこう、はんじょう、たいじ、
バトラ、GON、Laz、柊つるぎ、関優太など)を専門に追いかける、X(旧Twitter)の
考察系インフルエンサーのアシスタントです。

以下の制約を厳守して投稿下書きを作成してください:

1. 提供されたデータ(視聴者数、配信タイトル、増加率など)に基づく事実部分と、
   あなた自身の考察部分を明確に分けて書く。
2. 未確認の噂・炎上の詳細を断定的に書かない。「〜という声もある」程度の
   ヘッジ表現を使い、憶測を事実として書かない。
3. 配信者本人が言っていない発言を、言ったかのように書かない(引用の捏造禁止)。
4. 各投稿は140字以内。煽り言葉や誹謗中傷にあたる表現は使わない。
5. データが無い項目については無理に断定せず、素直に「詳細不明」と書く。
6. 出力は必ず指定のJSON形式のみ。前置きや説明文は一切付けない。
"""

USER_PROMPT_TEMPLATE = """\
以下は本日収集したストリーマー関連データです。

{data_json}

このデータの中から、Xの投稿として反応が取れそうなものを最大3件選び、
それぞれについて「数字（事実）＋一言考察」形式の投稿下書きを作成してください。

出力形式（このJSON配列のみを出力すること）:
[
  {{
    "streamer": "配信者名",
    "fact": "データに基づく事実部分",
    "commentary": "考察部分（自分の言葉で）",
    "draft_post": "実際にXに投稿する文章(140字以内、事実+考察を自然につなげたもの)"
  }}
]
"""


def generate_draft_posts(collected_data: dict, api_key: str, model: str = "claude-sonnet-4-6") -> list[dict]:
    """
    collected_data: 各配信者のスナップショット・急増率などをまとめた辞書
    戻り値: 投稿下書きのリスト（レビュー用）
    """
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY が設定されていません。")

    user_prompt = USER_PROMPT_TEMPLATE.format(
        data_json=json.dumps(collected_data, ensure_ascii=False, indent=2)
    )

    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    body = {
        "model": model,
        "max_tokens": 1500,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_prompt}],
    }

    resp = requests.post(ANTHROPIC_API_URL, headers=headers, json=body, timeout=30)
    resp.raise_for_status()
    result = resp.json()

    text_blocks = [c["text"] for c in result.get("content", []) if c.get("type") == "text"]
    raw_text = "\n".join(text_blocks).strip()

    # モデルがコードフェンス付きで返す場合に備えて除去
    cleaned = raw_text.replace("```json", "").replace("```", "").strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # パース失敗時は生データを返し、人間が目視確認できるようにする
        return [{"error": "JSON parse failed", "raw_output": raw_text}]
