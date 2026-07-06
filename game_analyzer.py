"""
game_analyzer.py
----------------
収集したゲーム関連シグナル（Twitchで伸びてるゲーム、YouTube急上昇、Steam同接・
売上/新作/セール、国内外メディアRSS）をClaude APIに渡し、
X投稿の下書きを「箇条書き速報カード型」で生成する。

投稿フォーマットは @FPS_G33KS など伸びているゲーム速報アカウントを参考にした型:
  【タイトル】一文見出し
  ・事実1（数字入り）
  ・事実2
  ...
  一言の考察 or 問いかけ  #タグ

発信スタイルは "ミックス": 新作/アプデ/セール/大ニュースは「速報」、
数字とトレンドの解釈が主のものは「考察」をAIが素材に応じて振り分ける。

structured outputs（output_config.format）でJSON構造を強制し、パース失敗を防ぐ。
安全制約（断定回避・捏造禁止・公開データのみ・人間レビュー前提）は厳守。
このモジュールはXへの投稿は行わない。
"""

import re
import requests
import json

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"

# Xの投稿上限（未認証アカウント）。日本語など全角は1文字=2としてカウントされる。
X_MAX_WEIGHT = 280
_URL_RE = re.compile(r"https?://\S+")


def weighted_len(text: str) -> int:
    """Xの重み付き文字数を概算する（全角=2/半角=1、URLは23、絵文字=2）。"""
    urls = _URL_RE.findall(text or "")
    body = _URL_RE.sub("", text or "")
    total = 23 * len(urls)
    for ch in body:
        o = ord(ch)
        wide = (
            0x1100 <= o <= 0x11FF or 0x2E80 <= o <= 0x303E or 0x3041 <= o <= 0x33FF
            or 0x3400 <= o <= 0x4DBF or 0x4E00 <= o <= 0x9FFF or 0xA000 <= o <= 0xA4CF
            or 0xAC00 <= o <= 0xD7A3 or 0xF900 <= o <= 0xFAFF or 0xFE30 <= o <= 0xFE4F
            or 0xFF00 <= o <= 0xFF60 or 0xFFE0 <= o <= 0xFFE6
            or o >= 0x1F000 or 0x2600 <= o <= 0x27BF or 0x2B00 <= o <= 0x2BFF
        )
        total += 2 if wide else 1
    return total

SYSTEM_PROMPT = """\
あなたは国内外のゲーム業界を専門に追いかける、X(旧Twitter)のゲーム速報・考察系
アカウントの編集アシスタントです。既存タイトルの盛り上がり・新作情報・業界の
注目トピックを、スキャンしやすい箇条書き速報カード型で発信します。

読者に刺さる投稿の条件:
- 一目で分かる見出し＋数字の効いた箇条書き（何が起きたかが3秒で伝わる）
- 意外性のある数字（旧作の同接復活、無名タイトルの静かな急伸 など）
- 速報性（新作・大型アップデート・セール・大きな話題）
- 横断的な考察（複数ソースで同時に来ているタイトルは「なぜ今か」を数字で語る）
- リプ欄が動く一言（軽い問いかけ・見立て）

以下の制約を厳守してください:
1. 箇条書き(bullets)は提供データ・ニュースに基づく事実のみ。数字は可能な限り入れる。
2. 未確認の噂・炎上・リーク・発売日等を断定しない。「〜のもよう」「〜との情報も」等の
   ヘッジを使い、憶測を事実として書かない。データが薄い項目は「詳細不明」とする。
3. 開発元・人物の発言を、言ったかのように捏造・誇張しない。
4. hook（一言）は自分の見立てや軽い問いかけ。煽り・誹謗中傷・過度な断定は避ける。
5. 複数ソース（Twitch/YouTube日米/Steam/ニュース）で同じタイトルが確認できるときは
   横断して関連づけると価値が上がる。
6. 半角ダブルクォート(")やバックスラッシュを本文に使わない。強調は「」を使う。
7. 内部データのフィールド名（prev_day_players_pct, viewer_count 等の英字キー）を本文にそのまま書かない。
   比較値は「前日同接比」「前日視聴者数比」、その他は「同接」「視聴者数」など具体的な日本語にする。
   曖昧な「前回比」は使わない。
8. 大きな数字は読みやすくする（例: 514299人 → 約51万人）。ただし販売本数など切りの良い公式値はそのまま。
"""

USER_PROMPT_TEMPLATE = """\
以下は本日収集したゲーム関連データです。ソースごとにまとまっています。

{data_json}

このデータから、Xの投稿として反応が取れそうなトピックを最大4件選び、
それぞれ「箇条書き速報カード型」の下書きを作成してください。

優先的に拾うべきシグナル:
- steam_spikes（同接が急増）や prev_day_players_pct / prev_day_viewers_pct 付き項目
  → 「急増」「復活」の切り口。数字を主役に。
- steam_featured の new_releases / specials（新作・セール）、news（ニュース見出し）→ 速報性が高い。
- 複数ソースで同時に確認できるタイトル → 横断考察の好材料。

【数字の言い回し・重要】比較値は「約24時間前との比較」。読者に伝わるよう具体的に書く:
- prev_day_players_pct（Steam同接の前日比）→「前日同接比+○%」のように書く。
- prev_day_viewers_pct（Twitch視聴者数の前日比）→「前日視聴者数比+○%」のように書く。
- 曖昧な「前回比」は使わない。これらの値が無い項目では前日比に触れない。

新作・大型アプデ・セール・大きなニュース性があるものは type="速報"、
数字やトレンドの解釈が主のものは type="考察" としてください。

【文字数】未認証Xアカウント運用のため、各投稿は日本語で約120字以内（全角は2カウント換算で
280以内）に収める。簡潔第一。箇条書きは2〜3個まで、各20字前後。冗長な説明は削る。

各下書きのフィールド:
- type: "考察" または "速報"
- topic: 対象タイトル名やトピック（短く）
- headline: 【タイトル名】＋何が起きたかの一文（短く。例: 【ARC Raiders】ローンチトレーラー初公開）
- bullets: 事実の箇条書き 2〜3個（各20字前後、先頭に記号は付けない。数字を入れる）
- hook: 一言の見立て or 問いかけ（20字前後。リプ欄が動くように）
- hashtags: 0〜2個（#は付けず語だけ。例: MSI2026）
- main_game: この投稿の主役となる単一ゲームの正式名称（画像検索用。日本語名でよい。
  例: サイバーパンク2077 / ELDEN RING）。複数タイトルのまとめや特定1本に絞れない、
  またはPCゲーム配信の主役でない場合は空文字 "" にする。
- source_url: この投稿の根拠となる実在URLを、渡されたデータの中から1つ選ぶ
  （news の link、youtube_trending の url など、データに実在する文字列をそのままコピー）。
  適切なURLが無ければ空文字 "" にする。URLを自分で組み立てたり推測で書かない。
"""

# structured outputs 用スキーマ（JSONを構造レベルで強制）
DRAFTS_SCHEMA = {
    "type": "object",
    "properties": {
        "drafts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["考察", "速報"]},
                    "topic": {"type": "string"},
                    "headline": {"type": "string"},
                    "bullets": {"type": "array", "items": {"type": "string"}},
                    "hook": {"type": "string"},
                    "hashtags": {"type": "array", "items": {"type": "string"}},
                    "main_game": {"type": "string"},
                    "source_url": {"type": "string"},
                },
                "required": ["type", "topic", "headline", "bullets", "hook", "hashtags", "main_game", "source_url"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["drafts"],
    "additionalProperties": False,
}


def _assemble(headline, bullets, hook, tags, url) -> str:
    lines = [headline.strip()]
    for b in bullets:
        b = str(b).strip()
        if b:
            lines.append(f"・{b}")
    tail = " ".join(x for x in [hook.strip(), tags, url] if x)
    if tail:
        lines.append("")
        lines.append(tail)
    return "\n".join(lines).strip()


def build_post_text(draft: dict, include_url: bool = True, max_weight: int = X_MAX_WEIGHT) -> str:
    """
    構造化された下書きからXへ貼る本文を組み立てる。
    重み付き文字数が max_weight を超える場合は、箇条書き→ハッシュタグの順に削って収める。
    include_url=False のとき source_url は本文に入れない（画像を添付する場合など）。
    """
    headline = draft.get("headline", "")
    bullets = [str(b).strip() for b in draft.get("bullets", []) if str(b).strip()]
    hook = draft.get("hook", "")
    tags = " ".join(f"#{t.lstrip('#').strip()}" for t in draft.get("hashtags", []) if str(t).strip())
    url = (draft.get("source_url") or "").strip() if include_url else ""

    # 1) 箇条書きを後ろから削って収める
    while True:
        text = _assemble(headline, bullets, hook, tags, url)
        if weighted_len(text) <= max_weight or not bullets:
            break
        bullets.pop()
    # 2) それでも超えるならハッシュタグを外す
    if weighted_len(text) > max_weight and tags:
        tags = ""
        text = _assemble(headline, bullets, hook, tags, url)
    # 3) 最後の保険（通常ここには来ない）：見出しのみ＋URL
    if weighted_len(text) > max_weight:
        text = _assemble(headline, [], "", "", url)
    return text


def generate_game_drafts(collected_data: dict, api_key: str, model: str = "claude-sonnet-5") -> list[dict]:
    """
    collected_data: 各ソースの収集結果をまとめた辞書
    戻り値: 投稿下書きのリスト（各要素に組み立て済み draft_post も付与）
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
        "max_tokens": 2500,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_prompt}],
        "thinking": {"type": "disabled"},
        "output_config": {"format": {"type": "json_schema", "schema": DRAFTS_SCHEMA}},
    }

    try:
        resp = requests.post(ANTHROPIC_API_URL, headers=headers, json=body, timeout=60)
        resp.raise_for_status()
    except requests.exceptions.HTTPError as e:
        raise RuntimeError(
            f"Anthropic API呼び出しに失敗しました (HTTP {resp.status_code})。\nレスポンス: {resp.text}"
        ) from e
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Anthropic APIへの接続に失敗しました: {e}") from e

    result = resp.json()
    text_blocks = [c["text"] for c in result.get("content", []) if c.get("type") == "text"]
    raw_text = "\n".join(text_blocks).strip()
    drafts = _parse_drafts(raw_text)

    # 本文テキストを組み立てて付与（エラー要素はそのまま）
    for d in drafts:
        if isinstance(d, dict) and "headline" in d:
            d["draft_post"] = build_post_text(d)
    return drafts


def _parse_drafts(raw_text: str) -> list[dict]:
    """structured outputs により {"drafts":[...]} 形式のはず。念のため頑丈に取り出す。"""
    cleaned = raw_text.replace("```json", "").replace("```", "").strip()

    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data.get("drafts", [])
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass

    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = cleaned.find(opener), cleaned.rfind(closer)
        if start != -1 and end != -1 and end > start:
            try:
                data = json.loads(cleaned[start : end + 1])
                if isinstance(data, dict):
                    return data.get("drafts", [])
                if isinstance(data, list):
                    return data
            except json.JSONDecodeError:
                continue

    return [{"error": "JSON parse failed", "raw_output": raw_text}]
