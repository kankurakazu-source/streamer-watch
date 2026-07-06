"""
main.py
-------
データ収集 → 履歴保存 → 急増検知 → AI下書き生成 → ファイル出力
までを一括で実行するオーケストレーションスクリプト。

想定運用:
- cronで1日3〜4回程度実行(例: 9時, 13時, 20時, 24時)
- 実行後、output/ 配下に生成された下書きファイルを確認し、
  良いものだけ手動でXに投稿する

実行例:
    python main.py
"""

import json
import os
import sys
from datetime import datetime, timezone

# Windowsのコンソール(cp932)でも日本語ログが文字化けしないよう標準出力をUTF-8化
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import config
from collectors import twitch_collector, youtube_collector, x_mentions_collector
import storage
import analyzer


def collect_all() -> dict:
    """全配信者の現在データを収集し、辞書にまとめて返す"""
    storage.init_db(config.HISTORY_DB)

    twitch_logins = [s["twitch_login"] for s in config.STREAMERS if s["twitch_login"]]
    twitch_status = {}
    if twitch_logins and config.TWITCH_CLIENT_ID and config.TWITCH_CLIENT_SECRET:
        twitch_status = twitch_collector.fetch_stream_status(
            twitch_logins, config.TWITCH_CLIENT_ID, config.TWITCH_CLIENT_SECRET
        )
    else:
        print("[WARN] Twitch API未設定、またはログイン名未登録のためスキップ")

    collected = {}

    for streamer in config.STREAMERS:
        name = streamer["display_name"]
        entry = {"twitch": None, "youtube": None, "x_mentions": None, "growth_rate": None}

        # --- Twitch ---
        login = streamer["twitch_login"]
        if login and login in twitch_status:
            status = twitch_status[login]
            entry["twitch"] = status
            storage.save_snapshot(config.HISTORY_DB, name, "twitch", status)

            if status.get("is_live"):
                prev = storage.get_previous_snapshot(config.HISTORY_DB, name, "twitch")
                if prev:
                    entry["growth_rate"] = storage.calc_growth_rate(
                        status.get("viewer_count"), prev.get("viewer_count")
                    )

        # --- YouTube ---
        # UCチャンネルIDが直書きされていればそれを、無ければ@ハンドルを使う
        # （@ハンドルは youtube_collector 側でUC IDに自動解決される）
        yt_target = streamer.get("youtube_channel_id") or streamer.get("youtube_handle")
        if yt_target and config.YOUTUBE_API_KEY:
            yt_status = youtube_collector.fetch_live_status(yt_target, config.YOUTUBE_API_KEY)
            entry["youtube"] = yt_status
            if yt_status.get("is_live"):
                storage.save_snapshot(config.HISTORY_DB, name, "youtube", yt_status)

        # --- X mentions（契約していない場合は None が入る） ---
        if config.X_BEARER_TOKEN:
            entry["x_mentions"] = x_mentions_collector.fetch_mention_summary(
                name, config.X_BEARER_TOKEN
            )

        collected[name] = entry

    return collected


def filter_notable(collected: dict) -> dict:
    """
    「配信中」「急増率が大きい」「言及数が多い」など、
    ネタになりそうなものだけに絞り込んでAIに渡す(トークン節約＆精度向上)
    """
    notable = {}
    for name, entry in collected.items():
        is_live = (entry["twitch"] or {}).get("is_live") or (entry["youtube"] or {}).get("is_live")
        growth = entry.get("growth_rate")
        mention_count = (entry.get("x_mentions") or {}).get("mention_count")

        if is_live or (growth and abs(growth) > 30) or (mention_count and mention_count > 20):
            notable[name] = entry

    return notable


def main():
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)

    print("=== データ収集開始 ===")
    collected = collect_all()

    notable = filter_notable(collected)
    print(f"注目対象: {list(notable.keys()) if notable else '(該当なし)'}")

    result = {"generated_at": datetime.now(timezone.utc).isoformat(), "drafts": []}

    if notable and config.ANTHROPIC_API_KEY:
        print("=== AI下書き生成中 ===")
        drafts = analyzer.generate_draft_posts(notable, config.ANTHROPIC_API_KEY)
        result["drafts"] = drafts
    elif not notable:
        print("注目データが無かったため、下書き生成はスキップしました。")
    else:
        print("[WARN] ANTHROPIC_API_KEY未設定のため下書き生成をスキップしました。")

    # 生データも一緒に残す(見直し・デバッグ用)
    result["raw_collected"] = collected

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    out_path = os.path.join(config.OUTPUT_DIR, f"draft_{timestamp}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"=== 完了: {out_path} に出力しました ===")


if __name__ == "__main__":
    main()
