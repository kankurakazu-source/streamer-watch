"""
trend_detector.py
-----------------
収集済みデータ(collected)から「トレンド・ハイジャック」で狙うべきイベントを検出・ランク付けする。
ネットワークアクセスはしない（純粋な分類・スコアリング）。

狙うイベント種別:
- 新作・アプデ : 新作ゲームの発売/配信開始、大型アップデート/パッチ、新マップ・新エージェント実装 等
- デバイス     : 人気ゲーミングデバイスの予約開始/発売、新型GPU/CPUの発表・ベンチマーク 等
- eスポーツ    : プロ大会（決勝・優勝・世界大会）等

やること:
- news(RSS) の見出し/要約をキーワードで分類し、鮮度(公開からの経過時間)と数字シグナルで加点。
- Steam新作/セール、同接急増(steam_spikes)、Twitch/YouTubeの伸びも「反応の強さ」として加点材料に。
- 上位イベントを article_generator のプロンプトに「今狙うべきイベント」として渡す。

安全方針: ここでは事実の分類・並び替えのみ。感想や口コミの創作はしない。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

# ---- 種別ごとの検出キーワード（見出し・要約に対する部分一致。大文字小文字は無視） ----
EVENT_KEYWORDS: dict[str, list[str]] = {
    "新作・アプデ": [
        "発売", "配信開始", "リリース", "ローンチ", "早期アクセス", "アーリーアクセス",
        "アップデート", "アプデ", "パッチ", "パッチノート", "大型アップデート",
        "新マップ", "新エージェント", "新キャラ", "新モード", "新シーズン", "シーズン",
        "実装", "追加", "無料配布", "無料開放", "体験版", "ベータ", "先行",
        "release", "launch", "early access", "update", "patch", "season", "dlc",
    ],
    "デバイス": [
        "マウス", "キーボード", "モニター", "ディスプレイ", "ヘッドセット", "ゲーミングチェア",
        "コントローラー", "gpu", "グラボ", "グラフィックボード", "rtx", "radeon", "geforce",
        "cpu", "ryzen", "ssd", "予約", "予約開始", "発売日", "ベンチマーク", "新型",
        "lamzu", "logicool", "razer", "zowie", "pulsar", "vaxee", "finalmouse",
        "mouse", "keyboard", "monitor", "headset", "benchmark", "preorder",
    ],
    "eスポーツ": [
        "大会", "決勝", "準決勝", "優勝", "王者", "世界大会", "国際大会", "出場", "予選",
        "eスポーツ", "esports", "e-sports", "grand final", "champion", "playoffs",
        "msi", "worlds", "major", "vct", "rage", "iem", "the international", "ti",
    ],
}

# 数字シグナル（本文に絡めやすい「反応」）を強く見せる語
_HOT_HINT = ("急増", "急上昇", "過去最高", "記録", "同接", "話題", "殺到")

_URL_RE = re.compile(r"https?://\S+")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _hours_since(iso: str | None) -> float | None:
    """ISO文字列(UTC)から現在までの経過時間(時間)。不明ならNone。"""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (_now_utc() - dt).total_seconds() / 3600.0
    except (ValueError, TypeError):
        return None


def _freshness_bonus(hours: float | None) -> int:
    """鮮度ボーナス。速報性を重視するため、新しいほど大きく加点。"""
    if hours is None:
        return 0
    if hours <= 3:
        return 5
    if hours <= 6:
        return 4
    if hours <= 12:
        return 2
    if hours <= 24:
        return 1
    return 0


def classify_text(text: str) -> tuple[str | None, list[str]]:
    """
    テキストをイベント種別に分類する。最もヒット数の多い種別を返す。
    戻り値: (種別 or None, ヒットしたキーワード一覧)。
    """
    low = (text or "").lower()
    best_type: str | None = None
    best_hits: list[str] = []
    for etype, kws in EVENT_KEYWORDS.items():
        hits = [kw for kw in kws if kw.lower() in low]
        if len(hits) > len(best_hits):
            best_type, best_hits = etype, hits
    return best_type, best_hits


def _news_events(collected: dict) -> list[dict]:
    events = []
    for n in collected.get("news", []) or []:
        text = f"{n.get('title','')} {n.get('summary','')}"
        etype, hits = classify_text(text)
        if not etype:
            continue
        hours = _hours_since(n.get("published"))
        score = 2 * len(hits) + _freshness_bonus(hours)
        if any(h in text for h in _HOT_HINT):
            score += 1
        events.append({
            "event_type": etype,
            "headline": n.get("title", ""),
            "summary": n.get("summary", ""),
            "source": n.get("source", ""),
            "url": n.get("link", ""),
            "published": n.get("published"),
            "hours_since": round(hours, 1) if hours is not None else None,
            "matched": hits,
            "score": score,
            "origin": "news",
        })
    return events


def _steam_events(collected: dict) -> list[dict]:
    events = []
    feat = collected.get("steam_featured") or {}

    # 新作
    for it in (feat.get("new_releases") or [])[:8]:
        if not it.get("name"):
            continue
        events.append({
            "event_type": "新作・アプデ",
            "headline": f"{it['name']} がSteam新作に登場",
            "summary": "",
            "source": "Steam",
            "url": it.get("store_url", ""),
            "published": None,
            "hours_since": None,
            "matched": ["新作"],
            "score": 3,
            "origin": "steam_new",
            "main_game": it["name"],
        })

    # 同接急増（測定可能な「反応」＝最もバズる型）
    for r in (collected.get("steam_spikes") or [])[:6]:
        pct = r.get("prev_day_players_pct")
        pct_txt = f"（前日同接比+{int(pct)}%）" if isinstance(pct, (int, float)) else ""
        events.append({
            "event_type": "新作・アプデ",
            "headline": f"{r.get('name','')} の同接が急増{pct_txt}",
            "summary": "",
            "source": "Steam",
            "url": f"https://store.steampowered.com/app/{r.get('appid')}/" if r.get("appid") else "",
            "published": None,
            "hours_since": None,
            "matched": ["同接急増"],
            "score": 4 + (2 if isinstance(pct, (int, float)) and pct >= 100 else 0),
            "origin": "steam_spike",
            "main_game": r.get("name", ""),
        })
    return events


def detect(collected: dict, top_n: int = 6) -> dict:
    """
    collected から狙うべきイベントを検出し、スコア順に返す。
    戻り値: {
      "hot_events": [上位イベント...],
      "has_breaking": bool,   # 直近12h以内の鮮度の高いイベントがあるか
      "counts": {種別: 件数},
    }
    """
    events = _news_events(collected) + _steam_events(collected)
    events.sort(key=lambda e: e["score"], reverse=True)
    hot = events[:top_n]

    has_breaking = any(
        (e.get("hours_since") is not None and e["hours_since"] <= 12) or e.get("origin") == "steam_spike"
        for e in hot
    )
    counts: dict[str, int] = {}
    for e in events:
        counts[e["event_type"]] = counts.get(e["event_type"], 0) + 1

    return {"hot_events": hot, "has_breaking": has_breaking, "counts": counts}


def format_for_prompt(detection: dict) -> str:
    """検出結果を、記事生成プロンプトに埋め込む短いテキストに整形する。"""
    hot = detection.get("hot_events", [])
    if not hot:
        return "（今回、明確なトレンド・イベントは検出されませんでした。通常の考察記事を書いてよい）"
    lines = []
    for i, e in enumerate(hot, 1):
        age = f"{e['hours_since']}h前" if e.get("hours_since") is not None else "時刻不明"
        url = e.get("url") or "(URLなし)"
        lines.append(
            f"{i}. [{e['event_type']}] {e['headline']}"
            f"  <{e['source']} / {age} / score={e['score']}>\n   根拠URL: {url}"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    import json
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    src = sys.argv[1] if len(sys.argv) > 1 else None
    if src:
        collected = json.load(open(src, encoding="utf-8"))
        collected = collected.get("raw_collected", collected)
    else:
        collected = {"news": [
            {"title": "Apex Legends 新シーズン開幕、新マップ実装", "summary": "", "source": "4Gamer",
             "link": "https://example.com/a", "published": _now_utc().isoformat()},
            {"title": "Lamzuの新型ワイヤレスマウスが国内予約開始", "summary": "", "source": "GameWatch",
             "link": "https://example.com/b", "published": _now_utc().isoformat()},
        ]}
    det = detect(collected)
    print(f"has_breaking={det['has_breaking']} counts={det['counts']}")
    print(format_for_prompt(det))
