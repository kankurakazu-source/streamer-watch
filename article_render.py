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
from datetime import datetime, timedelta, timezone

import config
from game_analyzer import weighted_len

STEAM_CDN = "https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg"
_URL_RE = re.compile(r"https?://\S+")
_JST = timezone(timedelta(hours=9))

# 買い時判定(verdict)→バッジ色クラス。deals_tracker.py の _VERDICT_CLASS と同じ対応
# （ここでは他モジュールをimportせず、レンダリング専用の小さなdictとして持つ）。
_DEAL_VERDICT_CLASS = {
    "買い時": "buy", "セール中": "sale",
    "セール中・計測中": "watch", "計測中": "watch", "待ち": "wait",
}

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
  .hero{width:100%;height:320px;border-radius:16px;background:var(--bg2) center/cover;overflow:hidden;
    box-shadow:var(--shadow);border:1px solid var(--line-soft);}
  .hero img{width:100%;height:100%;object-fit:cover;display:block;border-radius:inherit;}
  .lead{font-size:17px;margin:24px 0;color:#dfe7f1;line-height:1.95;}
  .tldr{background:linear-gradient(135deg,rgba(57,216,255,.08),rgba(138,107,255,.08));
    border:1px solid rgba(57,216,255,.28);border-radius:14px;padding:16px 20px;margin:24px 0;}
  .tldr .lbl{font-size:11px;font-weight:800;letter-spacing:.14em;text-transform:uppercase;
    color:var(--accent);}
  .tldr .body{font-size:15.5px;margin-top:6px;color:var(--text);}
  /* 目次(TOC): tldrボックスと調和する控えめなカード */
  .toc{background:rgba(255,255,255,.02);border:1px solid var(--line-soft);border-radius:14px;
    padding:16px 20px;margin:24px 0;}
  .toc .lbl{font-size:11px;font-weight:800;letter-spacing:.14em;text-transform:uppercase;color:var(--accent);}
  .toc ol{margin:10px 0 0;padding-left:22px;}
  .toc li{margin:7px 0;color:var(--muted);}
  .toc li::marker{color:var(--accent);font-weight:800;}
  .toc a{color:var(--text);}
  .toc a:hover{color:var(--accent);}
  h2.g{font-size:22px;margin:38px 0 8px;padding-top:20px;border-top:1px solid var(--line-soft);
    font-weight:800;letter-spacing:.01em;}
  .gimg{width:100%;height:210px;border-radius:14px;background:var(--bg2) center/cover;margin:14px 0;
    overflow:hidden;box-shadow:var(--shadow);border:1px solid var(--line-soft);}
  .gimg img{width:100%;height:100%;object-fit:cover;display:block;border-radius:inherit;}
  p{margin:14px 0;color:#d7e0ec;}
  .buybox{margin:20px 0;border:1px solid rgba(255,106,61,.28);border-radius:16px;padding:18px;
    background:linear-gradient(135deg,rgba(255,106,61,.08),rgba(255,45,110,.05));box-shadow:var(--shadow);}
  .buybox .bt{display:flex;gap:14px;align-items:center;}
  .buybox .th{width:130px;height:62px;border-radius:10px;background:var(--bg2) center/cover;flex:0 0 auto;
    overflow:hidden;border:1px solid var(--line-soft);}
  .buybox .th img{width:100%;height:100%;object-fit:cover;display:block;border-radius:inherit;}
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
  /* 割引履歴（買い時データ） */
  .dealhist{display:flex;flex-wrap:wrap;gap:6px 16px;align-items:center;margin-top:14px;padding-top:12px;border-top:1px dashed rgba(255,255,255,.09);font-size:12px;color:var(--muted);}
  .dealhist .k{color:var(--dim);margin-right:4px;}
  .dh-badge{font-size:11px;font-weight:800;padding:2px 10px;border-radius:100px;}
  .dh-badge.buy{background:rgba(47,210,126,.16);color:var(--green);border:1px solid rgba(47,210,126,.35);}
  .dh-badge.sale{background:rgba(255,106,61,.16);color:var(--sale);border:1px solid rgba(255,106,61,.35);}
  .dh-badge.watch{background:rgba(90,168,255,.16);color:#5aa8ff;border:1px solid rgba(90,168,255,.35);}
  .dh-badge.wait{background:rgba(255,255,255,.06);color:var(--dim);border:1px solid var(--line);}
  .dh-link{font-size:11.5px;font-weight:700;color:var(--accent);}
  /* 同接推移グラフ */
  .chart{margin:24px 0;margin-left:0;margin-right:0;}
  .chart svg{display:block;width:100%;height:auto;background:var(--bg2);border:1px solid var(--line-soft);border-radius:14px;}
  .chart figcaption{font-size:11.5px;color:var(--dim);margin-top:8px;text-align:center;}
  /* セクションのスペック表 */
  .spec{width:100%;border-collapse:collapse;margin:16px 0;font-size:13.5px;background:var(--card);border:1px solid var(--line-soft);border-radius:12px;overflow:hidden;}
  .spec th{width:34%;text-align:left;font-weight:700;color:var(--muted);background:rgba(255,255,255,.03);padding:10px 14px;border-bottom:1px solid var(--line-soft);vertical-align:top;}
  .spec td{padding:10px 14px;border-bottom:1px solid var(--line-soft);color:#d7e0ec;}
  .spec tr:last-child th,.spec tr:last-child td{border-bottom:none;}
  /* FAQ */
  .faq{display:flex;flex-direction:column;gap:12px;margin:16px 0;}
  .faq .qa{background:var(--card);border:1px solid var(--line-soft);border-radius:12px;padding:14px 18px;}
  .faq .q{font-weight:800;font-size:14.5px;color:var(--text);}
  .faq .q::before{content:"Q. ";color:var(--accent);}
  .faq .a{margin-top:6px;font-size:13.5px;color:var(--muted);line-height:1.85;}
  .faq .a::before{content:"A. ";color:var(--sale);font-weight:800;}
  .pr-note{font-size:11.5px;color:var(--dim);margin:0 0 20px;padding:8px 12px;
    border:1px solid var(--line-soft);border-radius:8px;background:rgba(255,255,255,.02);line-height:1.7;}
  .back{display:inline-flex;align-items:center;gap:6px;margin:30px 0 0;font-size:14px;font-weight:700;color:var(--accent);}
  .back:hover{gap:10px;transition:gap .15s;}
  footer{margin-top:46px;border-top:1px solid var(--line-soft);padding:26px 0 44px;color:var(--dim);font-size:12px;
    background:linear-gradient(180deg,transparent,rgba(138,107,255,.04));}
  footer .disc{max-width:720px;margin:0 auto 14px;background:var(--card);border:1px solid var(--line-soft);
    border-radius:12px;padding:14px 16px;color:var(--muted);line-height:1.75;}
  footer .flinks{max-width:720px;margin:0 auto 12px;font-size:11.5px;color:var(--dim);}
  footer .flinks a{color:var(--dim);text-decoration:underline;text-underline-offset:2px;}
  footer .flinks a:hover{color:var(--accent);}
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
  .acard .th{width:120px;height:82px;flex:0 0 auto;border-radius:11px;background:var(--bg2) center/cover no-repeat;overflow:hidden;transition:filter .22s;}
  .acard .th img{width:100%;height:100%;object-fit:cover;display:block;border-radius:inherit;}
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
    .grid{grid-template-columns:1fr;gap:13px;}.acard .th{width:110px;height:74px;}
    .spec{font-size:12.5px;}.spec th{width:40%;padding:8px 10px;}.spec td{padding:8px 10px;}}
"""

_PAGE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<script>if(location.hostname.endsWith('.pages.dev')){{location.replace('https://gadgegame.com'+location.pathname+location.search+location.hash);}}</script>
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
  <nav><a href="../index.html">トップ</a><a href="../archive.html">全記事</a><a href="../deals.html">🔥セール・買い時</a>{x_nav}</nav>
</div></header>

<main class="wrap">
  <article class="article">
    <div class="crumb"><a href="../index.html">ホーム</a> › {category}</div>
    <span class="cat">{category}</span>
    <h1>{title}</h1>
    <div class="meta">{date} ・ 文: <a href="../about.html">ガジェゲ編集部</a></div>
    <div class="pr-note">※本記事にはアフィリエイト広告（プロモーション）が含まれます。</div>
    {hero}
    <p class="lead">{lead}</p>
    {tldr}
    {toc}
    {body}
    <a class="back" href="../index.html">← トップに戻る</a>
  </article>
{share}
{related}
  <footer>
    <div class="disc">当サイトはアフィリエイトプログラム（Amazonアソシエイト等）を利用し、商品の紹介で収益を得ることがあります。価格・割引はSteam等の公開情報を基にした参考値です。掲載時点の情報のため、最新の価格は各ストアでご確認ください。Amazonのアソシエイトとして、当メディアは適格販売により収入を得ています。</div>
    <div class="social">{x_footer}</div>
    <div class="flinks"><a href="../about.html">運営者情報</a> ・ <a href="../privacy.html">プライバシーポリシー</a></div>
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


def _fmt_count(v: int) -> str:
    """人数表記: 1万以上は「12.3万」形式、未満は3桁カンマ区切り。"""
    if v >= 10000:
        return f"{v / 10000:.1f}万"
    return f"{v:,}"


def _to_jst(dt: datetime) -> datetime:
    """tzなし(UTC想定)/tzありのdatetimeをJSTに変換する。"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_JST)


def _player_chart_svg(chart: dict) -> str:
    """
    同接推移の折れ線グラフ(インラインSVG)を組み立てる。
    chart: {"name": str, "points": [{"t": ISO8601文字列(UTC想定), "v": int}, ...]}
    有効点(t,vが両方解釈できるもの)を"t"昇順にソートし、2点未満なら空文字を返す。
    """
    chart = chart or {}
    name = chart.get("name", "")
    pts = []
    for p in (chart.get("points") or []):
        t = (p or {}).get("t")
        v = (p or {}).get("v")
        if t is None or v is None:
            continue
        try:
            dt = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
            v = int(v)
        except (ValueError, TypeError):
            continue
        pts.append((dt, v))
    pts.sort(key=lambda x: x[0])
    if len(pts) < 2:
        return ""

    w, h = 640, 240
    ml, mr, mt, mb = 56, 14, 14, 30
    plot_w = w - ml - mr
    plot_h = h - mt - mb
    y_bottom = mt + plot_h

    vmax = max(v for _, v in pts) * 1.08
    if vmax <= 0:
        vmax = 1

    n = len(pts)

    def x_at(i: int) -> float:
        return ml + (plot_w * i / (n - 1) if n > 1 else 0)

    def y_at(v: float) -> float:
        return mt + plot_h - (plot_h * v / vmax)

    # 横グリッド線3本(0/中間/最大)
    grid_parts = []
    for frac in (0.0, 0.5, 1.0):
        gy = mt + plot_h - (plot_h * frac)
        grid_parts.append(
            f'<line x1="{ml}" y1="{gy:.1f}" x2="{w - mr}" y2="{gy:.1f}" '
            f'stroke="#243046" stroke-width="1"/>'
        )
        grid_parts.append(
            f'<text x="{ml - 8}" y="{gy:.1f}" text-anchor="end" dominant-baseline="middle" '
            f'font-size="11" fill="var(--dim)">{_fmt_count(round(vmax * frac))}</text>'
        )

    # x軸ラベル(最初・中間・最後の3点)
    mid_i = (n - 1) // 2
    label_specs = [(0, "start"), (mid_i, "middle"), (n - 1, "end")]
    x_labels = []
    seen_i = set()
    for i, anchor in label_specs:
        if i in seen_i:
            continue
        seen_i.add(i)
        jst = _to_jst(pts[i][0])
        x_labels.append(
            f'<text x="{x_at(i):.1f}" y="{h - 8}" text-anchor="{anchor}" '
            f'font-size="11" fill="var(--dim)">{jst.month}/{jst.day}</text>'
        )

    line_pts = " ".join(f"{x_at(i):.1f},{y_at(v):.1f}" for i, (_, v) in enumerate(pts))
    area_pts = f"{line_pts} {x_at(n - 1):.1f},{y_bottom:.1f} {x_at(0):.1f},{y_bottom:.1f}"
    last_x, last_v = x_at(n - 1), pts[-1][1]

    svg = (
        f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg">'
        + "".join(grid_parts)
        + "".join(x_labels)
        + f'<polygon points="{area_pts}" fill="rgba(57,216,255,.08)"/>'
        + f'<polyline points="{line_pts}" fill="none" stroke="var(--accent)" '
          f'stroke-width="2.5" stroke-linejoin="round"/>'
        + f'<circle cx="{last_x:.1f}" cy="{y_at(last_v):.1f}" r="4" fill="var(--accent)"/>'
        + "</svg>"
    )
    return (
        '<figure class="chart">'
        + svg
        + f'<figcaption>「{_esc(name)}」のSteam同接推移（直近14日・当サイト計測）</figcaption>'
        + "</figure>"
    )


def _paragraphs(body: str) -> str:
    """本文文字列を段落<p>に変換（空行 or 改行で分割）。"""
    chunks = [c.strip() for c in re.split(r"\n\s*\n|\n", body or "") if c.strip()]
    return "".join(f"<p>{_esc(c)}</p>" for c in chunks)


def _dealhist_html(deal: dict) -> str:
    """
    購入ボックスに添える割引履歴(買い時データ)。deal:
    {current_discount, max_discount, last_sale_date, verdict, tracked_days}。
    価格(円)は出さず、割引率と日付のみ表示する。deal未指定なら空文字。
    """
    if not deal:
        return ""
    cur = int(deal.get("current_discount") or 0)
    mx = int(deal.get("max_discount") or 0)
    last = deal.get("last_sale_date")
    verdict = deal.get("verdict", "")
    tracked = int(deal.get("tracked_days") or 0)

    spans = []
    if cur > 0:
        spans.append(f'<span><span class="k">現在</span>-{cur}%</span>')
    else:
        spans.append('<span><span class="k">現在</span>セールなし</span>')
    if mx > 0:
        spans.append(f'<span><span class="k">過去最大</span>-{mx}%（計測{tracked}日）</span>')
    else:
        spans.append('<span><span class="k">過去最大</span>計測期間内セールなし</span>')
    if last:
        spans.append(f'<span><span class="k">直近セール</span>{_esc(last)}</span>')
    cls = _DEAL_VERDICT_CLASS.get(verdict, "wait")
    spans.append(f'<span class="dh-badge {cls}">{_esc(verdict)}</span>')
    spans.append('<a class="dh-link" href="/deals.html">買い時トラッカー →</a>')
    return '<div class="dealhist">' + "".join(spans) + "</div>"


def _buybox(game: dict) -> str:
    """1タイトル分の購入ボックス。game: {name, image_url, discount_percent?, deal?}。"""
    name = game.get("name", "")
    if not name:
        return ""
    links = affiliate_links(name)
    img = game.get("image_url", "")
    th = (f"<div class='th'><img src=\"{_esc(img)}\" alt=\"{_esc(name)}\" loading=\"lazy\"></div>"
          if img else "<div class='th'></div>")
    dp = game.get("discount_percent")
    off = f"<div class='off'>-{int(dp)}% セール中</div>" if dp else ""
    # Steamゲーム(appidあり)は公式ストアへの導線を先頭に付ける（アフィリ制度は無いのでUX目的）
    appid = game.get("appid")
    steam_btn = (f"<a class=\"buy steam\" href=\"https://store.steampowered.com/app/{int(appid)}/\" "
                 f"target=\"_blank\" rel=\"nofollow noopener\">Steamで見る</a>\n        "
                 if appid else "")
    dealhist_html = _dealhist_html(game.get("deal") or {})
    return f"""<div class="buybox">
      <div class="bt">{th}
        <div style="flex:1">
          <div class="name">{_esc(name)}</div>{off}
        </div>
      </div>
      {dealhist_html}
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


def _breadcrumb_jsonld(category: str, title: str, canonical: str) -> str:
    """
    パンくず(ホーム→カテゴリ→記事)のBreadcrumbList JSON-LD。
    カテゴリがCATEGORY_SLUGSに無い場合はカテゴリ階層を省き、ホーム→記事の2階層にする。
    base_url(config.SITE_BASE_URL)が無ければURLが組めないため空文字を返す（＝出力しない）。
    """
    base = (config.SITE_BASE_URL or "").rstrip("/")
    if not base:
        return ""
    items = [{"@type": "ListItem", "position": 1, "name": "ホーム", "item": f"{base}/"}]
    slug = CATEGORY_SLUGS.get(category or "", "")
    pos = 2
    if slug:
        items.append({"@type": "ListItem", "position": pos, "name": category,
                       "item": f"{base}/category/{slug}"})
        pos += 1
    items.append({"@type": "ListItem", "position": pos, "name": title, "item": canonical or f"{base}/"})
    data = {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": items}
    return f'<script type="application/ld+json">{json.dumps(data, ensure_ascii=False)}</script>'


def _spec_table(rows: list) -> str:
    """
    セクションのスペック表。rows: [{"label": str, "value": str}, ...]。
    label・valueが両方非空の行だけ採用する。有効行が0件なら空文字。
    """
    trs = []
    for r in rows or []:
        label = str((r or {}).get("label", "")).strip()
        value = str((r or {}).get("value", "")).strip()
        if not label or not value:
            continue
        trs.append(f"<tr><th>{_esc(label)}</th><td>{_esc(value)}</td></tr>")
    if not trs:
        return ""
    return '<table class="spec"><tbody>' + "".join(trs) + "</tbody></table>"


def _filter_faq(faq: list) -> list[tuple[str, str]]:
    """faqリストのうちq・aが両方非空の項目だけを(q, a)タプルで返す。"""
    out = []
    for item in faq or []:
        q = str((item or {}).get("q", "")).strip()
        a = str((item or {}).get("a", "")).strip()
        if q and a:
            out.append((q, a))
    return out


def _faq_html(faq: list) -> str:
    """よくある質問セクション。有効項目が0件なら空文字。"""
    items = _filter_faq(faq)
    if not items:
        return ""
    qa = "".join(
        f"<div class='qa'><div class='q'>{_esc(q)}</div><div class='a'>{_esc(a)}</div></div>"
        for q, a in items
    )
    return f"<h2 class='g'>よくある質問</h2>\n<div class='faq'>{qa}</div>"


def _faq_jsonld(faq: list) -> str:
    """FAQのFAQPage構造化データ(JSON-LD)。有効項目が0件なら空文字。"""
    items = _filter_faq(faq)
    if not items:
        return ""
    data = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": q,
             "acceptedAnswer": {"@type": "Answer", "text": a}}
            for q, a in items
        ],
    }
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
    title = article.get("title", "")
    hero_url = article.get("hero_image_url", "")
    hero = (f"<div class='hero'><img src=\"{_esc(hero_url)}\" alt=\"{_esc(title)}のメインビジュアル\" "
            f"fetchpriority=\"high\"></div>" if hero_url else "")
    tldr_txt = (article.get("tldr") or "").strip()
    tldr = (f"<div class='tldr'><div class='lbl'>結論</div>"
            f"<div class='body'>{_esc(tldr_txt)}</div></div>" if tldr_txt else "")

    # 目次(TOC)用: セクション見出しに連番アンカー(sec-1, sec-2, ...)を割り当てる。
    # まとめ見出しは対象外（セクション数のカウントにも含めない）。
    sections = article.get("sections", []) or []
    anchor_ids = []  # sections と同じ順番。見出し無しは None
    toc_entries = []  # [(anchor_id, heading), ...]
    idx = 0
    for sec in sections:
        heading = sec.get("heading", "")
        if heading:
            idx += 1
            anchor_ids.append(f"sec-{idx}")
            toc_entries.append((f"sec-{idx}", heading))
        else:
            anchor_ids.append(None)

    parts = []
    chart_html = _player_chart_svg(article.get("player_chart") or {})
    if chart_html:
        parts.append(chart_html)
    for sec, anchor in zip(sections, anchor_ids):
        heading = sec.get("heading", "")
        if heading:
            id_attr = f" id='{anchor}'" if anchor else ""
            parts.append(f"<h2 class='g'{id_attr}>{_esc(heading)}</h2>")
        buy = sec.get("buy") or {}
        # セクションのバナー画像は使い回しを避けた sec['image_url'] を優先（無ければbuyの画像）
        sec_img = sec.get("image_url") or buy.get("image_url", "")
        if sec_img:
            img_alt = heading or title
            parts.append(f"<div class='gimg'><img src=\"{_esc(sec_img)}\" alt=\"{_esc(img_alt)}\" "
                         f"loading=\"lazy\"></div>")
        parts.append(_paragraphs(sec.get("body", "")))
        spec_html = _spec_table(sec.get("spec_table") or [])
        if spec_html:
            parts.append(spec_html)
        if buy.get("name"):
            parts.append(_buybox(buy))

    conclusion = (article.get("conclusion") or "").strip()
    if conclusion:
        parts.append("<h2 class='g'>まとめ</h2>")
        parts.append(_paragraphs(conclusion))

    faq_html = _faq_html(article.get("faq") or [])
    if faq_html:
        parts.append(faq_html)

    # 目次はセクションが3個以上の時だけ表示（2個以下は不要）
    toc_html = ""
    if len(toc_entries) >= 3:
        items = "".join(f'<li><a href="#{aid}">{_esc(h)}</a></li>' for aid, h in toc_entries)
        toc_html = f"<nav class='toc'><div class='lbl'>目次</div><ol>{items}</ol></nav>"

    # OGP用メタ: 説明文はlead(無ければtldr)を160字程度に、画像はhero(無ければサイト共通OG画像)
    base_url = (config.SITE_BASE_URL or "").rstrip("/")
    og_desc = _esc((article.get("lead") or article.get("tldr") or "").strip()[:160])
    og_image = hero_url or (f"{base_url}/ogp.png" if base_url else "")
    canonical = (article.get("canonical_url") or "").strip()
    canonical_tag = f'<link rel="canonical" href="{_esc(canonical)}">' if canonical else ""
    og_url_tag = f'<meta property="og:url" content="{_esc(canonical)}">' if canonical else ""
    category = article.get("category", "")
    jsonld = (_article_jsonld(article, canonical, hero_url, now)
              + _breadcrumb_jsonld(category, title, canonical)
              + _faq_jsonld(article.get("faq") or []))

    # 記事末尾の「人気の記事」（自分自身は除外）
    cur_slug = ""
    if canonical:
        cur_slug = canonical.rstrip("/").split("/")[-1].removesuffix(".html")
    related_html = related_section(related or [], current_slug=cur_slug)
    share_html = share_section(canonical, title)

    return _PAGE.format(
        title=_esc(title),
        css=_CSS,
        category=_esc(category),
        date=now.strftime("%Y/%m/%d"),
        hero=hero,
        lead=_esc(article.get("lead", "")),
        tldr=tldr,
        toc=toc_html,
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
        th = f"<div class=\"th\"><img src=\"{_esc(img)}\" alt=\"{_esc(a.get('title',''))}\" loading=\"lazy\"></div>"
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
# 全記事アーカイブ／カテゴリ別ページ
# ============================================
# カテゴリ名(日本語) -> URLスラッグ。site/category/<slug>.html のファイル名に使う。
CATEGORY_SLUGS = {
    "セール分析": "sale",
    "注目株": "rising",
    "新作": "new",
    "eスポーツ": "esports",
    "デバイス": "device",
    "データ分析": "data",
    "考察": "column",
    "ガイド": "guide",
}

_ARCHIVE_PER_PAGE = 20

# アーカイブ/カテゴリ一覧ページ用CSS。カードグリッド(acard)は記事ページ(_CSS)と同じ見た目、
# ヒーロー/ナビはdeals.html系のレイアウト(max-width 1120)に合わせる。
_LISTING_CSS = """
  :root{
    --bg:#0a0e16; --bg2:#0c1220; --card:#121a29; --card-hi:#172135; --line:#243046; --line-soft:#1a2334;
    --text:#eef2f8; --muted:#9db0c6; --dim:#647689;
    --accent:#39d8ff; --accent-2:#8a6bff; --sale:#ff6a3d; --green:#2fd27e; --violet:#a78bfa;
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
  .hero .wrap{padding:44px 22px 36px;}
  .kicker{display:inline-flex;align-items:center;gap:8px;font-size:12px;font-weight:700;letter-spacing:.16em;
    color:var(--accent);text-transform:uppercase;margin-bottom:16px;}
  .kicker::before{content:"";width:26px;height:2px;background:var(--grad);border-radius:2px;}
  .hero h1{font-size:32px;line-height:1.3;margin:0 0 12px;font-weight:800;letter-spacing:.005em;max-width:780px;}
  .hero h1 .c{background:var(--grad);-webkit-background-clip:text;background-clip:text;color:transparent;}
  .hero p{margin:0;color:var(--muted);font-size:15px;max-width:640px;line-height:1.85;}

  main{padding:34px 0 10px;}
  .grid{display:grid;gap:16px;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));}
  .acard{position:relative;display:flex;gap:15px;background:var(--card);border:1px solid var(--line-soft);
    border-radius:16px;padding:14px;overflow:hidden;transition:transform .22s,border-color .22s,box-shadow .22s,background .22s;}
  .acard:hover{transform:translateY(-4px);background:var(--card-hi);border-color:rgba(57,216,255,.5);
    box-shadow:var(--shadow),0 8px 34px rgba(57,216,255,.16);}
  .acard .th{width:120px;height:82px;flex:0 0 auto;border-radius:11px;background:var(--bg2) center/cover no-repeat;overflow:hidden;transition:filter .22s;}
  .acard .th img{width:100%;height:100%;object-fit:cover;display:block;border-radius:inherit;}
  .acard:hover .th{filter:brightness(1.08) saturate(1.05);}
  .acard .th.noimg{background:radial-gradient(120px 80px at 70% 20%,rgba(138,107,255,.35),transparent 60%),linear-gradient(135deg,#182236,#10182a);position:relative;}
  .acard .th.noimg::after{content:"GADGET";position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:var(--dim);font-size:10px;font-weight:800;letter-spacing:.22em;}
  .acbody{min-width:0;display:flex;flex-direction:column;}
  .acmeta{display:flex;align-items:center;gap:7px;margin-bottom:7px;flex-wrap:wrap;}
  .cat{display:inline-block;font-size:10.5px;font-weight:800;padding:3px 10px;border-radius:100px;letter-spacing:.02em;
    background:rgba(57,216,255,.14);color:var(--accent);border:1px solid rgba(57,216,255,.25);}
  .cat.o{background:rgba(255,106,61,.15);color:var(--sale);border-color:rgba(255,106,61,.3);}
  .cat.g{background:rgba(47,210,126,.15);color:var(--green);border-color:rgba(47,210,126,.3);}
  .cat.p{background:rgba(167,139,250,.16);color:var(--violet);border-color:rgba(167,139,250,.3);}
  .pill-break{font-size:10.5px;font-weight:800;letter-spacing:.04em;padding:3px 9px;border-radius:100px;color:#fff;
    background:linear-gradient(135deg,#ff6a3d,#ff2d6e);box-shadow:0 0 14px rgba(255,45,110,.45);}
  .acard .ttl{font-size:15px;font-weight:700;margin:0 0 6px;line-height:1.5;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;}
  .acard:hover .ttl{color:#fff;}
  .acard .ex{font-size:12px;color:var(--muted);line-height:1.65;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;}
  .acard .d{font-size:11px;color:var(--dim);margin-top:auto;padding-top:8px;}
  .rank{position:absolute;top:8px;left:8px;z-index:2;min-width:26px;height:26px;padding:0 6px;display:flex;align-items:center;justify-content:center;
    font-weight:800;font-size:14px;color:#07101c;background:var(--grad);border-radius:8px;box-shadow:0 4px 14px rgba(57,216,255,.4);}

  .empty{grid-column:1/-1;color:var(--muted);font-size:13.5px;padding:26px 18px;text-align:center;
    background:var(--card);border:1px dashed var(--line);border-radius:14px;}

  .pager{display:flex;align-items:center;justify-content:center;gap:14px;margin:34px 0 6px;flex-wrap:wrap;}
  .pager .pg{font-size:13px;font-weight:700;color:var(--muted);padding:8px 14px;border-radius:10px;
    border:1px solid var(--line);transition:color .15s,border-color .15s;}
  .pager a.pg:hover{color:var(--text);border-color:var(--accent);}
  .pager .pg.disabled{opacity:.35;pointer-events:none;}
  .pager .pg-nums{display:flex;gap:6px;}
  .pager .pg-num{font-size:13px;font-weight:700;color:var(--muted);width:32px;height:32px;display:inline-flex;
    align-items:center;justify-content:center;border-radius:9px;border:1px solid var(--line);}
  .pager a.pg-num:hover{color:var(--text);border-color:var(--accent);}
  .pager .pg-num.on{background:var(--grad);color:#07101c;border-color:transparent;}

  footer{margin-top:44px;border-top:1px solid var(--line-soft);padding:26px 0 46px;color:var(--dim);font-size:12px;
    background:linear-gradient(180deg,transparent,rgba(138,107,255,.04));}
  footer .disc{background:var(--card);border:1px solid var(--line-soft);border-radius:12px;padding:14px 16px;
    margin-bottom:16px;color:var(--muted);line-height:1.75;}
  footer .flinks{font-size:11.5px;color:var(--dim);margin-bottom:12px;}
  footer .flinks a{color:var(--dim);text-decoration:underline;text-underline-offset:2px;}
  footer .flinks a:hover{color:var(--accent);}

  @media(max-width:680px){
    .head{height:56px;}
    .hero .wrap{padding:34px 22px 28px;}
    .hero h1{font-size:24px;}
    nav{gap:2px;-webkit-mask-image:linear-gradient(90deg,#000 90%,transparent);mask-image:linear-gradient(90deg,#000 90%,transparent);}
    nav a{padding:8px 10px;font-size:13px;}
    .grid{grid-template-columns:1fr;gap:13px;}
    .acard .th{width:110px;height:74px;}
  }
"""

_LISTING_PAGE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<script>if(location.hostname.endsWith('.pages.dev')){{location.replace('https://gadgegame.com'+location.pathname+location.search+location.hash);}}</script>
<title>{title}</title>
<meta name="description" content="{desc}">
{canonical_tag}
<meta property="og:type" content="website">
<meta property="og:site_name" content="ガジェゲ（Gadget×Game）">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{desc}">
{og_url_tag}
<meta property="og:image" content="{og_image}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{title}">
<meta name="twitter:description" content="{desc}">
<meta name="twitter:image" content="{og_image}">
<script type="application/ld+json">{jsonld}</script>
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32.png">
<link rel="alternate icon" href="/favicon.ico">
<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">
<style>{css}</style>
</head>
<body>
<header><div class="wrap head">
  <a class="logo" href="{base}index.html"><img class="logo-mark" src="/favicon.svg" alt="ガジェゲ" width="30" height="30"><span class="logo-name">ガジェゲ<i class="logo-sub">Gadget×Game</i></span></a>
  <nav>
    {nav}
  </nav>
</div></header>

<section class="hero">
  <div class="wrap">
    <div class="kicker">{kicker}</div>
    <h1>{h1}</h1>
    <p>{lead}</p>
  </div>
</section>

<main class="wrap">
  <div class="grid">
{cards}
  </div>
{pagination}

  <footer>
    <div class="disc">当サイトはアフィリエイトプログラム（Amazonアソシエイト等）を利用し、商品の紹介で収益を得ることがあります。価格・割引はSteam等の公開情報を基にした参考値です。掲載時点の情報のため、最新の価格は各ストアでご確認ください。Amazonのアソシエイトとして、当メディアは適格販売により収入を得ています。</div>
    <div class="social">{x_footer}</div>
    <div class="flinks"><a href="{base}about.html">運営者情報</a> ・ <a href="{base}privacy.html">プライバシーポリシー</a></div>
    (c) {year} ガジェゲ（Gadget×Game） ／ データで見るゲームトレンド
  </footer>
</main>
</body>
</html>
"""


def _listing_nav(base: str, active: str = "") -> str:
    """アーカイブ/カテゴリページ共通のヘッダーnav。base='' (site直下) or '../' (category/配下)。"""
    def cls(name: str) -> str:
        return ' class="on"' if name == active else ""

    items = [
        f'<a href="{base}index.html#trending">いま読まれている</a>',
        f'<a href="{base}index.html#games">ゲーム</a>',
        f'<a href="{base}index.html#devices">デバイス</a>',
        f'<a{cls("archive")} href="{base}archive.html">全記事</a>',
        f'<a href="{base}deals.html">🔥セール・買い時</a>',
    ]
    x = x_link_html(show_handle=False)
    if x:
        items.append(x)
    return "\n    ".join(items)


def _archive_filename(page: int) -> str:
    return "archive.html" if page == 1 else f"archive-{page}.html"


def _pagination_html(page: int, total_pages: int) -> str:
    """前へ/次へ＋ページ番号ナビ。1ページしかなければ空文字。"""
    if total_pages <= 1:
        return ""
    parts = []
    if page > 1:
        parts.append(f'<a class="pg prev" href="{_archive_filename(page - 1)}">← 前へ</a>')
    else:
        parts.append('<span class="pg prev disabled">← 前へ</span>')
    nums = []
    for p in range(1, total_pages + 1):
        if p == page:
            nums.append(f'<span class="pg-num on">{p}</span>')
        else:
            nums.append(f'<a class="pg-num" href="{_archive_filename(p)}">{p}</a>')
    parts.append('<span class="pg-nums">' + "".join(nums) + "</span>")
    if page < total_pages:
        parts.append(f'<a class="pg next" href="{_archive_filename(page + 1)}">次へ →</a>')
    else:
        parts.append('<span class="pg next disabled">次へ →</span>')
    return '  <div class="pager">' + "".join(parts) + "</div>"


def _listing_page_html(*, title: str, description: str, canonical_path: str, kicker: str,
                       h1: str, lead: str, cards_html: str, nav_html: str, base_url: str,
                       base: str = "", pagination_html: str = "") -> str:
    """アーカイブ/カテゴリページ共通のHTML組み立て。h1はマークアップ込みでそのまま埋め込む。"""
    b = (base_url or "").rstrip("/")
    canonical = f"{b}{canonical_path}" if b else ""
    og_image = f"{b}/ogp.png" if b else ""
    canonical_tag = f'<link rel="canonical" href="{_esc(canonical)}">' if canonical else ""
    og_url_tag = f'<meta property="og:url" content="{_esc(canonical)}">' if canonical else ""
    jsonld_data = {
        "@context": "https://schema.org", "@type": "CollectionPage",
        "name": title, "description": description,
    }
    if canonical:
        jsonld_data["url"] = canonical
    jsonld = f'<script type="application/ld+json">{json.dumps(jsonld_data, ensure_ascii=False)}</script>'
    now = datetime.now()
    return _LISTING_PAGE.format(
        title=_esc(title), desc=_esc(description), canonical_tag=canonical_tag,
        og_url_tag=og_url_tag, og_image=_esc(og_image), jsonld=jsonld, css=_LISTING_CSS,
        base=base, nav=nav_html, kicker=_esc(kicker), h1=h1, lead=_esc(lead),
        cards=cards_html, pagination=pagination_html,
        x_footer=x_link_html(show_handle=True), year=now.year,
    )


def render_archive_pages(articles: list[dict], site_dir: str, base_url: str) -> list[str]:
    """
    全記事アーカイブ(新しい順・20件/ページ)を site/archive.html, archive-2.html... に書き出す。
    戻り値: 生成したファイル名のリスト（例: ["archive.html", "archive-2.html"]）。
    """
    ordered = sorted(articles, key=lambda a: a.get("created_at") or "", reverse=True)
    total = len(ordered)
    total_pages = max(1, (total + _ARCHIVE_PER_PAGE - 1) // _ARCHIVE_PER_PAGE)
    written = []
    for page in range(1, total_pages + 1):
        chunk = ordered[(page - 1) * _ARCHIVE_PER_PAGE: page * _ARCHIVE_PER_PAGE]
        cards = ("\n".join(_article_card(a, base="") for a in chunk) if chunk
                 else _empty_note("記事がまだありません。"))
        pagination = _pagination_html(page, total_pages)
        h1 = "全記事アーカイブ" if page == 1 else f"全記事アーカイブ（{page}ページ目）"
        if page == 1:
            lead = f"公開済み全{total}記事を新しい順に掲載。"
        else:
            start = (page - 1) * _ARCHIVE_PER_PAGE + 1
            end = min(page * _ARCHIVE_PER_PAGE, total)
            lead = f"全{total}記事のうち{start}〜{end}件目。"
        title = "記事一覧｜ガジェゲ" if page == 1 else f"記事一覧（{page}ページ目）｜ガジェゲ"
        description = f"ガジェゲの公開済み全{total}記事を新しい順に掲載する記事一覧アーカイブ。"
        filename = _archive_filename(page)
        canonical_path = "/archive" if page == 1 else f"/archive-{page}"
        html_out = _listing_page_html(
            title=title, description=description, canonical_path=canonical_path,
            kicker="Article Archive", h1=h1, lead=lead, cards_html=cards,
            nav_html=_listing_nav("", active="archive"), pagination_html=pagination,
            base="", base_url=base_url,
        )
        out_path = os.path.join(site_dir, filename)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html_out)
        written.append(filename)
    return written


def render_category_pages(articles: list[dict], site_dir: str, base_url: str) -> list[str]:
    """
    カテゴリ別記事一覧を site/category/<slug>.html に全カテゴリ分書き出す(ページネーションなし)。
    記事0件のカテゴリも「まだ記事がありません」の空状態ページとして生成する。
    戻り値: 生成した相対パスのリスト（例: ["category/sale.html", ...]）。
    """
    cat_dir = os.path.join(site_dir, "category")
    os.makedirs(cat_dir, exist_ok=True)
    written = []
    for cat_name, slug in CATEGORY_SLUGS.items():
        matched = sorted(
            [a for a in articles if a.get("category") == cat_name],
            key=lambda a: a.get("created_at") or "", reverse=True,
        )
        cards = ("\n".join(_article_card(a, base="../") for a in matched) if matched
                 else _empty_note("まだ記事がありません。毎日自動更新中。"))
        title = f"{cat_name}の記事一覧｜ガジェゲ"
        description = f"ガジェゲの「{cat_name}」カテゴリの記事一覧（{len(matched)}件）。"
        h1 = f'{_esc(cat_name)}<span class="c">の記事</span>'
        lead = f"「{cat_name}」カテゴリの記事を新しい順に掲載（{len(matched)}件）。"
        html_out = _listing_page_html(
            title=title, description=description, canonical_path=f"/category/{slug}",
            kicker="Category", h1=h1, lead=lead, cards_html=cards,
            nav_html=_listing_nav("../", active=""), base="../", base_url=base_url,
        )
        out_path = os.path.join(cat_dir, f"{slug}.html")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html_out)
        written.append(f"category/{slug}.html")
    return written


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
    # Cloudflare Pagesが.html付きURLを拡張子なしに308リダイレクトするため、locは拡張子なしで登録する。
    for name in ("deals.html", "about.html", "privacy.html"):
        path = os.path.join(site_dir, name)
        if os.path.isfile(path):
            lastmod = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d")
            slug = name.removesuffix(".html")
            entries.append((f"{base}/{slug}", lastmod))

    # 全記事アーカイブ（archive.html, archive-2.html, ...）。存在する連番だけ拾う。
    page = 1
    while True:
        name = "archive.html" if page == 1 else f"archive-{page}.html"
        path = os.path.join(site_dir, name)
        if not os.path.isfile(path):
            break
        lastmod = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d")
        slug = name.removesuffix(".html")
        entries.append((f"{base}/{slug}", lastmod))
        page += 1

    # カテゴリ別ページ（category/*.html）
    category_dir = os.path.join(site_dir, "category")
    if os.path.isdir(category_dir):
        for name in sorted(os.listdir(category_dir)):
            if not name.endswith(".html"):
                continue
            path = os.path.join(category_dir, name)
            lastmod = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d")
            slug = name.removesuffix(".html")
            entries.append((f"{base}/category/{slug}", lastmod))

    articles_dir = os.path.join(site_dir, config.ARTICLES_SUBDIR)
    if os.path.isdir(articles_dir):
        for name in sorted(os.listdir(articles_dir)):
            if not name.endswith(".html"):
                continue
            path = os.path.join(articles_dir, name)
            lastmod = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d")
            slug = name.removesuffix(".html")
            entries.append((f"{base}/{config.ARTICLES_SUBDIR}/{slug}", lastmod))

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
