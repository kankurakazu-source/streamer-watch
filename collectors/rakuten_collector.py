"""
rakuten_collector.py
--------------------
楽天ウェブサービス「楽天市場 商品検索API(IchibaItem/Search)」で、製品名から
実際の商品画像URLを取得する。デバイス記事などSteam画像が無い対象の画像ソース。

- 必要なのは applicationId（無料。https://webservice.rakuten.co.jp/ で発行）のみ。
- applicationId 未設定なら常に "" を返す（呼び出し側は従来どおり画像なしになる）。
- 画像は mediumImageUrl(?_ex=128x128) を大きめサイズへ差し替えて返す。
"""
import json
import re
import urllib.parse
import urllib.request

_API = "https://app.rakuten.co.jp/services/api/IchibaItem/Search/20220601"


def _upsize(url: str, px: int = 600) -> str:
    """楽天サムネURLの ?_ex=WxH をより大きいサイズに差し替える（無ければそのまま）。"""
    if not url:
        return ""
    if "_ex=" in url:
        return re.sub(r"_ex=\d+x\d+", f"_ex={px}x{px}", url)
    return url


def search_image(query: str, app_id: str, hits: int = 3,
                 affiliate_id: str = "", timeout: float = 8.0) -> str:
    """
    製品名 query で楽天市場を検索し、先頭の商品画像URLを返す。
    取得できなければ ""。ネットワーク/パース失敗も "" にフォールバック（例外を投げない）。
    """
    if not (query and app_id):
        return ""
    params = {
        "applicationId": app_id,
        "keyword": query,
        "hits": max(1, min(hits, 10)),
        "imageFlag": 1,          # 画像がある商品のみ
        "sort": "standard",      # 楽天標準の関連順
        "formatVersion": 2,      # Items を平坦化（パースが簡単）
    }
    if affiliate_id:
        params["affiliateId"] = affiliate_id
    url = _API + "?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "gadgege/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
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
