"""
article_generator.py
---------------------
ローカル記事生成パイプライン（アフィリエイト記事の自動下書き→サイト掲載→通知）。

流れ:
  1. game_watch.collect_all() で最新のゲームデータを収集
  2. 直近に公開済みのトピックを避けて、Claudeに「1本の記事」を書かせる
  3. Steam公式アート/割引を各タイトルに付与（画像・購入ボックス用）
  4. 記事HTMLを site/articles/<slug>.html として書き出す
  5. トップページ(index.html)の記事一覧を最新記事で更新
  6. Xポスト文面（要約＋記事リンク）を組み立て、メールで通知

投稿はしない（人間がメールを確認して手動でXポスト）。まずローカル検証用。

使い方:
    .venv\\Scripts\\python.exe article_generator.py
"""

import json
import os
import re
import sys
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import requests

import config
import storage
import article_render
import game_watch
from collectors import steam_collector

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"

SYSTEM_PROMPT = """\
あなたは国内外のゲームトレンドをデータで解説する、アフィリエイト型ゲーム情報サイトの
編集ライターです。denfaminicogamer や FPS_G33KS のように、数字と一次情報に基づいた
読み応えのある記事を書きます。読者が「これは買い/要チェック」と判断できる実用記事が目標です。

記事の狙い:
- Steamのセール・同接の伸び・新作・話題性など、提供データから今おもしろいトピックを1つ選ぶ。
- 同時接続数の絶対値だけでなく「勢い（前日比の伸び）」も重視し、まだ無名でも伸びている作品を拾う。
- セール対象タイトルは購入導線（buy）を付け、読者の行動につなげる。

厳守する制約:
1. 本文は提供データ・公開情報に基づく事実のみ。数字は可能な範囲で入れるが、無い数字を作らない。
2. 価格（円）は書かない。割引率はデータにある discount_percent のみ触れてよい（無ければ触れない）。
3. 未確認の噂・リーク・発売日・炎上を断定しない。「〜のもよう」「〜との情報も」等でヘッジする。
4. 開発元や人物の発言を捏造・誇張しない。
5. 比較値は「前日同接比+○%」のように具体的に。曖昧な「前回比」やデータに無い比較は書かない。
6. 半角ダブルクォート(")やバックスラッシュを本文に使わない。強調は「」を使う。
7. 内部データの英字フィールド名（prev_day_players_pct 等）を本文にそのまま書かない。
8. 大きな数字は読みやすく（例: 514299人→約51万人）。販売本数など切りの良い公式値はそのまま。
9. 誠実なトーン。過度な煽り・誹謗中傷はしない。読者の判断材料を提供する姿勢。
"""

USER_TEMPLATE = """\
以下は本日収集したゲーム関連データ（Twitch人気ゲーム/YouTube急上昇日米/Steam売上・新作・セール・同接/
国内外ニュース）です。

{data_json}

直近に公開済みの記事タイトル（これらと重複しないトピックを選ぶこと）:
{recent_titles}

この中から、いま最も記事価値が高いトピックを【1つだけ】選び、1本の記事を書いてください。
セール・急増・新作・話題のいずれかを軸に、読者が得をする実用記事にします。

カテゴリは次から1つ選ぶ: {categories}

記事の構成:
- title: 具体的で内容が伝わる見出し（誇張しすぎない。例「Steamサマーセール、勢いで選ぶ買い時タイトル」）
- category: 上記から1つ
- main_game: 記事の主役となる単一ゲームの正式名称（Steam画像検索用。日本語名可。複数まとめで主役が定まらなければ空）
- lead: リード文（2〜3文。何が起きていて、なぜ今読む価値があるか）
- tldr: 結論を一言で（迷ったら何を見る/買うべきか）
- sections: 3〜5個。各 {{heading, body, game_name}}。
    heading: 小見出し（タイトル名を含めてよい）
    body: 2〜4文の本文（数字や根拠を入れる。段落は改行で区切ってよい）
    game_name: そのセクションで購入導線を出す単一ゲームの正式名称。セール/購入対象でなければ空文字。
- conclusion: まとめ（2〜3文）
- x_post: この記事を紹介するX投稿の本文（日本語で約90字以内。全角2換算で230程度まで。
    記事リンクは後でこちらが付けるのでURLは書かない。ハッシュタグも別途付けるので本文に含めない）
- hashtags: 0〜2個（#は付けず語だけ。例: Steamセール）
- topic_key: 重複検知用のキー。主役ゲーム名 or トピックを短い日本語で（例「Steamサマーセール」）
"""

ARTICLE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "category": {"type": "string", "enum": config.ARTICLE_CATEGORIES},
        "main_game": {"type": "string"},
        "lead": {"type": "string"},
        "tldr": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "heading": {"type": "string"},
                    "body": {"type": "string"},
                    "game_name": {"type": "string"},
                },
                "required": ["heading", "body", "game_name"],
                "additionalProperties": False,
            },
        },
        "conclusion": {"type": "string"},
        "x_post": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "topic_key": {"type": "string"},
    },
    "required": ["title", "category", "main_game", "lead", "tldr",
                 "sections", "conclusion", "x_post", "hashtags", "topic_key"],
    "additionalProperties": False,
}


def _build_steam_maps(collected: dict) -> tuple[dict, dict]:
    """収集済みSteamデータから name->appid と appid->discount_percent を作る。"""
    name_to_appid: dict[str, int] = {}
    appid_discount: dict[int, int] = {}
    for r in collected.get("steam_players", []) or []:
        if r.get("appid") and r.get("name"):
            name_to_appid[r["name"]] = r["appid"]
    feat = collected.get("steam_featured") or {}
    for cat_items in feat.values():
        for it in cat_items or []:
            if it.get("appid") and it.get("name"):
                name_to_appid[it["name"]] = it["appid"]
                if it.get("discount_percent"):
                    appid_discount[int(it["appid"])] = int(it["discount_percent"])
    return name_to_appid, appid_discount


def _resolve_game(name: str, name_to_appid: dict, appid_discount: dict) -> dict:
    """ゲーム名 -> {name, image_url, discount_percent?, appid}。解決できなければ画像なし。"""
    out = {"name": name}
    if not name:
        return out
    try:
        appid = game_watch._resolve_steam_appid({"main_game": name}, name_to_appid)
    except Exception:
        appid = None
    if appid:
        out["appid"] = appid
        out["image_url"] = article_render.steam_image_url(appid)
        if appid_discount.get(appid):
            out["discount_percent"] = appid_discount[appid]
    return out


def _enrich(article: dict, collected: dict) -> tuple[str, list[str]]:
    """記事にSteam画像/割引を付与。戻り値: (hero画像URL, 割引付きゲーム名の一覧ログ)。"""
    name_to_appid, appid_discount = _build_steam_maps(collected)
    log = []

    # hero画像（主役ゲーム）
    hero_url = ""
    main = (article.get("main_game") or "").strip()
    if main:
        g = _resolve_game(main, name_to_appid, appid_discount)
        hero_url = g.get("image_url", "")
    article["hero_image_url"] = hero_url

    # 各セクションの購入ボックス
    for sec in article.get("sections", []):
        gname = (sec.get("game_name") or "").strip()
        if not gname:
            sec["buy"] = {}
            continue
        g = _resolve_game(gname, name_to_appid, appid_discount)
        sec["buy"] = g
        if g.get("discount_percent"):
            log.append(f"{gname} -{g['discount_percent']}%")
    return hero_url, log


def generate_article(collected: dict, recent: list[dict], api_key: str,
                     model: str = "claude-sonnet-5") -> dict:
    """Claudeに1本の記事を書かせる。structured outputsでJSON構造を強制。"""
    recent_titles = "\n".join(f"- {r['title']}" for r in recent) or "(まだありません)"
    user_prompt = USER_TEMPLATE.format(
        data_json=json.dumps(collected, ensure_ascii=False, indent=2),
        recent_titles=recent_titles,
        categories=" / ".join(config.ARTICLE_CATEGORIES),
    )
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    body = {
        "model": model,
        "max_tokens": 4000,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_prompt}],
        "thinking": {"type": "disabled"},
        "output_config": {"format": {"type": "json_schema", "schema": ARTICLE_SCHEMA}},
    }
    resp = requests.post(ANTHROPIC_API_URL, headers=headers, json=body, timeout=90)
    if resp.status_code != 200:
        raise RuntimeError(f"Anthropic API失敗 (HTTP {resp.status_code}): {resp.text}")
    result = resp.json()
    text = "\n".join(c["text"] for c in result.get("content", []) if c.get("type") == "text").strip()
    text = text.replace("```json", "").replace("```", "").strip()
    return json.loads(text)


def publish(article: dict, hero_url: str) -> dict:
    """記事HTMLを書き出し、DBに登録し、トップページの一覧を更新。戻り値: メタ情報。"""
    now = datetime.now()
    slug = article_render.slugify(now, seq=1)
    articles_dir = os.path.join(config.SITE_DIR, config.ARTICLES_SUBDIR)
    os.makedirs(articles_dir, exist_ok=True)

    # OGP用の正規URL（公開URLベースがあれば）。render_article がog:url/canonicalに使う。
    article["canonical_url"] = build_public_url(slug)

    # 記事HTML
    html_str = article_render.render_article(article)
    article_path = os.path.join(articles_dir, f"{slug}.html")
    with open(article_path, "w", encoding="utf-8") as f:
        f.write(html_str)

    # DB登録（抜粋はleadを流用）
    excerpt = (article.get("lead") or "")[:120]
    storage.save_article(
        config.HISTORY_DB, slug, article.get("title", ""), article.get("category", ""),
        article.get("topic_key", ""), excerpt, hero_url,
    )

    # トップページの記事一覧を最新12件で再生成
    index_path = os.path.join(config.SITE_DIR, "index.html")
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            index_html = f.read()
        arts = storage.list_articles(config.HISTORY_DB, limit=12)
        new_index = article_render.inject_articles(index_html, arts)
        if new_index != index_html:
            with open(index_path, "w", encoding="utf-8") as f:
                f.write(new_index)
    except FileNotFoundError:
        print("[WARN] site/index.html が見つからず、一覧更新はスキップしました。")

    return {"slug": slug, "path": article_path}


def build_public_url(slug: str) -> str:
    """公開URL（SITE_BASE_URLがあれば）。無ければ空（未公開＝Xにはまだ載せられない）。"""
    base = (config.SITE_BASE_URL or "").strip()
    if not base:
        return ""
    return f"{base.rstrip('/')}/{config.ARTICLES_SUBDIR}/{slug}.html"


def main():
    if not config.ANTHROPIC_API_KEY:
        print("[ERROR] ANTHROPIC_API_KEY が未設定です。.env を確認してください。")
        return

    print("=== 記事生成: データ収集開始 ===")
    collected = game_watch.collect_all()
    if not game_watch.has_signal(collected):
        print("収集データが無かったため、記事生成を中止しました。")
        return

    recent = storage.recent_article_topics(config.HISTORY_DB, days=21)
    print(f"直近公開済み: {len(recent)}件（重複回避）")

    print("=== Claudeで記事を執筆中 ===")
    try:
        article = generate_article(collected, recent, config.ANTHROPIC_API_KEY)
    except Exception as e:
        print(f"[ERROR] 記事生成に失敗しました: {e}")
        return

    hero_url, disc_log = _enrich(article, collected)
    meta = publish(article, hero_url)
    public_url = build_public_url(meta["slug"])
    x_post = article_render.build_x_post(article, public_url)

    print(f"=== 公開: {meta['path']} ===")
    print(f"タイトル: {article.get('title','')}")
    print(f"カテゴリ: {article.get('category','')} / セクション{len(article.get('sections',[]))}個"
          + (f" / セール検知: {', '.join(disc_log)}" if disc_log else ""))
    print("\n--- Xポスト文面 ---")
    print(x_post)
    if not public_url:
        print(f"\n[INFO] 公開URL未設定（config.SITE_BASE_URL が空）。ローカル確認用パス:")
        print(f"  file:///{os.path.abspath(meta['path']).replace(os.sep, '/')}")

    # メール通知（設定があれば）
    if config.email_enabled():
        try:
            import emailer
            local_path = os.path.abspath(meta["path"])
            emailer.send_article_email(
                article, x_post, public_url, local_path, hero_url,
                config.SMTP_HOST, config.SMTP_PORT,
                config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD,
                config.GMAIL_ADDRESS, config.EMAIL_TO,
            )
            print(f"\n=== メール送信済み: {config.EMAIL_TO} ===")
        except Exception as e:
            print(f"[WARN] メール送信失敗: {e}")
    else:
        print("\n[INFO] メール未設定（.envにGMAIL_ADDRESS/GMAIL_APP_PASSWORDを入れると送信します）")


if __name__ == "__main__":
    main()
