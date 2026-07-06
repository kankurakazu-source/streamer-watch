"""
game_watch.py
-------------
新方針「話題のゲーム情報を収集して定期発信」のオーケストレーション（フェーズ1）。

現在のソース:
- Twitch: いま見られているゲームの視聴者数集計（配信界隈のホットなゲーム）
- YouTube: 急上昇・ゲームカテゴリ（日本＋米国）

収集 → AI考察/速報下書き生成 → ファイル出力 → draft_post をチャット表示用に標準出力。
※ フェーズ2でSteam(同接・ランキング)、フェーズ3で国内外RSSを追加予定。
※ 投稿は行わない（下書き生成まで。人間レビュー→手動投稿）。

使い方:
    .venv\\Scripts\\python.exe game_watch.py
"""

import glob
import json
import os
import re
import sys
from datetime import datetime, timezone

# Windowsコンソール(cp932)でも日本語が文字化けしないよう標準出力をUTF-8化
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import config
import emailer
import game_analyzer
import image_card
import review_sheet
import storage
from collectors import (
    twitch_games_collector,
    youtube_trending_collector,
    steam_collector,
    rss_collector,
)


def collect_all() -> dict:
    """各ソースからゲーム関連データを収集して1つの辞書にまとめる。"""
    storage.init_db(config.HISTORY_DB)
    collected: dict = {"collected_at": datetime.now(timezone.utc).isoformat()}

    # --- Twitch: ホットなゲーム（視聴者数を保存し前回比の急増を算出） ---
    if config.TWITCH_CLIENT_ID and config.TWITCH_CLIENT_SECRET:
        try:
            games = twitch_games_collector.fetch_hot_games(
                config.TWITCH_CLIENT_ID, config.TWITCH_CLIENT_SECRET, pages=config.TWITCH_TOP_PAGES
            )
            rows = twitch_games_collector.top_games(games, limit=12)
            for r in rows:
                storage.save_game_metric(
                    config.HISTORY_DB, "twitch", r["game"], r["game"], "viewers", r["viewers"]
                )
                prev = storage.get_prev_day_metric(config.HISTORY_DB, "twitch", r["game"], "viewers")
                r["prev_day_viewers_pct"] = storage.calc_growth_rate(r["viewers"], prev)
            collected["twitch_hot_games"] = rows
        except Exception as e:
            print(f"[WARN] Twitch人気ゲーム取得失敗: {e}")
            collected["twitch_hot_games"] = []
    else:
        print("[WARN] Twitch API未設定のためスキップ")
        collected["twitch_hot_games"] = []

    # --- YouTube: 急上昇ゲーム（日本＋米国） ---
    if config.YOUTUBE_API_KEY:
        collected["youtube_trending"] = youtube_trending_collector.fetch_multi_region(
            config.YOUTUBE_API_KEY, config.YOUTUBE_TREND_REGIONS, max_results=20
        )
    else:
        print("[WARN] YouTube API未設定のためスキップ")
        collected["youtube_trending"] = []

    # --- Steam: 売上/新作/セール ＋ 同接（前回比の急増検知） ---
    try:
        collected["steam_featured"] = steam_collector.fetch_featured(config.STEAM_CC, config.STEAM_LANG)
    except Exception as e:
        print(f"[WARN] Steam featured取得失敗: {e}")
        collected["steam_featured"] = {}

    # ウォッチリスト＋売上上位のappidを対象に同接を取得
    targets = dict(config.STEAM_WATCHLIST)
    for item in collected.get("steam_featured", {}).get("top_sellers", []):
        if item.get("appid") and item.get("name"):
            targets[int(item["appid"])] = item["name"]

    players = steam_collector.fetch_player_counts(targets)
    spikes = []
    for r in players:
        if r["player_count"] is None:
            continue
        storage.save_game_metric(config.HISTORY_DB, "steam", r["appid"], r["name"], "player_count", r["player_count"])
        prev = storage.get_prev_day_metric(config.HISTORY_DB, "steam", r["appid"], "player_count")
        r["prev_day_players_pct"] = storage.calc_growth_rate(r["player_count"], prev)
        if r["prev_day_players_pct"] is not None and r["prev_day_players_pct"] >= config.STEAM_SPIKE_THRESHOLD:
            spikes.append(r)
    collected["steam_players"] = players
    collected["steam_spikes"] = spikes  # 同接が急増したタイトル（一番バズる型）

    # --- RSS: 国内外ゲームメディアの最新ニュース（速報・裏取り） ---
    try:
        collected["news"] = rss_collector.fetch_all(
            config.RSS_FEEDS,
            per_feed_limit=config.RSS_PER_FEED_LIMIT,
            recent_hours=config.RSS_RECENT_HOURS,
        )
    except Exception as e:
        print(f"[WARN] RSS取得失敗: {e}")
        collected["news"] = []

    return collected


def has_signal(collected: dict) -> bool:
    """AIに渡す価値のあるデータが1件でもあるか。"""
    return (
        bool(collected.get("twitch_hot_games"))
        or bool(collected.get("youtube_trending"))
        or bool(collected.get("steam_players"))
        or bool(collected.get("steam_featured"))
    )


def main():
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)

    print("=== ゲーム情報 収集開始 ===")
    collected = collect_all()
    feat = collected.get("steam_featured", {}) or {}
    print(
        f"Twitchホットゲーム: {len(collected.get('twitch_hot_games', []))}件 / "
        f"YouTube急上昇: {len(collected.get('youtube_trending', []))}件 / "
        f"Steam同接: {len(collected.get('steam_players', []))}件"
        f"（急増{len(collected.get('steam_spikes', []))}件） / "
        f"Steam売上上位: {len(feat.get('top_sellers', []))}件 / "
        f"ニュース: {len(collected.get('news', []))}件"
    )

    result = {"generated_at": datetime.now(timezone.utc).isoformat(), "drafts": []}

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")

    if has_signal(collected) and config.ANTHROPIC_API_KEY:
        print("=== AI下書き生成中（考察/速報ミックス）===")
        result["drafts"] = game_analyzer.generate_game_drafts(collected, config.ANTHROPIC_API_KEY)
        _finalize_drafts(result["drafts"], collected, timestamp)
    elif not has_signal(collected):
        print("収集データが無かったため、下書き生成はスキップしました。")
    else:
        print("[WARN] ANTHROPIC_API_KEY未設定のため下書き生成をスキップしました。")

    result["raw_collected"] = collected

    out_path = os.path.join(config.OUTPUT_DIR, f"game_draft_{timestamp}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # 人間レビュー用の1枚HTML（文面＋画像をセットで確認）
    review_path = os.path.join(config.OUTPUT_DIR, f"review_{timestamp}.html")
    try:
        review_sheet.build_review_sheet(result, review_path)
    except Exception as e:
        print(f"[WARN] レビューシート生成失敗: {e}")
        review_path = None

    print(f"=== 完了: {out_path} に出力しました ===")
    if review_path:
        print(f"=== レビューシート: {review_path} ===")

    # スマホ確認用にメール送信（設定があれば）
    if config.email_enabled():
        try:
            emailer.send_review_email(
                result, config.SMTP_HOST, config.SMTP_PORT,
                config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD,
                config.GMAIL_ADDRESS, config.EMAIL_TO,
            )
            print(f"=== メール送信済み: {config.EMAIL_TO} ===")
        except Exception as e:
            print(f"[WARN] メール送信失敗: {e}")
    else:
        print("[INFO] メール未設定（.envにGMAIL_ADDRESS/GMAIL_APP_PASSWORDを入れると送信します）")

    _print_drafts(result.get("drafts", []), out_path)


def _norm_name(s: str) -> str:
    return re.sub(r"[\s:：・\-‐－—()（）\[\]【】]", "", (s or "")).lower()


def _game_name_for_search(draft: dict) -> str:
    """下書きからSteam検索に使うゲーム名を得る。AIの main_game を最優先。"""
    mg = (draft.get("main_game") or "").strip()
    if mg:
        return mg
    # フォールバック: 見出しの【】内 or topic
    h = draft.get("headline", "") or ""
    m = re.search(r"【(.+?)】", h)
    base = (m.group(1) if m else draft.get("topic", "")) or ""
    base = re.split(r"[／/｜|・、,]", base)[0]
    for suf in ("セール", "まとめ", "新情報", "速報", "考察", "新作"):
        base = base.replace(suf, "")
    return base.strip()


def _name_match(query: str, result: str) -> bool:
    q, r = _norm_name(query), _norm_name(result)
    if len(q) < 2 or len(r) < 2:
        return False
    return q in r or r in q


def _resolve_steam_appid(draft: dict, collected_names: dict) -> int | None:
    """下書きのゲームをSteam appidに解決する。まず収集済みデータ、次にストア検索。"""
    name = _game_name_for_search(draft)
    if not name:
        return None
    # 1) 収集済みのSteamデータ内で一致
    for cn, ca in collected_names.items():
        if _name_match(name, cn):
            return ca
    # 2) Steamストア検索（名前が一致する場合のみ採用）
    hit = steam_collector.search_game(name, config.STEAM_CC, config.STEAM_LANG)
    if hit and hit.get("appid") and _name_match(name, hit.get("name", "")):
        return hit["appid"]
    return None


def _resolve_youtube(draft: dict, collected: dict) -> dict | None:
    """下書きを YouTube 急上昇の動画に対応づけ、サムネ用の項目を返す（連想画像用）。"""
    q = _norm_name(_game_name_for_search(draft)) or _norm_name(draft.get("topic", ""))
    if len(q) < 2:
        return None
    best = None
    for v in collected.get("youtube_trending", []) or []:
        if not v.get("thumbnail"):
            continue
        if q in _norm_name(v.get("title", "")):
            if best is None or (v.get("view_count") or 0) > (best.get("view_count") or 0):
                best = v
    return best


def _finalize_drafts(drafts: list, collected: dict, timestamp: str):
    """
    各下書きに『連想させる画像』を割り当て、本文を確定する。
    画像優先度: Steam公式アート → YouTubeサムネ → なし（本文に根拠リンク掲載）。
    画像がある投稿は本文にURLを入れず、無い投稿はURLを入れる（画像 or リンク）。
    """
    card_dir = os.path.join(config.OUTPUT_DIR, "cards")

    coll_names: dict[str, int] = {}
    for r in collected.get("steam_players", []) or []:
        if r.get("appid") and r.get("name"):
            coll_names[r["name"]] = r["appid"]
    for cat in (collected.get("steam_featured") or {}).values():
        for it in cat or []:
            if it.get("appid") and it.get("name"):
                coll_names[it["name"]] = it["appid"]

    for i, d in enumerate(drafts, 1):
        if not (isinstance(d, dict) and d.get("headline")):
            continue

        img_bytes, credit, source_type, url_fallback = None, None, None, ""

        # 1) Steam公式アート
        try:
            appid = _resolve_steam_appid(d, coll_names)
            if appid:
                img_bytes = steam_collector.fetch_game_image_bytes(appid)
                if img_bytes:
                    credit, source_type = "画像: Steam", "steam"
                    url_fallback = f"https://store.steampowered.com/app/{appid}/"
        except Exception as e:
            print(f"[WARN] Steam画像の解決に失敗（{i}件目）: {e}")

        # 2) YouTube由来の話題は画像を付けず、根拠リンク（動画URL）を本文に使う
        #    （配信者個人のサムネは使わない方針。Steam公式アートのみ画像化する）
        if not img_bytes:
            yt = _resolve_youtube(d, collected)
            if yt and yt.get("url"):
                url_fallback = yt["url"]

        # source_url: AIが選んだものを優先、無ければ画像元のURL
        if not (d.get("source_url") or "").strip():
            d["source_url"] = url_fallback

        # 画像を描画（あれば）
        if img_bytes:
            path = os.path.join(card_dir, f"game_card_{timestamp}_{i}.png")
            try:
                image_card.render_art_card(img_bytes, d, path, credit)
                d["image_path"] = path
                d["image_source"] = source_type
            except Exception as e:
                print(f"[WARN] 画像生成失敗（{i}件目）: {e}")

        # 本文確定：画像があればURLは省略（画像 or リンク）
        include_url = not bool(d.get("image_path"))
        d["draft_post"] = game_analyzer.build_post_text(d, include_url=include_url)
        d["char_weight"] = game_analyzer.weighted_len(d["draft_post"])


def _print_drafts(drafts: list, out_path: str):
    """draft_post をチャット表示しやすい形で標準出力する。"""
    print("\n===DRAFTS_START===")
    if not drafts:
        print("(今回は下書きなし：収集データが無かったか、AI生成がスキップされました)")
    else:
        for i, d in enumerate(drafts, 1):
            if "draft_post" in d:
                tag = d.get("type", "")
                topic = d.get("topic", "")
                cw = d.get("char_weight")
                cws = f" [{cw}/280]" if cw is not None else ""
                print(f"[{i}] ({tag}) {topic}{cws}")
                print(d["draft_post"])
                if d.get("image_path"):
                    src = {"steam": "Steam公式", "youtube": "YouTubeサムネ"}.get(d.get("image_source"), "画像")
                    print(f"[画像/{src}] {d['image_path']}")
                else:
                    print("[画像なし：本文に根拠リンク]")
                print()
            else:
                print(f"[{i}] {json.dumps(d, ensure_ascii=False)}")
                print()
    print("===DRAFTS_END===")
    print(f"file: {out_path}")


if __name__ == "__main__":
    main()
