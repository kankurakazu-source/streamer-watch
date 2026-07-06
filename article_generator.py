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
import image_card
import trend_detector
from collectors import steam_collector

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"

SYSTEM_PROMPT = """\
あなたは国内外のゲームトレンドをデータで解説する、アフィリエイト型ゲーム情報サイトの
編集ライターです。denfaminicogamer や FPS_G33KS のように、数字と一次情報に基づいた
読み応えのある記事を書きます。読者が「これは買い/要チェック」と判断できる実用記事が目標です。

【集客戦略＝トレンド・ハイジャック】
このメディアの生命線は「話題の瞬発力」です。新作の発売/配信開始、大型アップデート（新マップ・
新エージェント・パッチノート公開）、人気ゲーミングデバイスの予約開始/発売やGPUのベンチマーク、
プロ大会（eスポーツ）の結果——こうした「今まさに検索されているイベント」に乗って、
『どこよりも早いまとめ』を出すのが狙いです。速さと網羅性そのものが価値になります。
提供される「今狙うべきイベント」から最も鮮度と関心が高いものを主役に選んでください。

【反応（口コミ）の扱い＝重要】
「ユーザーのリアルな反応」は、Steam同接の前日比、Twitch視聴者数、YouTube急上昇といった
"測定できる数字"だけを根拠に要約します。実在しないSNSコメントや感想・引用を創作しては
いけません（例「盛り上がっている」ではなく「前日同接比+○%と数字が伸びている」と書く）。

記事の狙い:
- 提供データ／検出イベントから、今いちばん検索・話題になっているトピックを1つ選ぶ。
- 同時接続数の絶対値だけでなく「勢い（前日比の伸び）」も重視し、まだ無名でも伸びている作品を拾う。
- セール対象タイトルは購入導線（buy）を付け、読者の行動につなげる。

厳守する制約:
1. 本文は提供データ・公開情報に基づく事実のみ。数字は可能な範囲で入れるが、無い数字を作らない。
2. 価格（円）は書かない。割引率はデータにある discount_percent のみ触れてよい（無ければ触れない）。
3. 未確認の噂・リーク・発売日・炎上を断定しない。「〜のもよう」「〜との情報も」等でヘッジする。
4. 開発元や人物の発言、ユーザーの感想・口コミを捏造・誇張しない。反応は測定可能な数字のみ。
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

■ 今狙うべきイベント（トレンド・ハイジャック候補。スコア順。速報性・関心の高い順）:
{hot_events}

直近に公開済みの記事タイトル（これらと重複しないトピックを選ぶこと）:
{recent_titles}

上記イベントの中から、いま最も検索・話題になっていて記事価値が高いものを【1つだけ】選び、
『どこよりも早いまとめ』となる1本の記事を書いてください。明確なイベントが無い場合のみ、
データから通常の考察記事を書いてかまいません。読者が得をする実用記事にします。

カテゴリは次から1つ選ぶ: {categories}

記事の構成:
- title: 具体的で内容が伝わる見出し（誇張しすぎない。速報なら「【速報】」等を付けてよい）
- category: 上記から1つ
- event_type: 乗ったイベントの種別。次から1つ: 新作・アプデ / デバイス / eスポーツ / 通常
- is_breaking: 速報性が高い（発売当日・アプデ直後・大会直後など鮮度勝負）なら true、じっくり考察なら false
- main_game: 記事の主役となる単一ゲームの正式名称（Steam画像検索用。日本語名可。複数まとめで主役が定まらなければ空）。
    ※デバイス記事(event_type=デバイス)では、この欄はSteam画像検索専用なので基本は空でよい（製品はSteamに無いため）。
- lead: リード文（2〜3文。何が起きていて、なぜ今読む価値があるか）
- tldr: 結論を一言で（迷ったら何を見る/買うべきか）
- sections: 3〜5個。各 {{heading, body, game_name}}。
    heading: 小見出し（タイトル名を含めてよい）
    body: 2〜4文の本文（数字や根拠を入れる。段落は改行で区切ってよい）
    game_name: そのセクションで購入導線(buy)を出す対象の正式名称。ゲームならタイトル名、
    デバイス記事なら製品名（例: Lamzu Atlantis / RTX 5080 / 〇〇ゲーミングモニター）を入れてよい。
    購入導線が不要なら空文字。※デバイスはSteam画像が付かないが、Amazon/楽天の検索リンクは製品名から生成される。
- conclusion: まとめ（2〜3文）
- x_main: 【親ポスト】記事リンクを貼らないX投稿本文。ここでインプレッションを稼ぐフック。
    「思わず手が止まる最新情報・数字・比較の要点」をテキストだけで完結させる（画像は別途こちらが添付）。
    日本語で約100字以内（全角2換算で230程度まで）。末尾に関連ハッシュタグを1〜2個入れてよい。
    URLは絶対に含めない（Xはリンク付き投稿の表示を大きく下げるため）。煽りすぎない。
- x_reply: 【リプ用の誘導文】親ポストのリプ欄に貼る一言。記事へ誘導するCTA。
    例「詳しいスペック比較はこちらの記事にまとめています👇」。URLは書かない（こちらで付ける）。20〜40字。
- hashtags: 0〜2個（#は付けず語だけ。トレンドに乗る語を選ぶ。例: VALORANT / Steamセール）
- topic_key: 重複検知用のキー。主役ゲーム名 or トピックを短い日本語で（例「Steamサマーセール」）
"""

ARTICLE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "category": {"type": "string", "enum": config.ARTICLE_CATEGORIES},
        "event_type": {"type": "string", "enum": ["新作・アプデ", "デバイス", "eスポーツ", "通常"]},
        "is_breaking": {"type": "boolean"},
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
        "x_main": {"type": "string"},
        "x_reply": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "topic_key": {"type": "string"},
    },
    "required": ["title", "category", "event_type", "is_breaking", "main_game", "lead", "tldr",
                 "sections", "conclusion", "x_main", "x_reply", "hashtags", "topic_key"],
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
    """
    記事にSteam画像/割引を付与。hero と各セクションのバナー画像には、同じ画像の使い回しを
    避けるため「まだ使っていない別の画像（スクリーンショット等）」を順に割り当てる。
    戻り値: (hero画像URL, 割引付きゲーム名の一覧ログ)。
    """
    name_to_appid, appid_discount = _build_steam_maps(collected)
    log = []

    pools: dict[int, list[str]] = {}   # appid -> 画像URL群（キャッシュ）
    used: set[str] = set()             # 記事内で既に使った画像

    def pick_image(appid, fallback: str) -> str:
        """このappidの画像から、記事内でまだ使っていないものを1枚選ぶ。"""
        if not appid:
            return fallback
        if appid not in pools:
            try:
                pools[appid] = steam_collector.fetch_image_urls(appid)
            except Exception:
                pools[appid] = []
        for u in pools[appid]:
            if u not in used:
                used.add(u)
                return u
        # 全部使い切った/取得不可なら、先頭 or ヘッダーへフォールバック
        return (pools[appid][0] if pools[appid] else fallback)

    # hero画像（主役ゲーム）
    hero_url = ""
    main = (article.get("main_game") or "").strip()
    if main:
        g = _resolve_game(main, name_to_appid, appid_discount)
        article["main_appid"] = g.get("appid")  # 親ポスト添付画像の生成に使う
        hero_url = pick_image(g.get("appid"), g.get("image_url", ""))
    article["hero_image_url"] = hero_url

    # 各セクション：購入ボックス＋（使い回さない）バナー画像
    for sec in article.get("sections", []):
        gname = (sec.get("game_name") or "").strip()
        if not gname:
            sec["buy"] = {}
            sec["image_url"] = ""
            continue
        g = _resolve_game(gname, name_to_appid, appid_discount)
        sec["buy"] = g
        sec["image_url"] = pick_image(g.get("appid"), g.get("image_url", ""))
        if g.get("discount_percent"):
            log.append(f"{gname} -{g['discount_percent']}%")
    return hero_url, log


def generate_article(collected: dict, recent: list[dict], api_key: str,
                     hot_events_text: str = "", model: str = "claude-sonnet-5") -> dict:
    """Claudeに1本の記事を書かせる。structured outputsでJSON構造を強制。"""
    recent_titles = "\n".join(f"- {r['title']}" for r in recent) or "(まだありません)"
    user_prompt = USER_TEMPLATE.format(
        data_json=json.dumps(collected, ensure_ascii=False, indent=2),
        hot_events=hot_events_text or "（検出なし）",
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
        is_breaking=bool(article.get("is_breaking")),
        event_type=article.get("event_type", ""),
    )

    # トップページの記事一覧を最新12件で再生成
    index_path = os.path.join(config.SITE_DIR, "index.html")
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            index_html = f.read()
        arts = storage.list_articles(config.HISTORY_DB, limit=12)
        new_index = article_render.inject_homepage(index_html, arts)
        if new_index != index_html:
            with open(index_path, "w", encoding="utf-8") as f:
                f.write(new_index)
    except FileNotFoundError:
        print("[WARN] site/index.html が見つからず、一覧更新はスキップしました。")

    return {"slug": slug, "path": article_path}


def build_post_image(article: dict, slug: str) -> str | None:
    """
    親ポストにそのまま添付できる画像(PNG)を生成して返す。
    - Steam公式アートが取れれば、それを敷いた「連想カード」(種別バッジ付き)。
    - 取れなければ、タイトル＋結論のテキストカード（著作権リスクなし）。
    失敗時は None。
    """
    # メール添付専用（デプロイ不要）なので output/ 配下に出す
    card_dir = os.path.join(config.OUTPUT_DIR, "cards")
    out_path = os.path.join(card_dir, f"article_{slug}.png")
    ctype = "速報" if article.get("is_breaking") else "考察"
    appid = article.get("main_appid")

    try:
        if appid:
            # 記事ごとに絵柄が変わるよう、スクショ群から1枚を選んで使う（無ければ従来のヘッダー系）
            img_bytes = None
            urls = steam_collector.fetch_image_urls(appid)
            if urls:
                idx = abs(hash(slug)) % len(urls)
                try:
                    r = requests.get(urls[idx], timeout=10)
                    if r.status_code == 200 and len(r.content) > 3000:
                        img_bytes = r.content
                except requests.exceptions.RequestException:
                    img_bytes = None
            if not img_bytes:
                img_bytes = steam_collector.fetch_game_image_bytes(appid)
            if img_bytes:
                draft = {"type": ctype, "headline": article.get("title", "")}
                return image_card.render_art_card(img_bytes, draft, out_path, "画像: Steam")
        # フォールバック: タイトル＋結論のテキストカード
        draft = {
            "type": ctype,
            "headline": article.get("title", ""),
            "bullets": [b for b in [article.get("tldr", "")] if b],
        }
        return image_card.render_card(draft, out_path)
    except Exception as e:
        print(f"[WARN] 親ポスト画像の生成に失敗: {e}")
        return None


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

    # トレンド・ハイジャック: 狙うべきイベントを検出してプロンプトに渡す
    detection = trend_detector.detect(collected)
    hot_text = trend_detector.format_for_prompt(detection)
    print(f"=== 検出イベント: {sum(detection['counts'].values())}件"
          f"（速報級={'あり' if detection['has_breaking'] else 'なし'}） ===")
    for e in detection["hot_events"][:5]:
        print(f"  [{e['event_type']}] {e['headline'][:48]}  (score={e['score']})")

    print("=== Claudeで記事を執筆中 ===")
    try:
        article = generate_article(collected, recent, config.ANTHROPIC_API_KEY, hot_events_text=hot_text)
    except Exception as e:
        print(f"[ERROR] 記事生成に失敗しました: {e}")
        return

    hero_url, disc_log = _enrich(article, collected)
    meta = publish(article, hero_url)
    public_url = build_public_url(meta["slug"])
    thread = article_render.build_x_thread(article, public_url)
    post_image = build_post_image(article, meta["slug"])  # 親ポストに添付するPNG

    print(f"=== 公開: {meta['path']} ===")
    print(f"タイトル: {article.get('title','')}")
    print(f"種別: {article.get('event_type','-')} / 速報={article.get('is_breaking')} "
          f"/ カテゴリ: {article.get('category','')} / セクション{len(article.get('sections',[]))}個"
          + (f" / セール検知: {', '.join(disc_log)}" if disc_log else ""))
    print("\n--- Xポスト（2ステップ）---")
    print(f"[親ポスト・画像付き/リンクなし] ({thread['main_weight']}/280)")
    print(thread["main"])
    print(f"[親ポスト添付画像] {post_image or '(生成なし)'}")
    print(f"\n[リプ・記事リンク] ({thread['reply_weight']}/280)")
    print(thread["reply"])
    if not public_url:
        print(f"\n[INFO] 公開URL未設定（config.SITE_BASE_URL が空）。ローカル確認用パス:")
        print(f"  file:///{os.path.abspath(meta['path']).replace(os.sep, '/')}")

    # メール通知（設定があれば）
    if config.email_enabled():
        try:
            import emailer
            local_path = os.path.abspath(meta["path"])
            emailer.send_article_email(
                article, thread, public_url, local_path, hero_url, post_image,
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
