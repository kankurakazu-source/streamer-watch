"""
article_render.py
-----------------
生成された記事データ（Claude出力＋Steam画像/割引の付与済み）から、
公開用の記事HTMLを組み立て、トップページ(index.html)の記事一覧を差し替え、
X投稿の文面を組み立てるためのユーティリティ。

ネットワークアクセスはしない（純粋なレンダリング）。画像URL・割引率・appidの
解決は呼び出し側(article_generator.py)で済ませ、ここには結果だけ渡す。

安全方針:
- 価格(円)は捏造しない。割引率(discount_percent)はSteam公式データで判明した時のみ表示。
- アフィリンクは各ストアの検索URL（設定があればタグ付与）。断定・誇張はしない。
"""

import html
import json
import os
import random
import re
import urllib.parse
from datetime import datetime

import config
from game_analyzer import weighted_len

STEAM_CDN = "https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg"
_URL_RE = re.compile(r"https?://\S+")

# 親ポストに入れる「リプ(2つ目)へ誘導」する一文。毎回ランダムで表現を変える。
_REPLY_GUIDES = [
    "詳細はリプ欄へ👇",
    "続きはリプ欄のリンクから👇",
    "価格・詳細はリプ欄に👇",
    "まとめはリプのリンクへ👇",
    "詳しくはリプ欄をチェック👇",
    "リプ欄に詳細まとめてます👇",
    "続きと比較はリプのリンクで👇",
    "全部リプ欄にまとめた👇",
    "気になる人はリプ欄へ👇",
    "詳細・根拠はリプの記事で👇",
]

# カテゴリ→acardのアクセント色クラス（index.htmlのCSSに合わせる）
_CAT_CLASS = {
    "セール分析": "o", "注目株": "g", "eスポーツ": "p", "デバイス": "p",
    "新作": "", "データ分析": "", "考察": "",
}

# 記事テンプレート（CSSは site/article.html と同系統。プレースホルダを置換して使う）
_CSS = """
  :root{
    --bg:#0a0e16; --bg2:#0c1220; --card:#121a29; --card-hi:#172135; --line:#243046; --line-soft:#1a2334;
    --text:#eef2f8; --muted:#a7b7ca; --dim:#647689;
    --accent:#39d8ff; --accent-2:#8a6bff; --sale:#ff6a3d; --green:#2fd27e; --violet:#a78bfa;
    --amazon:#ff9900; --rakuten:#c5121a; --dmm:#f04e23; --steam:#1387b8;
    --shadow:0 10px 30px rgba(0,0,0,.45);
    --grad:linear-gradient(135deg,var(--accent),var(--accent-2));
  }
  *{box-sizing:border-box;}
  html{scroll-behavior:smooth;}
  body{margin:0;background:var(--bg);color:var(--text);
    font-family:"Hiragino Kaku Gothic ProN","Hiragino Sans","Yu Gothic UI","Noto Sans JP","Segoe UI",system-ui,-apple-system,"Meiryo",sans-serif;
    line-height:1.9;-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility;font-feature-settings:"palt";}
  a{color:inherit;text-decoration:none;}
  .wrap{max-width:1000px;margin:0 auto;padding:0 20px;}
  header{position:sticky;top:0;z-index:20;background:rgba(10,14,22,.72);
    backdrop-filter:blur(14px) saturate(140%);border-bottom:1px solid var(--line-soft);}
  .head{display:flex;align-items:center;justify-content:space-between;height:60px;}
  .logo{display:flex;align-items:center;gap:10px;font-weight:800;font-size:19px;}
  .logo{text-decoration:none;}
  .logo-mark{width:28px;height:28px;border-radius:8px;display:block;flex:0 0 auto;box-shadow:0 0 16px rgba(57,216,255,.30);}
  .logo-name{display:inline-flex;flex-direction:column;line-height:1.04;}
  .logo-sub{font-size:9px;font-weight:700;letter-spacing:.13em;color:var(--accent);font-style:normal;text-transform:uppercase;margin-top:1px;}
  nav{display:flex;align-items:center;gap:16px;font-size:14px;color:var(--muted);}
  nav a:hover{color:var(--text);}
  .x-link{display:inline-flex;align-items:center;gap:5px;color:var(--muted);transition:color .15s;}
  .x-link:hover{color:var(--accent);}
  .x-link svg{width:16px;height:16px;}
  footer .social{margin:0 0 12px;}
  footer .social .x-link{font-weight:700;color:var(--text);}
  footer .social .x-link:hover{color:var(--accent);}
  .article{max-width:720px;margin:0 auto;padding:30px 0 10px;}
  .crumb{font-size:12px;color:var(--dim);margin-bottom:14px;}
  .crumb a:hover{color:var(--accent);}
  .cat{display:inline-block;font-size:12px;font-weight:800;padding:4px 12px;border-radius:100px;letter-spacing:.02em;
    background:rgba(255,106,61,.15);color:var(--sale);border:1px solid rgba(255,106,61,.3);}
  h1{font-size:30px;line-height:1.45;margin:14px 0 12px;font-weight:800;letter-spacing:.005em;}
  .meta{font-size:13px;color:var(--dim);margin-bottom:20px;}
  .hero{width:100%;height:320px;border-radius:16px;background:var(--bg2) center/cover;
    box-shadow:var(--shadow);border:1px solid var(--line-soft);}
  .lead{font-size:17px;margin:24px 0;color:#dfe7f1;line-height:1.95;}
  .tldr{background:linear-gradient(135deg,rgba(57,216,255,.08),rgba(138,107,255,.08));
    border:1px solid rgba(57,216,255,.28);border-radius:14px;padding:16px 20px;margin:24px 0;}
  .tldr .lbl{font-size:11px;font-weight:800;letter-spacing:.14em;text-transform:uppercase;
    color:var(--accent);}
  .tldr .body{font-size:15.5px;margin-top:6px;color:var(--text);}
  h2.g{font-size:22px;margin:38px 0 8px;padding-top:20px;border-top:1px solid var(--line-soft);
    font-weight:800;letter-spacing:.01em;}
  .gimg{width:100%;height:210px;border-radius:14px;background:var(--bg2) center/cover;margin:14px 0;
    box-shadow:var(--shadow);border:1px solid var(--line-soft);}
  p{margin:14px 0;color:#d7e0ec;}
  .buybox{margin:20px 0;border:1px solid rgba(255,106,61,.28);border-radius:16px;padding:18px;
    background:linear-gradient(135deg,rgba(255,106,61,.08),rgba(255,45,110,.05));box-shadow:var(--shadow);}
  .buybox .bt{display:flex;gap:14px;align-items:center;}
  .buybox .th{width:130px;height:62px;border-radius:10px;background:var(--bg2) center/cover;flex:0 0 auto;
    border:1px solid var(--line-soft);}
  .buybox .name{font-weight:800;font-size:15.5px;}
  .buybox .off{margin-top:5px;font-size:12px;font-weight:800;color:#fff;
    background:linear-gradient(135deg,#ff6a3d,#ff2d6e);padding:3px 10px;border-radius:100px;display:inline-block;}
  .buybox .buys{display:flex;gap:10px;margin-top:16px;}
  .buy{flex:1;text-align:center;font-size:14px;font-weight:800;padding:12px 4px;border-radius:11px;color:#fff;
    transition:transform .12s,filter .15s,box-shadow .15s;}
  .buy:hover{transform:translateY(-2px);filter:brightness(1.08);box-shadow:0 8px 20px rgba(0,0,0,.4);}
  .buy:active{transform:translateY(0);}
  .buy.amazon{background:var(--amazon);color:#231a08;}
  .buy.rakuten{background:var(--rakuten);}
  .buy.dmm{background:var(--dmm);}
  .buy.steam{background:var(--steam);}
  .pnote{font-size:11px;color:var(--dim);margin-top:10px;line-height:1.7;}
  .back{display:inline-flex;align-items:center;gap:6px;margin:30px 0 0;font-size:14px;font-weight:700;color:var(--accent);}
  .back:hover{gap:10px;transition:gap .15s;}
  footer{margin-top:46px;border-top:1px solid var(--line-soft);padding:26px 0 44px;color:var(--dim);font-size:12px;
    background:linear-gradient(180deg,transparent,rgba(138,107,255,.04));}
  footer .disc{max-width:720px;margin:0 auto 14px;background:var(--card);border:1px solid var(--line-soft);
    border-radius:12px;padding:14px 16px;color:var(--muted);line-height:1.75;}
  /* 人気の記事（記事下の回遊導線） */
  .related-block{max-width:1000px;margin:44px auto 0;}
  .related-block .sec-head{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;margin:0 0 18px;}
  .related-block .sec-head .k{font-size:11px;font-weight:700;letter-spacing:.16em;color:var(--accent);text-transform:uppercase;}
  .related-block .sec-head h2{font-size:21px;margin:6px 0 0;font-weight:800;letter-spacing:.01em;display:flex;align-items:center;gap:11px;}
  .related-block .sec-head h2 .ic{width:6px;height:22px;border-radius:3px;background:var(--grad);display:inline-block;}
  .related-block .sec-head .note{font-size:12.5px;color:var(--dim);}
  .grid{display:grid;gap:16px;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));}
  .acard{position:relative;display:flex;gap:15px;background:var(--card);border:1px solid var(--line-soft);
    border-radius:16px;padding:14px;overflow:hidden;transition:transform .22s,border-color .22s,box-shadow .22s,background .22s;}
  .acard:hover{transform:translateY(-4px);background:var(--card-hi);border-color:rgba(57,216,255,.5);
    box-shadow:var(--shadow),0 8px 34px rgba(57,216,255,.16);}
  .acard .th{width:120px;height:82px;flex:0 0 auto;border-radius:11px;background:var(--bg2) center/cover no-repeat;transition:filter .22s;}
  .acard:hover .th{filter:brightness(1.08) saturate(1.05);}
  .acard .th.noimg{background:radial-gradient(120px 80px at 70% 20%,rgba(138,107,255,.35),transparent 60%),linear-gradient(135deg,#182236,#10182a);position:relative;}
  .acard .th.noimg::after{content:"GADGET";position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:var(--dim);font-size:10px;font-weight:800;letter-spacing:.22em;}
  .acbody{min-width:0;display:flex;flex-direction:column;}
  .acmeta{display:flex;align-items:center;gap:7px;margin-bottom:7px;flex-wrap:wrap;}
  .acard .cat{display:inline-block;font-size:10.5px;font-weight:800;padding:3px 10px;border-radius:100px;letter-spacing:.02em;
    background:rgba(57,216,255,.14);color:var(--accent);border:1px solid rgba(57,216,255,.25);}
  .acard .cat.o{background:rgba(255,106,61,.15);color:var(--sale);border-color:rgba(255,106,61,.3);}
  .acard .cat.g{background:rgba(47,210,126,.15);color:var(--green);border-color:rgba(47,210,126,.3);}
  .acard .cat.p{background:rgba(167,139,250,.16);color:var(--violet);border-color:rgba(167,139,250,.3);}
  .acard .pill-break{font-size:10.5px;font-weight:800;letter-spacing:.04em;padding:3px 9px;border-radius:100px;color:#fff;
    background:linear-gradient(135deg,#ff6a3d,#ff2d6e);box-shadow:0 0 14px rgba(255,45,110,.45);}
  .acard .ttl{font-size:15px;font-weight:700;margin:0 0 6px;line-height:1.5;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;}
  .acard:hover .ttl{color:#fff;}
  .acard .ex{font-size:12px;color:var(--muted);line-height:1.65;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;}
  .acard .d{font-size:11px;color:var(--dim);margin-top:auto;padding-top:8px;}
  .acard.ranked .th{width:110px;}
  .rank{position:absolute;top:8px;left:8px;z-index:2;min-width:26px;height:26px;padding:0 6px;display:flex;align-items:center;justify-content:center;
    font-weight:800;font-size:14px;color:#07101c;background:var(--grad);border-radius:8px;box-shadow:0 4px 14px rgba(57,216,255,.4);}
  /* この記事を共有 */
  .share-block{max-width:720px;margin:34px auto 0;padding-top:22px;border-top:1px solid var(--line-soft);text-align:center;}
  .share-ttl{font-size:13px;font-weight:800;letter-spacing:.08em;color:var(--muted);margin:0 0 16px;}
  .share-row{display:flex;justify-content:center;gap:14px;}
  .sbtn{width:50px;height:50px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;
    color:#fff;font-weight:800;letter-spacing:.02em;box-shadow:0 4px 14px rgba(0,0,0,.35);
    transition:transform .12s,filter .15s;}
  .sbtn:hover{transform:translateY(-3px);filter:brightness(1.1);}
  .sbtn.x{background:#000;border:1px solid #2a3448;}
  .sbtn.fb{background:#1877F2;}
  .sbtn.line{background:#06C755;font-size:13px;}
  .sbtn.hb{background:#00A4DE;font-size:19px;}
  @media(max-width:680px){h1{font-size:24px;}.hero{height:210px;}.buybox .buys{flex-wrap:wrap;}
    .grid{grid-template-columns:1fr;gap:13px;}.acard .th{width:110px;height:74px;}}
"""

_PAGE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} ｜ガジェゲ</title>
<meta name="description" content="{og_desc}">
{canonical_tag}
<!-- OGP / Twitter Card（Xでリンクを貼った時のカード表示。CTRに直結） -->
<meta property="og:type" content="article">
<meta property="og:site_name" content="ガジェゲ（Gadget×Game）">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{og_desc}">
{og_url_tag}
<meta property="og:image" content="{og_image}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{title}">
<meta name="twitter:description" content="{og_desc}">
<meta name="twitter:image" content="{og_image}">
{jsonld}
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32.png">
<link rel="alternate icon" href="/favicon.ico">
<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">
<style>{css}</style>
</head>
<body>
<header><div class="wrap head">
  <a class="logo" href="/"><img class="logo-mark" src="/favicon.svg" alt="ガジェゲ" width="28" height="28"><span class="logo-name">ガジェゲ<i class="logo-sub">Gadget×Game</i></span></a>
  <nav><a href="../index.html">トップ</a><a href="../deals.html">🔥セール・買い時</a>{x_nav}</nav>
</div></header>

<main class="wrap">
  <article class="article">
    <div class="crumb"><a href="../index.html">ホーム</a> › {category}</div>
    <span class="cat">{category}</span>
    <h1>{title}</h1>
    <div class="meta">{date} ・ ガジェゲ編集部</div>
    {hero}
    <p class="lead">{lead}</p>
    {tldr}
    {body}
    <a class="back" href="../index.html">← トップに戻る</a>
  </article>
{share}
{related}
  <footer>
    <div class="disc">当サイトはアフィリエイトプログラム（Amazonアソシエイト等）を利用し、商品の紹介で収益を得ることがあります。価格・割引はSteam等の公開情報を基にした参考値です。掲載時点の情報のため、最新の価格は各ストアでご確認ください。</div>
    <div class="social">{x_footer}</div>
    (c) {year} ガジェゲ（Gadget×Game） ／ データで見るゲームトレンド
  </footer>
</main>
</body>
</html>
"""


def _esc(s: str) -> str:
    return html.escape(str(s or ""))


# X（旧Twitter）ロゴ
_X_SVG = ("<svg viewBox='0 0 24 24' width='16' height='16' aria-hidden='true'>"
          "<path fill='currentColor' d='M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817"
          "L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z'/></svg>")


def x_link_html(show_handle: bool = True) -> str:
    """公式XアカウントへのリンクHTML。config.X_HANDLE 未設定なら空文字。"""
    url = config.x_url()
    if not url:
        return ""
    handle = config.x_handle()
    label = f" @{handle}" if show_handle else ""
    aria = f"公式X @{handle}"
    return (f"<a class=\"x-link\" href=\"{_esc(url)}\" target=\"_blank\" rel=\"noopener\" "
            f"aria-label=\"{_esc(aria)}\">{_X_SVG}{_esc(label)}</a>")


# Facebook ロゴ（f マーク）
_FB_SVG = ("<svg viewBox='0 0 24 24' width='22' height='22' aria-hidden='true'>"
           "<path fill='currentColor' d='M13.5 21v-8h2.7l.4-3.1h-3.1V7.9c0-.9.25-1.5 1.53-1.5H17V3.6"
           "c-.28-.04-1.23-.12-2.34-.12-2.32 0-3.9 1.42-3.9 4.02v2.24H8v3.1h2.76V21h2.74z'/></svg>")
_XL_SVG = _X_SVG.replace("width='16' height='16'", "width='20' height='20'")


def share_section(url: str, title: str) -> str:
    """記事末尾の「この記事を共有」導線（X/Facebook/LINE/はてブ）。URLが無ければ空。"""
    url = (url or "").strip()
    if not url:
        return ""
    u = urllib.parse.quote(url, safe="")
    t = urllib.parse.quote((title or "").strip(), safe="")
    x_url = f"https://x.com/intent/tweet?text={t}&url={u}"
    fb_url = f"https://www.facebook.com/sharer/sharer.php?u={u}"
    line_url = f"https://social-plugins.line.me/lineit/share?url={u}"
    hb_url = f"https://b.hatena.ne.jp/add?mode=confirm&url={u}&title={t}"
    return f"""  <section class="share-block">
    <div class="share-ttl">この記事を共有</div>
    <div class="share-row">
      <a class="sbtn x" href="{x_url}" target="_blank" rel="nofollow noopener" aria-label="Xで共有">{_XL_SVG}</a>
      <a class="sbtn fb" href="{fb_url}" target="_blank" rel="nofollow noopener" aria-label="Facebookで共有">{_FB_SVG}</a>
      <a class="sbtn line" href="{line_url}" target="_blank" rel="nofollow noopener" aria-label="LINEで共有">LINE</a>
      <a class="sbtn hb" href="{hb_url}" target="_blank" rel="nofollow noopener" aria-label="はてなブックマークに追加">B!</a>
    </div>
  </section>"""


def slugify(dt: datetime, seq: int = 1) -> str:
    """URL用スラッグ（日時ベースで衝突しにくい）。"""
    return f"{dt.strftime('%Y%m%d-%H%M')}-{seq}"


def steam_image_url(appid: int | None) -> str:
    return STEAM_CDN.format(appid=appid) if appid else ""


def affiliate_links(name: str) -> dict:
    """
    商品名から各ストアの検索URLを作る。アフィリID(config)があれば成果計測付きにする。
    - Amazon : 検索URLにアソシエイトタグ(&tag=)を付与。
    - 楽天   : 検索URLを楽天アフィリのリダイレクト(hb.afl.rakuten.co.jp)でラップ。
    - DMM    : 検索URLをDMMアフィリのリダイレクト(al.dmm.co.jp)でラップ。
    IDが無いストアは通常の検索URL（成果は付かないがユーザーの導線としては機能）。
    """
    q = urllib.parse.quote(name or "")

    # Amazon
    amazon = f"https://www.amazon.co.jp/s?k={q}"
    if config.AMAZON_ASSOC_TAG:
        amazon += f"&tag={urllib.parse.quote(config.AMAZON_ASSOC_TAG)}"

    # 楽天
    rakuten_target = f"https://search.rakuten.co.jp/search/mall/{q}/"
    if config.RAKUTEN_AFFILIATE_ID:
        enc = urllib.parse.quote(rakuten_target, safe="")
        rakuten = (f"https://hb.afl.rakuten.co.jp/hgc/{config.RAKUTEN_AFFILIATE_ID}/"
                   f"?pc={enc}&m={enc}")
    else:
        rakuten = rakuten_target

    # DMM
    dmm_target = f"https://www.dmm.co.jp/search/=/searchstr={q}/"
    if config.DMM_AFFILIATE_ID:
        enc = urllib.parse.quote(dmm_target, safe="")
        dmm = (f"https://al.dmm.co.jp/?lurl={enc}"
               f"&af_id={urllib.parse.quote(config.DMM_AFFILIATE_ID)}&ch=link_tool&ch_id=text")
    else:
        dmm = dmm_target

    return {"amazon": amazon, "rakuten": rakuten, "dmm": dmm}


def _paragraphs(body: str) -> str:
    """本文文字列を段落<p>に変換（空行 or 改行で分割）。"""
    chunks = [c.strip() for c in re.split(r"\n\s*\n|\n", body or "") if c.strip()]
    return "".join(f"<p>{_esc(c)}</p>" for c in chunks)


def _buybox(game: dict) -> str:
    """1タイトル分の購入ボックス。game: {name, image_url, discount_percent?}。"""
    name = game.get("name", "")
    if not name:
        return ""
    links = affiliate_links(name)
    img = game.get("image_url", "")
    th = f"<div class='th' style=\"background-image:url('{_esc(img)}')\"></div>" if img else "<div class='th'></div>"
    dp = game.get("discount_percent")
    off = f"<div class='off'>-{int(dp)}% セール中</div>" if dp else ""
    # Steamゲーム(appidあり)は公式ストアへの導線を先頭に付ける（アフィリ制度は無いのでUX目的）
    appid = game.get("appid")
    steam_btn = (f"<a class=\"buy steam\" href=\"https://store.steampowered.com/app/{int(appid)}/\" "
                 f"target=\"_blank\" rel=\"nofollow noopener\">Steamで見る</a>\n        "
                 if appid else "")
    return f"""<div class="buybox">
      <div class="bt">{th}
        <div style="flex:1">
          <div class="name">{_esc(name)}</div>{off}
        </div>
      </div>
      <div class="buys">
        {steam_btn}<a class="buy amazon" href="{_esc(links['amazon'])}" target="_blank" rel="nofollow noopener">Amazonで見る</a>
        <a class="buy rakuten" href="{_esc(links['rakuten'])}" target="_blank" rel="nofollow noopener">楽天</a>
        <a class="buy dmm" href="{_esc(links['dmm'])}" target="_blank" rel="nofollow noopener">DMM</a>
      </div>
      <div class="pnote">※価格・割引は掲載時点の参考値です。最新の価格は各ストアでご確認ください。当サイトはアフィリエイトリンクを使用しています。</div>
    </div>"""


def _article_jsonld(article: dict, canonical: str, image_url: str, now: datetime) -> str:
    """
    記事の構造化データ(JSON-LD, schema.org/Article)を組み立てる。
    検索エンジンがheadline・公開日・著者・画像などを機械可読な形で把握できるようにするためのSEO対応。
    json.dumpsで生成する（手書き文字列連結だとエスケープ漏れの恐れがあるため）。
    """
    date_iso = now.strftime("%Y-%m-%dT%H:%M:%S+09:00")
    data = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": article.get("title", ""),
        "description": (article.get("lead") or article.get("tldr") or "").strip(),
        "datePublished": date_iso,
        "dateModified": date_iso,
        "author": {"@type": "Organization", "name": "ガジェゲ"},
        "publisher": {"@type": "Organization", "name": "ガジェゲ"},
    }
    if image_url:
        data["image"] = image_url
    if canonical:
        data["mainEntityOfPage"] = {"@type": "WebPage", "@id": canonical}
    return f'<script type="application/ld+json">{json.dumps(data, ensure_ascii=False)}</script>'


def render_article(article: dict, related: list[dict] | None = None) -> str:
    """
    article: {
      title, category, lead, tldr, conclusion,
      hero_image_url (str, 任意),
      sections: [ {heading, body, buy:{name,image_url,discount_percent?}(任意)} ]
    }
    related: 他記事のリスト（storage.list_articles）。渡すと末尾に「人気の記事」を出す。
    """
    now = datetime.now()
    hero_url = article.get("hero_image_url", "")
    hero = (f"<div class='hero' style=\"background-image:url('{_esc(hero_url)}')\"></div>"
            if hero_url else "")
    tldr_txt = (article.get("tldr") or "").strip()
    tldr = (f"<div class='tldr'><div class='lbl'>結論</div>"
            f"<div class='body'>{_esc(tldr_txt)}</div></div>" if tldr_txt else "")

    parts = []
    for sec in article.get("sections", []):
        heading = sec.get("heading", "")
        if heading:
            parts.append(f"<h2 class='g'>{_esc(heading)}</h2>")
        buy = sec.get("buy") or {}
        # セクションのバナー画像は使い回しを避けた sec['image_url'] を優先（無ければbuyの画像）
        sec_img = sec.get("image_url") or buy.get("image_url", "")
        if sec_img:
            parts.append(f"<div class='gimg' style=\"background-image:url('{_esc(sec_img)}')\"></div>")
        parts.append(_paragraphs(sec.get("body", "")))
        if buy.get("name"):
            parts.append(_buybox(buy))

    conclusion = (article.get("conclusion") or "").strip()
    if conclusion:
        parts.append("<h2 class='g'>まとめ</h2>")
        parts.append(_paragraphs(conclusion))

    # OGP用メタ: 説明文はlead(無ければtldr)を160字程度に、画像はhero(無ければサイト共通OG画像)
    base_url = (config.SITE_BASE_URL or "").rstrip("/")
    og_desc = _esc((article.get("lead") or article.get("tldr") or "").strip()[:160])
    og_image = hero_url or (f"{base_url}/ogp.png" if base_url else "")
    canonical = (article.get("canonical_url") or "").strip()
    canonical_tag = f'<link rel="canonical" href="{_esc(canonical)}">' if canonical else ""
    og_url_tag = f'<meta property="og:url" content="{_esc(canonical)}">' if canonical else ""
    jsonld = _article_jsonld(article, canonical, hero_url, now)

    # 記事末尾の「人気の記事」（自分自身は除外）
    cur_slug = ""
    if canonical:
        cur_slug = canonical.rstrip("/").split("/")[-1].removesuffix(".html")
    related_html = related_section(related or [], current_slug=cur_slug)
    share_html = share_section(canonical, article.get("title", ""))

    return _PAGE.format(
        title=_esc(article.get("title", "")),
        css=_CSS,
        category=_esc(article.get("category", "")),
        date=now.strftime("%Y/%m/%d"),
        hero=hero,
        lead=_esc(article.get("lead", "")),
        tldr=tldr,
        body="\n".join(parts),
        year=now.year,
        og_desc=og_desc,
        og_image=_esc(og_image),
        canonical_tag=canonical_tag,
        og_url_tag=og_url_tag,
        jsonld=jsonld,
        x_nav=x_link_html(show_handle=False),
        x_footer=x_link_html(show_handle=True),
        related=related_html,
        share=share_html,
    )


# ============================================
# トップページ(index.html)の記事一覧を差し替える
# ============================================
_ARTICLES_RE = re.compile(r"(<!--ARTICLES:START-->).*?(<!--ARTICLES:END-->)", re.DOTALL)


def _article_card(a: dict, rank: int | None = None, base: str = "") -> str:
    """
    1記事分の一覧カード(acard)HTML。a: storage.list_articles の1要素。
    rank を渡すと「いま読まれている」用の順位バッジを付ける。
    画像が無い記事（デバイス記事など）は .noimg でプレースホルダ表示にする。
    base はリンクの基準（トップ=""でarticles/…、記事ページ="/"で/articles/…の絶対パス）。
    """
    catcls = _CAT_CLASS.get(a.get("category", ""), "")
    cls = f"cat {catcls}".strip()
    img = a.get("image_url", "")
    if img:
        th = f"<div class=\"th\" style=\"background-image:url('{_esc(img)}')\"></div>"
    else:
        th = "<div class=\"th noimg\"></div>"
    try:
        d = datetime.fromisoformat(a["created_at"]).strftime("%Y/%m/%d")
    except Exception:
        d = ""
    href = f"{base}{config.ARTICLES_SUBDIR}/{a['slug']}.html"
    rank_badge = f"<span class=\"rank\">{rank}</span>" if rank else ""
    breaking = ("<span class=\"pill-break\">速報</span>"
                if a.get("is_breaking") else "")
    extra_cls = " ranked" if rank else ""
    return f"""      <a class="acard{extra_cls}" data-cat="{_esc(a.get('category',''))}" href="{_esc(href)}">
        {rank_badge}{th}
        <div class="acbody"><div class="acmeta"><span class="{cls}">{_esc(a.get('category',''))}</span>{breaking}</div>
          <div class="ttl">{_esc(a.get('title',''))}</div>
          <div class="ex">{_esc(a.get('excerpt',''))}</div>
          <div class="d">{d}</div></div>
      </a>"""


def _empty_note(msg: str) -> str:
    return f'      <div class="empty">{_esc(msg)}</div>'


def _replace_region(html: str, name: str, inner: str) -> str:
    """<!--NAME:START--> と <!--NAME:END--> の間を inner で置換する（無ければ無変更）。"""
    rx = re.compile(rf"(<!--{name}:START-->).*?(<!--{name}:END-->)", re.DOTALL)
    if not rx.search(html):
        return html
    replacement = f"<!--{name}:START-->\n{inner}\n      <!--{name}:END-->"
    return rx.sub(lambda m: replacement, html)


def _sort_trending(articles: list[dict]) -> list[dict]:
    """「いま読まれている」用の並び：速報を優先し、次に新しい順（実閲覧数は未計測のため代用）。"""
    return sorted(
        articles,
        key=lambda a: (1 if a.get("is_breaking") else 0, a.get("created_at") or ""),
        reverse=True,
    )


def related_section(articles: list[dict], current_slug: str = "", limit: int = 4) -> str:
    """
    記事ページ末尾に置く「人気の記事」セクション（トップの"いま読まれている"と同方式）。
    current_slug の記事は除外。他記事が無ければ空文字を返す（＝セクション非表示）。
    リンクは絶対パス(/articles/…)なので、どの記事ページからでも正しく辿れる。
    """
    others = [a for a in articles if a.get("slug") and a.get("slug") != current_slug]
    picks = _sort_trending(others)[:limit]
    if not picks:
        return ""
    cards = "\n".join(_article_card(a, rank=i, base="/") for i, a in enumerate(picks, 1))
    return f"""  <section class="related-block">
    <div class="sec-head">
      <div><div class="k">POPULAR</div><h2><span class="ic"></span>人気の記事</h2></div>
      <div class="note">よく読まれているトピックから</div>
    </div>
    <div class="grid">
{cards}
    </div>
  </section>"""


def inject_homepage(index_html: str, articles: list[dict]) -> str:
    """
    トップページの領域を再生成する:
      TRENDING … いま読まれている記事（速報優先＋新着）上位5
      GAMES    … 話題・注目ゲーム（デバイス以外＝ゲーム系）上位6
      DEVICES  … 注目のデバイス情報（カテゴリ/種別=デバイス）上位6
      ARTICLES … 全記事（カテゴリで絞り込み可能な一覧）
    """
    def _is_device(a: dict) -> bool:
        return a.get("category") == "デバイス" or a.get("event_type") == "デバイス"

    trending = _sort_trending(articles)[:5]
    games = [a for a in _sort_trending(articles) if not _is_device(a)][:6]
    devices = [a for a in articles if _is_device(a)][:6]

    trending_html = ("\n".join(_article_card(a, rank=i) for i, a in enumerate(trending, 1))
                     if trending else _empty_note("記事がまだありません。"))
    games_html = ("\n".join(_article_card(a) for a in games) if games
                  else _empty_note("ゲーム記事は近日公開。新作・セール・eスポーツの注目タイトルを追って掲載します。"))
    devices_html = ("\n".join(_article_card(a) for a in devices) if devices
                    else _empty_note("デバイス記事は近日公開。ゲーミングデバイスの新作・予約・ベンチマーク情報を追って掲載します。"))
    articles_html = ("\n".join(_article_card(a) for a in articles) if articles
                     else _empty_note("記事がまだありません。"))

    html = _replace_region(index_html, "TRENDING", trending_html)
    html = _replace_region(html, "GAMES", games_html)
    html = _replace_region(html, "DEVICES", devices_html)
    html = _replace_region(html, "ARTICLES", articles_html)
    # 公式Xリンク（config.X_HANDLE 変更時に次回publishで自動反映）
    html = _replace_region(html, "XNAV", x_link_html(show_handle=False))
    html = _replace_region(html, "XLINK", x_link_html(show_handle=True))
    return html


def inject_articles(index_html: str, articles: list[dict]) -> str:
    """後方互換: ARTICLES領域のみ差し替え（現在は inject_homepage を使う）。"""
    cards = "\n".join(_article_card(a) for a in articles) if articles else ""
    return _replace_region(index_html, "ARTICLES", cards)


# ============================================
# sitemap.xml / robots.txt の自動生成
# ============================================
def write_sitemap(site_dir: str, base_url: str) -> None:
    """
    site/index.html と site/articles/*.html を列挙して sitemap.xml と robots.txt を
    site_dir 直下に書き出す。publish()のたびに呼ばれ、記事が増えるたびに自動更新される。
    lastmod は各ファイルの更新日時(YYYY-MM-DD)。
    """
    base = (base_url or "").rstrip("/")
    entries: list[tuple[str, str]] = []

    index_path = os.path.join(site_dir, "index.html")
    if os.path.isfile(index_path):
        lastmod = datetime.fromtimestamp(os.path.getmtime(index_path)).strftime("%Y-%m-%d")
        entries.append((f"{base}/", lastmod))

    # site直下の特定ページ（テンプレ類は含めない明示リスト方式）
    for name in ("deals.html",):
        path = os.path.join(site_dir, name)
        if os.path.isfile(path):
            lastmod = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d")
            entries.append((f"{base}/{name}", lastmod))

    articles_dir = os.path.join(site_dir, config.ARTICLES_SUBDIR)
    if os.path.isdir(articles_dir):
        for name in sorted(os.listdir(articles_dir)):
            if not name.endswith(".html"):
                continue
            path = os.path.join(articles_dir, name)
            lastmod = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d")
            entries.append((f"{base}/{config.ARTICLES_SUBDIR}/{name}", lastmod))

    urls_xml = "\n".join(
        f"  <url>\n    <loc>{_esc(u)}</loc>\n    <lastmod>{lm}</lastmod>\n  </url>"
        for u, lm in entries
    )
    sitemap = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{urls_xml}\n"
        "</urlset>\n"
    )
    with open(os.path.join(site_dir, "sitemap.xml"), "w", encoding="utf-8") as f:
        f.write(sitemap)

    robots = "User-agent: *\nAllow: /\nSitemap: {}/sitemap.xml\n".format(base)
    with open(os.path.join(site_dir, "robots.txt"), "w", encoding="utf-8") as f:
        f.write(robots)


# ============================================
# X投稿の文面を組み立てる（2ステップ・ポスト）
# ============================================
def _fit_reply(lead: str, url: str, max_weight: int) -> str:
    """リプ用テキスト（誘導文＋URL）を280に収める。超えたら誘導文を短縮。"""
    lead = (lead or "詳しくは記事にまとめています👇").strip()
    while True:
        text = (lead + ("\n" + url if url else "")).strip()
        if weighted_len(text) <= max_weight or len(lead) <= 8:
            return text
        lead = lead[: max(8, len(lead) - 4)].rstrip("　 、。")


def build_x_thread(article: dict, url: str, max_weight: int = 280,
                   linkless: bool = False) -> dict:
    """
    「2ステップ・ポスト」を組み立てる。
      main : 親ポスト（フック。リンクを貼らずインプレッションを稼ぐ。画像は別途添付）
      reply: リプ欄に貼る記事リンク付き投稿（関心を持った読者だけを誘導）
    Xはリンク付き投稿の表示を下げるため、URLは reply 側だけに置く。
    戻り値: {main, reply, main_weight, reply_weight, linkless}

    linkless=True のときは検索ban対策の「リンク無し・単発ポスト」モード。
    誘導文もリプ（記事リンク）も付けず、本文＋ハッシュタグだけの1投稿にする。
    """
    if linkless:
        url = ""  # 誘導文（guide）が付かなくなる＋リプにURLが入らなくなる

    # --- 親ポスト: x_main（無ければlead）＋ハッシュタグ。URLは絶対に入れない ---
    base = (article.get("x_main") or article.get("lead") or "").strip()
    base = _URL_RE.sub("", base).strip()  # 念のためAIが混入させたURLを除去
    tags = " ".join(f"#{str(t).lstrip('#').strip()}"
                    for t in article.get("hashtags", []) if str(t).strip())

    # リプ(2つ目)へ誘導する一文。URL(=リプに記事リンクがある)時のみ付ける。毎回ランダムで変える。
    guide = random.choice(_REPLY_GUIDES)

    def assemble_main(with_tags: bool, with_guide: bool = True) -> str:
        parts = [base]
        if with_guide and url:
            parts.append("\n\n" + guide)
        if with_tags and tags:
            parts.append("\n" + tags)
        return "".join(parts).strip()

    main = assemble_main(True)
    if weighted_len(main) > max_weight:      # 超えたらまずタグを外す（誘導文は残す）
        main = assemble_main(False)
    if weighted_len(main) > max_weight:      # まだ超えるなら本文を末尾から詰める（誘導文は残す）
        while weighted_len(main) > max_weight and len(base) > 20:
            base = base[:-4]
            main = assemble_main(False)

    # --- リプ: 誘導文＋記事URL（linkless時はリプ自体を作らない） ---
    reply = "" if linkless else _fit_reply(article.get("x_reply", ""), url, max_weight)

    return {
        "main": main,
        "reply": reply,
        "main_weight": weighted_len(main),
        "reply_weight": weighted_len(reply),
        "linkless": linkless,
    }


# 旧: 1ポストに要約＋リンクをまとめる版（後方互換のため残置）
def build_x_post(article: dict, url: str, max_weight: int = 280) -> str:
    """
    記事の要約(x_post)＋ハッシュタグ＋記事URLでX投稿文面を作る。
    280(全角2換算)を超える場合はタグを外して収める。
    url が空（未公開）のときはURL無しで返す。
    """
    base = (article.get("x_post") or article.get("lead") or "").strip()
    tags = " ".join(f"#{str(t).lstrip('#').strip()}"
                    for t in article.get("hashtags", []) if str(t).strip())

    def assemble(with_tags: bool) -> str:
        lines = [base]
        if with_tags and tags:
            lines.append(tags)
        if url:
            lines.append(url)
        return "\n".join(x for x in lines if x).strip()

    text = assemble(True)
    if weighted_len(text) > max_weight:
        text = assemble(False)
    return text
