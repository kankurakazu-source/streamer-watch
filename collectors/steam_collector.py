"""
steam_collector.py
------------------
Steamの公開エンドポイント（APIキー不要）から、硬い数字ネタを収集する。
- 現在の同時接続プレイ人数（GetNumberOfCurrentPlayers）
- 売上上位・新作・セール（storefront の featuredcategories）

同接は storage の game_snapshots に保存して前回比の急増検知に使う想定。
"""

import requests

STEAM_PLAYERS_URL = "https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/"
STEAM_FEATURED_URL = "https://store.steampowered.com/api/featuredcategories/"
STEAM_SEARCH_URL = "https://store.steampowered.com/api/storesearch/"
STEAM_CDN = "https://cdn.cloudflare.steamstatic.com/steam/apps"


def search_game(term: str, cc: str = "jp", lang: str = "japanese") -> dict | None:
    """
    Steamストア検索で、ゲーム名から先頭ヒットの {appid, name} を返す。
    見つからない/エラー時は None。
    """
    if not term:
        return None
    try:
        resp = requests.get(
            STEAM_SEARCH_URL, params={"term": term, "cc": cc, "l": lang}, timeout=10
        )
        resp.raise_for_status()
    except requests.exceptions.RequestException:
        return None

    items = resp.json().get("items", [])
    if not items:
        return None
    top = items[0]
    return {"appid": top.get("id"), "name": top.get("name", "")}


def fetch_game_image_bytes(appid: int) -> bytes | None:
    """
    Steam公式のゲーム画像を取得する。高解像度から順に試す。
    取得できなければ None。
    """
    if not appid:
        return None
    for suffix in ("library_hero.jpg", "capsule_616x353.jpg", "header.jpg"):
        url = f"{STEAM_CDN}/{appid}/{suffix}"
        try:
            r = requests.get(url, timeout=10)
        except requests.exceptions.RequestException:
            continue
        # 存在しない画像はプレースホルダ(小サイズ)やエラーを返すことがある
        if r.status_code == 200 and r.content and len(r.content) > 3000:
            return r.content
    return None


def fetch_player_count(appid: int) -> int | None:
    """1タイトルの現在の同接プレイ人数を返す。取得不可なら None。"""
    try:
        resp = requests.get(STEAM_PLAYERS_URL, params={"appid": appid}, timeout=10)
        resp.raise_for_status()
    except requests.exceptions.RequestException:
        return None

    data = resp.json().get("response", {})
    if data.get("result") == 1 and "player_count" in data:
        return int(data["player_count"])
    return None


def fetch_player_counts(appid_names: dict) -> list[dict]:
    """
    {appid: name} の各タイトルについて同接を取得する。
    戻り値: [{"appid": int, "name": str, "player_count": int|None}, ...]（同接降順）
    """
    rows = []
    for appid, name in appid_names.items():
        rows.append({"appid": int(appid), "name": name, "player_count": fetch_player_count(appid)})
    rows.sort(key=lambda r: (r["player_count"] or -1), reverse=True)
    return rows


def _parse_items(items: list) -> list[dict]:
    # final_price は単位が紛らわしく（円/最小通貨単位）AIが桁を誤りやすいので渡さない。
    # 確実な discount_percent（○%オフ）だけを使う。
    out = []
    for it in items or []:
        appid = it.get("id")
        out.append(
            {
                "appid": appid,
                "name": it.get("name"),
                "discount_percent": it.get("discount_percent") or 0,
                "store_url": f"https://store.steampowered.com/app/{appid}/" if appid else None,
            }
        )
    return out


def fetch_featured(cc: str = "jp", lang: str = "japanese") -> dict:
    """
    売上上位・新作・セールをまとめて取得する。
    戻り値: {"top_sellers": [...], "new_releases": [...], "specials": [...]}
    各要素は {appid, name, discount_percent, final_price}
    """
    try:
        resp = requests.get(STEAM_FEATURED_URL, params={"cc": cc, "l": lang}, timeout=10)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Steam featured取得に失敗しました: {e}") from e

    data = resp.json()
    return {
        "top_sellers": _parse_items(data.get("top_sellers", {}).get("items", []))[:12],
        "new_releases": _parse_items(data.get("new_releases", {}).get("items", []))[:12],
        "specials": _parse_items(data.get("specials", {}).get("items", []))[:12],
    }


if __name__ == "__main__":
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    feat = fetch_featured()
    print("--- Steam 売上上位 ---")
    for r in feat["top_sellers"][:8]:
        disc = f" -{r['discount_percent']}%" if r["discount_percent"] else ""
        print(f"  {r['name']}{disc}")

    print("\n--- 同接（主要タイトル例） ---")
    for r in fetch_player_counts({730: "Counter-Strike 2", 570: "Dota 2", 1245620: "ELDEN RING"}):
        print(f"  {r['player_count']:>9}  {r['name']}")
