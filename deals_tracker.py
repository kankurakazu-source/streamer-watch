"""
deals_tracker.py
----------------
「セール・買い時トラッカー」機能。主要タイトル(config.STEAM_WATCHLIST)の割引率を
毎回記録し、過去の割引実績から「いま買うべきか」を判定して site/deals.html を生成する。

安全方針（厳守）:
- 価格（円）は一切表示しない。扱うのは discount_percent（○%オフ）のみ。
- データに無い数字は作らない。計測期間が短い場合は正直に「計測中」と表示する。
"""

import html
import os
import urllib.parse
from datetime import datetime, timedelta, timezone

import config
import storage
from collectors import steam_collector

JST = timezone(timedelta(hours=9))

# 判定バッジの表示順（グループ順）と色クラス
_VERDICT_ORDER = {"買い時": 0, "セール中": 1, "計測中": 2, "セール中・計測中": 2, "待ち": 3}
_VERDICT_CLASS = {
    "買い時": "buy", "セール中": "sale",
    "セール中・計測中": "watch", "計測中": "watch", "待ち": "wait",
}

STEAM_CDN_HEADER = "https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg"


def _esc(s) -> str:
    return html.escape(str(s or ""))


# ============================================
# 収集・記録
# ============================================
def record_discounts(collected: dict) -> dict[int, dict]:
    """
    config.STEAM_WATCHLIST 全件の現在割引を取得し、
    steam_featured.specials（既に取得済みのdiscount_percent）も統合してDBに記録する。
    割引0も記録する（＝「その日はセールなし」の証拠として残す）。
    戻り値: {appid: {"name": str, "discount": int}}
    """
    appids = list(config.STEAM_WATCHLIST.keys())
    discounts = steam_collector.fetch_discounts(appids, cc=config.STEAM_CC)

    merged: dict[int, dict] = {
        appid: {"name": config.STEAM_WATCHLIST[appid], "discount": int(discounts.get(appid) or 0)}
        for appid in appids
    }

    # steam_featured.specials 分を統合（API再呼び出し不要。discount_percentが既にある）
    specials = (collected.get("steam_featured") or {}).get("specials", []) or []
    for it in specials:
        appid = it.get("appid")
        if not appid:
            continue
        appid = int(appid)
        disc = int(it.get("discount_percent") or 0)
        name = it.get("name") or ""
        if appid in merged:
            # 同一appidはfetch_discountsの値と一致するはずだが、念のため大きい方を採用
            merged[appid]["discount"] = max(merged[appid]["discount"], disc)
            if not merged[appid]["name"]:
                merged[appid]["name"] = name
        else:
            merged[appid] = {"name": name, "discount": disc}

    for appid, info in merged.items():
        storage.save_game_metric(
            config.HISTORY_DB, "steam", appid, info["name"], "discount", info["discount"]
        )
    return merged


# ============================================
# 集計・買い時判定
# ============================================
def _to_jst_date(iso_str: str):
    """ISO8601(UTC想定)文字列 -> JSTのdateオブジェクト。"""
    dt = datetime.fromisoformat(iso_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(JST).date()


def _player_trend_pct(db_path: str, appid: int) -> float | None:
    """直近7日の同接トレンド(%)。最新値・7日前付近の値のどちらかが無ければNone。"""
    with storage.get_connection(db_path) as conn:
        cur = conn.execute(
            """
            SELECT value FROM game_snapshots
            WHERE source='steam' AND game_key=? AND metric='player_count'
            ORDER BY recorded_at DESC LIMIT 1
            """,
            (str(appid),),
        )
        row = cur.fetchone()
    latest = row[0] if row and row[0] is not None else None
    if latest is None:
        return None
    prev = storage.get_prev_day_metric(db_path, "steam", str(appid), "player_count", min_age_hours=7 * 24)
    return storage.calc_growth_rate(latest, prev)


def build_deals_data(db_path: str) -> list[dict]:
    """
    game_snapshots(source='steam', metric='discount')をappidごとに集計し、
    買い時判定(verdict)を付けたリストを返す（並び順は仕様通り）。
    """
    with storage.get_connection(db_path) as conn:
        cur = conn.execute(
            """
            SELECT game_key, game_name, value, recorded_at FROM game_snapshots
            WHERE source='steam' AND metric='discount'
            ORDER BY game_key, recorded_at
            """
        )
        rows = cur.fetchall()

    by_appid: dict[str, list[tuple]] = {}
    for game_key, game_name, value, recorded_at in rows:
        by_appid.setdefault(game_key, []).append((game_name, value, recorded_at))

    now_jst_date = datetime.now(JST).date()
    deals: list[dict] = []
    for game_key, records in by_appid.items():
        try:
            appid = int(game_key)
        except (TypeError, ValueError):
            continue
        values = [v for _, v, _ in records if v is not None]
        if not values:
            continue
        name = records[-1][0] or config.STEAM_WATCHLIST.get(appid, str(appid))
        current_discount = int(records[-1][1] or 0)
        max_discount = int(max(values))
        tracked_since_date = _to_jst_date(records[0][2])
        tracked_days = (now_jst_date - tracked_since_date).days

        sale_dates = [_to_jst_date(rec_at) for _, v, rec_at in records if v]
        last_sale_date = max(sale_dates).strftime("%Y-%m-%d") if sale_dates else None

        trend = _player_trend_pct(db_path, appid)

        if tracked_days < 14:
            verdict = "セール中・計測中" if current_discount > 0 else "計測中"
        elif current_discount > 0 and current_discount >= max_discount:
            verdict = "買い時"
        elif current_discount > 0:
            verdict = "セール中"
        else:
            verdict = "待ち"

        deals.append({
            "appid": appid,
            "name": name,
            "current_discount": current_discount,
            "max_discount": max_discount,
            "last_sale_date": last_sale_date,
            "tracked_since": tracked_since_date.strftime("%Y-%m-%d"),
            "tracked_days": tracked_days,
            "player_trend_pct": trend,
            "verdict": verdict,
        })

    deals.sort(key=lambda d: (_VERDICT_ORDER.get(d["verdict"], 9), -d["current_discount"]))
    return deals


# ============================================
# ページ生成
# ============================================
_CSS = """
  :root{
    --bg:#0a0e16; --bg2:#0c1220; --card:#121a29; --card-hi:#172135; --line:#243046; --line-soft:#1a2334;
    --text:#eef2f8; --muted:#9db0c6; --dim:#647689;
    --accent:#39d8ff; --accent-2:#8a6bff; --sale:#ff6a3d; --green:#2fd27e; --violet:#a78bfa; --blue:#5aa8ff;
    --steam:#1387b8;
    --shadow:0 10px 30px rgba(0,0,0,.45);
    --grad:linear-gradient(135deg,var(--accent),var(--accent-2));
  }
  *{box-sizing:border-box;}
  html{scroll-behavior:smooth;}
  body{margin:0;background:var(--bg);color:var(--text);
    font-family:"Hiragino Kaku Gothic ProN","Hiragino Sans","Yu Gothic UI","Noto Sans JP","Segoe UI",system-ui,-apple-system,"Meiryo",sans-serif;
    line-height:1.75;-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility;font-feature-settings:"palt";}
  a{color:inherit;text-decoration:none;}
  .wrap{max-width:1120px;margin:0 auto;padding:0 22px;}
  header{position:sticky;top:0;z-index:20;background:rgba(10,14,22,.72);
    backdrop-filter:blur(14px) saturate(140%);border-bottom:1px solid var(--line-soft);}
  .head{display:flex;align-items:center;justify-content:space-between;gap:12px;height:62px;}
  .logo{display:flex;align-items:center;gap:10px;font-weight:800;font-size:19px;flex:0 0 auto;text-decoration:none;}
  .logo-mark{width:30px;height:30px;border-radius:8px;display:block;flex:0 0 auto;box-shadow:0 0 16px rgba(57,216,255,.30);}
  .logo-name{display:inline-flex;flex-direction:column;line-height:1.04;}
  .logo-sub{font-size:9.5px;font-weight:700;letter-spacing:.13em;color:var(--accent);font-style:normal;text-transform:uppercase;margin-top:1px;}
  nav{display:flex;align-items:center;gap:6px;min-width:0;flex-wrap:nowrap;overflow-x:auto;overflow-y:hidden;
    -webkit-overflow-scrolling:touch;scrollbar-width:none;}
  nav::-webkit-scrollbar{display:none;}
  nav a{position:relative;flex:0 0 auto;white-space:nowrap;padding:8px 12px;font-size:13.5px;color:var(--muted);
    border-radius:9px;transition:color .15s,background .15s;}
  nav a:hover{color:var(--text);background:rgba(255,255,255,.05);}
  nav a.on{color:var(--text);background:rgba(57,216,255,.12);}
  .x-link{display:inline-flex;align-items:center;gap:5px;flex:0 0 auto;color:var(--muted);transition:color .15s;}
  .x-link:hover{color:var(--accent);}
  .x-link svg{width:16px;height:16px;display:block;}
  footer .social{margin:0 0 12px;}
  footer .social .x-link{font-weight:700;color:var(--text);}
  footer .social .x-link:hover{color:var(--accent);}

  .hero{position:relative;overflow:hidden;border-bottom:1px solid var(--line-soft);
    background:radial-gradient(900px 380px at 12% -20%,rgba(57,216,255,.20),transparent 60%),
      radial-gradient(760px 360px at 92% 0%,rgba(138,107,255,.18),transparent 55%),
      linear-gradient(180deg,#0b1120,#0a0e16);}
  .hero .wrap{padding:48px 22px 40px;}
  .kicker{display:inline-flex;align-items:center;gap:8px;font-size:12px;font-weight:700;letter-spacing:.16em;
    color:var(--accent);text-transform:uppercase;margin-bottom:16px;}
  .kicker::before{content:"";width:26px;height:2px;background:var(--grad);border-radius:2px;}
  .hero h1{font-size:38px;line-height:1.28;margin:0 0 14px;font-weight:800;letter-spacing:.005em;max-width:780px;}
  .hero h1 .c{background:var(--grad);-webkit-background-clip:text;background-clip:text;color:transparent;}
  .hero p{margin:0;color:var(--muted);font-size:15.5px;max-width:640px;line-height:1.85;}

  main{padding:34px 0 10px;}
  .legend{display:flex;flex-wrap:wrap;gap:10px;margin:0 0 24px;}
  .legend .b{display:inline-flex;align-items:center;gap:6px;font-size:12px;color:var(--muted);
    background:rgba(255,255,255,.04);border:1px solid var(--line);padding:6px 12px;border-radius:100px;}

  .deals{display:flex;flex-direction:column;gap:14px;}
  .deal{display:flex;gap:18px;align-items:center;background:var(--card);border:1px solid var(--line-soft);
    border-radius:16px;padding:16px 18px;transition:border-color .2s,box-shadow .2s,transform .2s;}
  .deal:hover{border-color:rgba(57,216,255,.4);box-shadow:var(--shadow);transform:translateY(-2px);}
  .deal .th{width:140px;height:66px;flex:0 0 auto;border-radius:11px;background:var(--bg2) center/cover no-repeat;
    border:1px solid var(--line-soft);}
  .deal .main{flex:1;min-width:0;}
  .deal .name{font-weight:800;font-size:16.5px;margin-bottom:6px;}
  .deal .stats{display:flex;flex-wrap:wrap;gap:16px 22px;font-size:12.5px;color:var(--muted);}
  .deal .stats .k{color:var(--dim);margin-right:4px;}
  .deal .cur{flex:0 0 auto;text-align:center;min-width:96px;}
  .deal .cur .num{font-size:26px;font-weight:800;}
  .deal .cur .num.off{color:var(--sale);}
  .deal .cur .num.none{color:var(--dim);font-size:15px;font-weight:700;}
  .deal .cur .lbl{font-size:10.5px;color:var(--dim);letter-spacing:.06em;margin-top:2px;}
  .badge{flex:0 0 auto;font-size:12px;font-weight:800;padding:6px 14px;border-radius:100px;white-space:nowrap;}
  .badge.buy{background:rgba(47,210,126,.16);color:var(--green);border:1px solid rgba(47,210,126,.35);}
  .badge.sale{background:rgba(255,106,61,.16);color:var(--sale);border:1px solid rgba(255,106,61,.35);}
  .badge.watch{background:rgba(90,168,255,.16);color:var(--blue);border:1px solid rgba(90,168,255,.35);}
  .badge.wait{background:rgba(255,255,255,.06);color:var(--dim);border:1px solid var(--line);}
  .trend{font-size:12px;font-weight:700;}
  .trend.up{color:var(--green);}
  .trend.down{color:var(--sale);}
  .buy-btn{flex:0 0 auto;display:inline-flex;align-items:center;justify-content:center;font-size:13px;font-weight:800;
    padding:10px 16px;border-radius:11px;background:var(--steam);color:#fff;white-space:nowrap;
    transition:transform .12s,filter .15s;}
  .buy-btn:hover{transform:translateY(-2px);filter:brightness(1.12);}

  .note{margin:34px 0 0;font-size:12.5px;color:var(--muted);background:var(--card);border:1px solid var(--line-soft);
    border-radius:12px;padding:16px 18px;line-height:1.85;}
  .note .meta{margin-top:10px;color:var(--dim);font-size:11.5px;}

  footer{margin-top:44px;border-top:1px solid var(--line-soft);padding:26px 0 46px;color:var(--dim);font-size:12px;
    background:linear-gradient(180deg,transparent,rgba(138,107,255,.04));}
  footer .disc{background:var(--card);border:1px solid var(--line-soft);border-radius:12px;padding:14px 16px;
    margin-bottom:16px;color:var(--muted);line-height:1.75;}

  @media(max-width:680px){
    .head{height:56px;}
    .hero .wrap{padding:36px 22px 32px;}
    .hero h1{font-size:27px;}
    nav{gap:2px;-webkit-mask-image:linear-gradient(90deg,#000 90%,transparent);mask-image:linear-gradient(90deg,#000 90%,transparent);}
    nav a{padding:8px 10px;font-size:13px;}
    .deal{flex-direction:column;align-items:stretch;gap:12px;}
    .deal .th{width:100%;height:150px;}
    .deal .cur{min-width:0;display:flex;flex-direction:row-reverse;align-items:center;
      justify-content:flex-end;gap:8px;}
    .deal .cur .lbl{margin-top:0;}
    .badge{align-self:flex-start;}
    .buy-btn{width:100%;}
  }
"""

_X_SVG = ("<svg viewBox='0 0 24 24' width='16' height='16' aria-hidden='true'>"
          "<path fill='currentColor' d='M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817"
          "L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z'/></svg>")


def _x_link_html(show_handle: bool) -> str:
    url = config.x_url()
    if not url:
        return ""
    handle = config.x_handle()
    label = f" @{handle}" if show_handle else ""
    return (f"<a class=\"x-link\" href=\"{_esc(url)}\" target=\"_blank\" rel=\"noopener\" "
            f"aria-label=\"公式X @{_esc(handle)}\">{_X_SVG}{_esc(label)}</a>")


def _trend_html(pct: float | None) -> str:
    if pct is None:
        return ""
    cls = "up" if pct >= 0 else "down"
    arrow = "↑" if pct >= 0 else "↓"
    return f'<span class="trend {cls}">{arrow}{pct:+.0f}%</span>'


def _deal_card(d: dict) -> str:
    appid = d["appid"]
    thumb = STEAM_CDN_HEADER.format(appid=appid)
    disc = d["current_discount"]
    cur_html = (f'<div class="num off">-{disc}%</div>' if disc > 0
                else '<div class="num none">セールなし</div>')
    # 計測期間内にセール実績が無い(max=0)タイトルに「-0%」と出すのは紛らわしいので出し分ける
    if d["max_discount"] > 0:
        stats = [f'<span><span class="k">過去最大</span>-{d["max_discount"]}%</span>']
    else:
        stats = ['<span><span class="k">過去最大</span>計測期間内なし</span>']
    if d["last_sale_date"]:
        stats.append(f'<span><span class="k">直近セール</span>{d["last_sale_date"]}</span>')
    trend = _trend_html(d.get("player_trend_pct"))
    if trend:
        stats.append(f'<span><span class="k">同接7日トレンド</span>{trend}</span>')
    badge_cls = _VERDICT_CLASS.get(d["verdict"], "wait")
    store_url = f"https://store.steampowered.com/app/{appid}/"
    return f"""      <div class="deal">
        <div class="th" style="background-image:url('{_esc(thumb)}')"></div>
        <div class="main">
          <div class="name">{_esc(d['name'])}</div>
          <div class="stats">{"".join(stats)}</div>
        </div>
        <div class="cur">
          {cur_html}
          <div class="lbl">現在割引</div>
        </div>
        <span class="badge {badge_cls}">{_esc(d['verdict'])}</span>
        <a class="buy-btn" href="{_esc(store_url)}" target="_blank" rel="nofollow noopener">Steamで見る</a>
      </div>"""


def render_deals_page(deals: list[dict], out_path: str) -> None:
    """deals（build_deals_dataの戻り値）から site/deals.html を書き出す。"""
    now_jst = datetime.now(JST)
    base = (config.SITE_BASE_URL or "").rstrip("/")
    # Cloudflare Pagesが.html付きURLを拡張子なしに308リダイレクトするため、拡張子なしの正規URLにする。
    canonical = f"{base}/deals" if base else ""
    og_image = f"{base}/ogp.png" if base else ""
    title = "Steamセール・買い時トラッカー｜ガジェゲ"
    description = ("主要タイトルの割引率を毎日自動記録し、過去の割引実績から"
                    "「いま買うべきか」を判定するSteamセール・買い時トラッカー。")

    if deals:
        oldest = min(d["tracked_since"] for d in deals)
    else:
        oldest = now_jst.strftime("%Y-%m-%d")

    cards = "\n".join(_deal_card(d) for d in deals) if deals else (
        '      <div class="deal"><div class="main"><div class="name">計測中のデータがまだありません。</div></div></div>'
    )

    canonical_tag = f'<link rel="canonical" href="{_esc(canonical)}">' if canonical else ""
    og_url_tag = f'<meta property="og:url" content="{_esc(canonical)}">' if canonical else ""

    import json
    jsonld = json.dumps({
        "@context": "https://schema.org",
        "@type": "WebPage",
        "name": title,
        "description": description,
        "url": canonical or None,
    }, ensure_ascii=False)

    html_out = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)}</title>
<meta name="description" content="{_esc(description)}">
{canonical_tag}
<meta property="og:type" content="website">
<meta property="og:site_name" content="ガジェゲ（Gadget×Game）">
<meta property="og:title" content="{_esc(title)}">
<meta property="og:description" content="{_esc(description)}">
{og_url_tag}
<meta property="og:image" content="{_esc(og_image)}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{_esc(title)}">
<meta name="twitter:description" content="{_esc(description)}">
<meta name="twitter:image" content="{_esc(og_image)}">
<script type="application/ld+json">{jsonld}</script>
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32.png">
<link rel="alternate icon" href="/favicon.ico">
<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">
<style>{_CSS}</style>
</head>
<body>
<header><div class="wrap head">
  <a class="logo" href="index.html"><img class="logo-mark" src="/favicon.svg" alt="ガジェゲ" width="30" height="30"><span class="logo-name">ガジェゲ<i class="logo-sub">Gadget×Game</i></span></a>
  <nav>
    <a href="index.html#trending">いま読まれている</a>
    <a href="index.html#games">ゲーム</a>
    <a href="index.html#devices">デバイス</a>
    <a href="index.html#articles">記事</a>
    <a class="on" href="deals.html">セール・買い時</a>
    {_x_link_html(False)}
  </nav>
</div></header>

<section class="hero">
  <div class="wrap">
    <div class="kicker">Steam Deal Tracker</div>
    <h1>Steam<span class="c">セール・買い時トラッカー</span></h1>
    <p>主要タイトルの割引率を毎日5回自動記録し、過去の割引実績から「いま買うべきか」を判定する。</p>
  </div>
</section>

<main class="wrap">
  <div class="legend">
    <span class="b">🟢 買い時＝過去最大級の割引が今出ている</span>
    <span class="b">🟠 セール中＝割引はあるが過去最大ではない</span>
    <span class="b">🔵 計測中＝記録開始から14日未満で判定を保留</span>
    <span class="b">⚪ 待ち＝現在セールなし</span>
  </div>
  <div class="deals">
{cards}
  </div>
  <div class="note">
    割引率はSteam公開情報の自動記録に基づく参考値。計測開始日以降のデータのみで判定しており、それ以前のセール履歴は含まない。最新の価格・割引は必ずSteamストアで確認してほしい。
    <div class="meta">計測開始日: {oldest} ／ 最終更新: {now_jst.strftime('%Y/%m/%d %H:%M')} (JST)</div>
  </div>

  <footer>
    <div class="disc">当サイトはアフィリエイトプログラム（Amazonアソシエイト等）を利用し、商品の紹介で収益を得ることがあります。価格・割引はSteam等の公開情報を基にした参考値です。掲載時点の情報のため、最新の価格は各ストアでご確認ください。Amazonのアソシエイトとして、当メディアは適格販売により収入を得ています。</div>
    <div class="social">{_x_link_html(True)}</div>
    (c) {now_jst.year} ガジェゲ（Gadget×Game） ／ データで見るゲームトレンド
  </footer>
</main>
</body>
</html>
"""

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_out)


if __name__ == "__main__":
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    storage.init_db(config.HISTORY_DB)
    print("=== 割引記録中... ===")
    merged = record_discounts({})
    for appid, info in merged.items():
        disc = info["discount"]
        print(f"  {info['name']}: {'-' + str(disc) + '%' if disc else 'セールなし'}")

    data = build_deals_data(config.HISTORY_DB)
    out = os.path.join(config.SITE_DIR, "deals.html")
    render_deals_page(data, out)
    print(f"=== {out} を生成しました（{len(data)}件） ===")
