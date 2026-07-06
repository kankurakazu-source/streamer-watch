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
import re
import urllib.parse
from datetime import datetime

import config
from game_analyzer import weighted_len

STEAM_CDN = "https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg"
_URL_RE = re.compile(r"https?://\S+")

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
    --amazon:#ff9900; --rakuten:#c5121a; --dmm:#f04e23;
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
  .pnote{font-size:11px;color:var(--dim);margin-top:10px;line-height:1.7;}
  .back{display:inline-flex;align-items:center;gap:6px;margin:30px 0 0;font-size:14px;font-weight:700;color:var(--accent);}
  .back:hover{gap:10px;transition:gap .15s;}
  footer{margin-top:46px;border-top:1px solid var(--line-soft);padding:26px 0 44px;color:var(--dim);font-size:12px;
    background:linear-gradient(180deg,transparent,rgba(138,107,255,.04));}
  footer .disc{max-width:720px;margin:0 auto 14px;background:var(--card);border:1px solid var(--line-soft);
    border-radius:12px;padding:14px 16px;color:var(--muted);line-height:1.75;}
  @media(max-width:680px){h1{font-size:24px;}.hero{height:210px;}.buybox .buys{flex-wrap:wrap;}}
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
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32.png">
<link rel="alternate icon" href="/favicon.ico">
<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">
<style>{css}</style>
</head>
<body>
<header><div class="wrap head">
  <a class="logo" href="/"><img class="logo-mark" src="/favicon.svg" alt="ガジェゲ" width="28" height="28"><span class="logo-name">ガジェゲ<i class="logo-sub">Gadget×Game</i></span></a>
  <nav><a href="../index.html">トップ</a>{x_nav}</nav>
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
    return f"""<div class="buybox">
      <div class="bt">{th}
        <div style="flex:1">
          <div class="name">{_esc(name)}</div>{off}
        </div>
      </div>
      <div class="buys">
        <a class="buy amazon" href="{_esc(links['amazon'])}" target="_blank" rel="nofollow noopener">Amazonで見る</a>
        <a class="buy rakuten" href="{_esc(links['rakuten'])}" target="_blank" rel="nofollow noopener">楽天</a>
        <a class="buy dmm" href="{_esc(links['dmm'])}" target="_blank" rel="nofollow noopener">DMM</a>
      </div>
      <div class="pnote">※価格・割引は掲載時点の参考値です。最新の価格は各ストアでご確認ください。当サイトはアフィリエイトリンクを使用しています。</div>
    </div>"""


def render_article(article: dict) -> str:
    """
    article: {
      title, category, lead, tldr, conclusion,
      hero_image_url (str, 任意),
      sections: [ {heading, body, buy:{name,image_url,discount_percent?}(任意)} ]
    }
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
        x_nav=x_link_html(show_handle=False),
        x_footer=x_link_html(show_handle=True),
    )


# ============================================
# トップページ(index.html)の記事一覧を差し替える
# ============================================
_ARTICLES_RE = re.compile(r"(<!--ARTICLES:START-->).*?(<!--ARTICLES:END-->)", re.DOTALL)


def _article_card(a: dict, rank: int | None = None) -> str:
    """
    1記事分の一覧カード(acard)HTML。a: storage.list_articles の1要素。
    rank を渡すと「いま読まれている」用の順位バッジを付ける。
    画像が無い記事（デバイス記事など）は .noimg でプレースホルダ表示にする。
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
    href = f"{config.ARTICLES_SUBDIR}/{a['slug']}.html"
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


def inject_homepage(index_html: str, articles: list[dict]) -> str:
    """
    トップページの3領域を再生成する:
      TRENDING … いま読まれている記事（速報優先＋新着）上位5
      DEVICES  … 注目のデバイス情報（カテゴリ/種別=デバイス）上位6
      ARTICLES … 全記事（カテゴリで絞り込み可能な一覧）
    """
    trending = _sort_trending(articles)[:5]
    devices = [a for a in articles
               if a.get("category") == "デバイス" or a.get("event_type") == "デバイス"][:6]

    trending_html = ("\n".join(_article_card(a, rank=i) for i, a in enumerate(trending, 1))
                     if trending else _empty_note("記事がまだありません。"))
    devices_html = ("\n".join(_article_card(a) for a in devices) if devices
                    else _empty_note("デバイス記事は近日公開。ゲーミングデバイスの新作・予約・ベンチマーク情報を追って掲載します。"))
    articles_html = ("\n".join(_article_card(a) for a in articles) if articles
                     else _empty_note("記事がまだありません。"))

    html = _replace_region(index_html, "TRENDING", trending_html)
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


def build_x_thread(article: dict, url: str, max_weight: int = 280) -> dict:
    """
    「2ステップ・ポスト」を組み立てる。
      main : 親ポスト（フック。リンクを貼らずインプレッションを稼ぐ。画像は別途添付）
      reply: リプ欄に貼る記事リンク付き投稿（関心を持った読者だけを誘導）
    Xはリンク付き投稿の表示を下げるため、URLは reply 側だけに置く。
    戻り値: {main, reply, main_weight, reply_weight}
    """
    # --- 親ポスト: x_main（無ければlead）＋ハッシュタグ。URLは絶対に入れない ---
    base = (article.get("x_main") or article.get("lead") or "").strip()
    base = _URL_RE.sub("", base).strip()  # 念のためAIが混入させたURLを除去
    tags = " ".join(f"#{str(t).lstrip('#').strip()}"
                    for t in article.get("hashtags", []) if str(t).strip())

    def assemble_main(with_tags: bool) -> str:
        parts = [base]
        if with_tags and tags:
            parts.append("\n" + tags)
        return "".join(parts).strip()

    main = assemble_main(True)
    if weighted_len(main) > max_weight:
        main = assemble_main(False)
    if weighted_len(main) > max_weight:  # まだ超えるなら本文を末尾から詰める
        while weighted_len(main) > max_weight and len(base) > 20:
            base = base[:-4]
            main = assemble_main(False)

    # --- リプ: 誘導文＋記事URL ---
    reply = _fit_reply(article.get("x_reply", ""), url, max_weight)

    return {
        "main": main,
        "reply": reply,
        "main_weight": weighted_len(main),
        "reply_weight": weighted_len(reply),
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
