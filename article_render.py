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
    --bg:#f4f6f9; --card:#ffffff; --card2:#e9edf2; --line:#e2e7ee;
    --text:#1a2331; --muted:#5d6b7c; --dim:#9aa6b4;
    --orange:#ef5423; --cyan:#1793c9; --green:#1a9e5a; --purple:#7857d6;
    --amazon:#ff9900; --rakuten:#bf0000; --dmm:#ec4d0f;
    --sh:0 1px 3px rgba(20,30,50,.07);
  }
  *{box-sizing:border-box;}
  body{margin:0;background:var(--bg);color:var(--text);
    font-family:"Hiragino Kaku Gothic ProN","Yu Gothic UI","Meiryo",sans-serif;line-height:1.75;}
  a{color:inherit;text-decoration:none;}
  .wrap{max-width:1000px;margin:0 auto;padding:0 20px;}
  header{position:sticky;top:0;background:rgba(255,255,255,.9);backdrop-filter:blur(6px);
    border-bottom:1px solid var(--line);z-index:10;}
  .head{display:flex;align-items:center;justify-content:space-between;height:60px;}
  .logo{display:flex;align-items:center;gap:9px;font-weight:700;font-size:19px;}
  .logo .dot{width:12px;height:12px;border-radius:3px;background:var(--cyan);}
  nav{display:flex;gap:20px;font-size:14px;color:var(--muted);}
  .article{max-width:720px;margin:0 auto;padding:26px 0 10px;}
  .crumb{font-size:12px;color:var(--muted);margin-bottom:12px;}
  .cat{display:inline-block;font-size:12px;font-weight:700;padding:3px 11px;border-radius:6px;background:rgba(239,84,35,.13);color:var(--orange);}
  h1{font-size:27px;line-height:1.4;margin:12px 0 10px;}
  .meta{font-size:13px;color:var(--dim);margin-bottom:18px;}
  .hero{width:100%;height:300px;border-radius:14px;background:var(--card2) center/cover;box-shadow:var(--sh);}
  .lead{font-size:16px;margin:22px 0;}
  .tldr{background:#edf7f1;border:1px solid #cfe9d9;border-radius:12px;padding:14px 18px;margin:22px 0;}
  .tldr .lbl{font-size:12px;font-weight:700;color:var(--green);}
  .tldr .body{font-size:15px;margin-top:4px;}
  h2.g{font-size:21px;margin:34px 0 6px;padding-top:8px;border-top:1px solid var(--line);}
  .gimg{width:100%;height:200px;border-radius:12px;background:var(--card2) center/cover;margin:12px 0;box-shadow:var(--sh);}
  p{margin:12px 0;}
  .buybox{margin:16px 0;border:1px solid #f3d8cc;background:#fff7f2;border-radius:14px;padding:16px;box-shadow:var(--sh);}
  .buybox .bt{display:flex;gap:14px;align-items:center;}
  .buybox .th{width:130px;height:60px;border-radius:8px;background:var(--card2) center/cover;flex:0 0 auto;}
  .buybox .name{font-weight:700;font-size:15px;}
  .buybox .off{margin-top:4px;font-size:12px;font-weight:800;color:#fff;background:var(--orange);padding:2px 8px;border-radius:6px;display:inline-block;}
  .buybox .buys{display:flex;gap:9px;margin-top:14px;}
  .buy{flex:1;text-align:center;font-size:14px;font-weight:700;padding:11px 4px;border-radius:9px;color:#fff;}
  .buy.amazon{background:var(--amazon);color:#231a08;}
  .buy.rakuten{background:var(--rakuten);}
  .buy.dmm{background:var(--dmm);}
  .pnote{font-size:11px;color:var(--dim);margin-top:8px;}
  .back{display:inline-block;margin:26px 0 0;font-size:14px;color:var(--cyan);}
  footer{margin-top:40px;border-top:1px solid var(--line);padding:22px 0 40px;color:var(--dim);font-size:12px;}
  footer .disc{max-width:720px;margin:0 auto 14px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;color:var(--muted);box-shadow:var(--sh);}
"""

_PAGE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} ｜ゲームウォッチ</title>
<meta name="description" content="{og_desc}">
{canonical_tag}
<!-- OGP / Twitter Card（Xでリンクを貼った時のカード表示。CTRに直結） -->
<meta property="og:type" content="article">
<meta property="og:site_name" content="ゲームウォッチ">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{og_desc}">
{og_url_tag}
<meta property="og:image" content="{og_image}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{title}">
<meta name="twitter:description" content="{og_desc}">
<meta name="twitter:image" content="{og_image}">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%231793c9'/%3E%3C/svg%3E">
<style>{css}</style>
</head>
<body>
<header><div class="wrap head">
  <div class="logo"><span class="dot"></span>ゲームウォッチ</div>
  <nav><a href="../index.html">トップ</a></nav>
</div></header>

<main class="wrap">
  <article class="article">
    <div class="crumb"><a href="../index.html">ホーム</a> › {category}</div>
    <span class="cat">{category}</span>
    <h1>{title}</h1>
    <div class="meta">{date} ・ ゲームウォッチ編集部</div>
    {hero}
    <p class="lead">{lead}</p>
    {tldr}
    {body}
    <a class="back" href="../index.html">← トップに戻る</a>
  </article>
  <footer>
    <div class="disc">当サイトはアフィリエイトプログラム（Amazonアソシエイト等）を利用し、商品の紹介で収益を得ることがあります。価格・割引はSteam等の公開情報を基にした参考値です。掲載時点の情報のため、最新の価格は各ストアでご確認ください。</div>
    (c) {year} ゲームウォッチ ／ データで見るゲームトレンド
  </footer>
</main>
</body>
</html>
"""


def _esc(s: str) -> str:
    return html.escape(str(s or ""))


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
    )


# ============================================
# トップページ(index.html)の記事一覧を差し替える
# ============================================
_ARTICLES_RE = re.compile(r"(<!--ARTICLES:START-->).*?(<!--ARTICLES:END-->)", re.DOTALL)


def _article_card(a: dict) -> str:
    """1記事分の一覧カード(acard)HTML。a: storage.list_articles の1要素。"""
    catcls = _CAT_CLASS.get(a.get("category", ""), "")
    cls = f"cat {catcls}".strip()
    img = a.get("image_url", "")
    th = (f"<div class=\"th\" style=\"background-image:url('{_esc(img)}')\"></div>"
          if img else "<div class=\"th\"></div>")
    try:
        d = datetime.fromisoformat(a["created_at"]).strftime("%Y/%m/%d")
    except Exception:
        d = ""
    href = f"{config.ARTICLES_SUBDIR}/{a['slug']}.html"
    return f"""      <a class="acard" data-cat="{_esc(a.get('category',''))}" href="{_esc(href)}">
        {th}
        <div><span class="{cls}">{_esc(a.get('category',''))}</span>
          <div class="ttl">{_esc(a.get('title',''))}</div>
          <div class="ex">{_esc(a.get('excerpt',''))}</div>
          <div class="d">{d}</div></div>
      </a>"""


def inject_articles(index_html: str, articles: list[dict]) -> str:
    """index.htmlのARTICLESマーカー間を、記事一覧カードで置き換えた文字列を返す。"""
    cards = "\n".join(_article_card(a) for a in articles) if articles else ""
    replacement = f"<!--ARTICLES:START-->\n{cards}\n      <!--ARTICLES:END-->"
    if not _ARTICLES_RE.search(index_html):
        return index_html  # マーカーが無ければ何もしない
    return _ARTICLES_RE.sub(lambda m: replacement, index_html)


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
