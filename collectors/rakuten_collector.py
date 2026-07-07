"""
rakuten_collector.py
--------------------
楽天 新プラットフォーム(Rakuten Developers, 2026年新仕様)の「楽天市場 商品検索API」で、
製品名から実際の商品画像URLを取得する。デバイス記事などSteam画像が無い対象の画像ソース。

2026年新仕様のポイント:
- エンドポイント: https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20260401
- 認証: applicationId(UUID) + accessKey(pk_...)。accessKey はヘッダで送る。
- **Origin / Referer ヘッダ必須**（アプリの「Allowed websites」に登録したドメインと一致）。
- キーワードは1文字トークンがあると "keyword is not valid" になるため2文字未満を除去。
- app_id / access_key / referrer のいずれか未設定なら "" を返す（＝呼び出し側は画像なし）。
"""
import json
import re
import time
import urllib.parse
import urllib.request

_ENDPOINT = "https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20260401"

_last_call = [0.0]      # 直近呼び出し時刻（レート制限=約1req/sec 対策の簡易スロットル）
_MIN_INTERVAL = 1.2


def _clean_keyword(q: str) -> str:
    """空白区切りの各トークンから2文字未満(例 '4')を除く。楽天の keyword バリデーション対策。"""
    toks = [t for t in re.split(r"\s+", (q or "").strip()) if len(t) >= 2]
    return " ".join(toks)


def _upsize(url: str, px: int = 600) -> str:
    """楽天サムネURLの ?_ex=WxH をより大きいサイズに差し替える（無ければそのまま）。"""
    if not url:
        return ""
    if "_ex=" in url:
        return re.sub(r"_ex=\d+x\d+", f"_ex={px}x{px}", url)
    return url


def _origin_of(referrer: str) -> str:
    """Referer から Origin(scheme://host) を作る。"""
    try:
        p = urllib.parse.urlparse(referrer)
        if p.scheme and p.netloc:
            return f"{p.scheme}://{p.netloc}"
    except Exception:
        pass
    return referrer.rstrip("/")


def _throttle():
    dt = time.monotonic() - _last_call[0]
    if dt < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - dt)
    _last_call[0] = time.monotonic()


def search_image(query: str, app_id: str, access_key: str = "", referrer: str = "",
                 hits: int = 5, timeout: float = 8.0, _retry: int = 1) -> str:
    """
    製品名 query で楽天市場を検索し、先頭の商品画像URL(大きめサイズ)を返す。
    取得できなければ ""（例外・レート制限・パース失敗も "" にフォールバック）。
    """
    kw = _clean_keyword(query)
    if not (kw and app_id and access_key and referrer):
        return ""

    params = {
        "applicationId": app_id,
        "keyword": kw,
        "hits": max(1, min(hits, 10)),
        "imageFlag": 1,          # 画像がある商品のみ
        "sort": "standard",
        "formatVersion": 2,      # Items を平坦化
    }
    url = _ENDPOINT + "?" + urllib.parse.urlencode(params)
    origin = _origin_of(referrer)
    headers = {
        "User-Agent": "gadgege/1.0",
        "accessKey": access_key,
        "Origin": origin,
        "Referer": origin + "/",
    }

    _throttle()
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        # レート制限は1回だけ待って再試行
        if e.code == 429 and _retry > 0:
            time.sleep(1.5)
            return search_image(query, app_id, access_key, referrer, hits, timeout, _retry - 1)
        return ""
    except Exception:
        return ""

    for item in (data.get("Items") or []):
        imgs = item.get("mediumImageUrls") or item.get("smallImageUrls") or []
        if not imgs:
            continue
        first = imgs[0]
        u = first if isinstance(first, str) else (first or {}).get("imageUrl", "")
        if u:
            return _upsize(u)
    return ""
