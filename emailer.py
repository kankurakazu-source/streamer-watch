"""
emailer.py
----------
1回の実行結果（下書き＋カード画像）を、スマホで確認できるようメールで送る。
文面はインラインのカード画像(CID埋め込み・Gmailでも表示される)付きで、
各投稿に「Xで開く」リンク（タップで文面プリフィルのX投稿画面が開く）を添える。

Gmailの「アプリパスワード」を使ってSMTP送信する。投稿自体は行わない。
"""

import html
import os
import smtplib
import urllib.parse
from datetime import datetime
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import make_msgid


def _post_html(index: int, draft: dict, cid: str | None) -> str:
    dtype = html.escape(draft.get("type", ""))
    topic = html.escape(draft.get("topic", ""))
    post = draft.get("draft_post", "")
    post_html = html.escape(post).replace("\n", "<br>")
    color = "#ff5a3c" if draft.get("type") == "速報" else "#3cc7ff"
    intent = "https://x.com/intent/tweet?text=" + urllib.parse.quote(post)

    img_block = ""
    if cid:
        img_block = (
            f"<img src='cid:{cid}' width='100%' "
            f"style='max-width:520px;border-radius:10px;display:block;margin:0 0 12px;'>"
        )

    cw = draft.get("char_weight")
    cw_html = ""
    if cw is not None:
        cw_color = "#3cc7ff" if cw <= 280 else "#ff5a3c"
        cw_html = f"<span style='color:{cw_color};font-size:13px;'>&nbsp;{cw}/280</span>"

    return f"""
    <div style="background:#16202b;border:1px solid #24313d;border-radius:12px;padding:16px;margin:0 0 16px;">
      <div style="margin:0 0 10px;">
        <span style="background:{color};color:#0f1720;font-weight:bold;padding:3px 12px;border-radius:8px;">{dtype}</span>
        <span style="color:#c7d2dc;font-size:14px;">&nbsp;#{index} {topic}</span>{cw_html}
      </div>
      {img_block}
      <div style="color:#f5f7fa;font-size:15px;line-height:1.7;white-space:normal;">{post_html}</div>
      <div style="margin-top:12px;">
        <a href="{html.escape(intent)}"
           style="background:#1d9bf0;color:#fff;text-decoration:none;padding:9px 18px;border-radius:8px;font-size:14px;">
           Xで開く（文面プリフィル）</a>
      </div>
    </div>
    """


def build_message(result: dict, from_addr: str, to_addr: str) -> MIMEMultipart:
    """レビュー内容の HTML メール（インライン画像付き）を組み立てる。"""
    drafts = [d for d in result.get("drafts", []) if isinstance(d, dict) and d.get("draft_post")]
    now = datetime.now().strftime("%Y/%m/%d %H:%M")

    root = MIMEMultipart("related")
    root["Subject"] = f"🎮ガジェゲ 下書き{len(drafts)}件 ({now})"
    root["From"] = from_addr
    root["To"] = to_addr

    alt = MIMEMultipart("alternative")
    root.attach(alt)

    posts_html = []
    plain_lines = [f"ガジェゲ 下書き {len(drafts)}件 ({now})", ""]
    for i, d in enumerate(drafts, 1):
        cid = None
        if d.get("image_path") and os.path.exists(d["image_path"]):
            cid = make_msgid()[1:-1]  # <...> を除去
            with open(d["image_path"], "rb") as f:
                img = MIMEImage(f.read(), _subtype="png")
            img.add_header("Content-ID", f"<{cid}>")
            img.add_header("Content-Disposition", "inline")
            root.attach(img)
        posts_html.append(_post_html(i, d, cid))
        plain_lines.append(f"#{i} [{d.get('type','')}] {d.get('topic','')}")
        plain_lines.append(d.get("draft_post", ""))
        plain_lines.append("")

    if not drafts:
        posts_html.append("<p style='color:#9aa7b4;'>今回は下書きがありませんでした。</p>")

    body = f"""<!DOCTYPE html><html><body style="background:#0f1720;margin:0;padding:16px;
      font-family:'Hiragino Kaku Gothic ProN','Yu Gothic',sans-serif;">
      <div style="color:#f5f7fa;font-size:18px;font-weight:bold;margin:0 0 4px;">🎮 ガジェゲ 下書きレビュー</div>
      <div style="color:#9aa7b4;font-size:12px;margin:0 0 16px;">{now} ／ 確認後、良いものだけ手動でポストしてください（自動投稿はしません）</div>
      {''.join(posts_html)}
    </body></html>"""

    alt.attach(MIMEText("\n".join(plain_lines), "plain", "utf-8"))
    alt.attach(MIMEText(body, "html", "utf-8"))
    return root


def build_article_message(article: dict, thread: dict, public_url: str, local_path: str,
                          hero_url: str, image_path: str | None,
                          from_addr: str, to_addr: str) -> MIMEMultipart:
    """
    記事1本の通知メール。「2ステップ・ポスト」（①親ポスト＝画像＋リンクなし／②リプ＝記事リンク）を
    セットで表示。親ポスト用の画像(image_path)は、①インライン表示(CID)＋②保存できる添付ファイル の
    両方でメールに載せる（Gmailが外部画像をブロックしても見え、そのまま保存してXに添付できる）。
    thread: {main, reply, main_weight, reply_weight}
    """
    now = datetime.now().strftime("%Y/%m/%d %H:%M")
    title = html.escape(article.get("title", ""))
    category = html.escape(article.get("category", ""))
    lead = html.escape(article.get("lead", ""))

    main_txt = thread.get("main", "")
    reply_txt = thread.get("reply", "")
    linkless = bool(thread.get("linkless"))  # 検索ban対策: リンク無し単発モード
    main_html = html.escape(main_txt).replace("\n", "<br>")
    reply_html = html.escape(reply_txt).replace("\n", "<br>")
    intent_main = "https://x.com/intent/tweet?text=" + urllib.parse.quote(main_txt)
    intent_reply = "https://x.com/intent/tweet?text=" + urllib.parse.quote(reply_txt)
    mw, rw = thread.get("main_weight"), thread.get("reply_weight")

    breaking = article.get("is_breaking")
    etype = html.escape(article.get("event_type", "") or "")
    badge = ("🚨速報" if breaking else "📝記事")

    # 添付画像を読み込む（あれば）。CID表示用と保存用の両方で使う。
    img_data = None
    img_name = "post_image.png"
    if image_path and os.path.exists(image_path):
        with open(image_path, "rb") as f:
            img_data = f.read()
        img_name = os.path.basename(image_path)
    cid = make_msgid()[1:-1] if img_data else None

    # MIME構造: mixed(related(alternative + inline画像) + 添付画像)
    root = MIMEMultipart("mixed")
    root["Subject"] = f"{badge}ガジェゲ: {article.get('title','')[:40]} ({now})"
    root["From"] = from_addr
    root["To"] = to_addr
    related = MIMEMultipart("related")
    alt = MIMEMultipart("alternative")

    # 記事リンク: 公開URLがあればそれ、無ければローカルファイル(file://)を案内
    if public_url:
        link_href = html.escape(public_url)
        link_label = "記事を開く（公開URL）"
        link_note = ""
    else:
        file_url = "file:///" + local_path.replace("\\", "/")
        link_href = html.escape(file_url)
        link_label = "記事を開く（ローカル確認用）"
        link_note = ("<div style='color:#9aa7b4;font-size:12px;margin-top:6px;'>"
                     "※まだ公開URLが未設定です。config.SITE_BASE_URL を設定してください。</div>")

    def _weight_span(w):
        if w is None:
            return ""
        c = "#3cc7ff" if w <= 280 else "#ff5a3c"
        return f"<span style='color:{c};font-size:12px;'>&nbsp;{w}/280</span>"

    # 親ポスト添付画像ブロック（CID表示。画像が無ければテキストの注意書き）
    if cid:
        post_img_block = (
            f"<img src='cid:{cid}' width='100%' "
            f"style='max-width:520px;border-radius:10px;display:block;margin:0 0 10px;'>"
            f"<div style='color:#9aa7b4;font-size:12px;margin:0 0 12px;'>"
            f"↑ この画像を保存して親ポストに添付（末尾に「{img_name}」も添付済み）</div>"
        )
    else:
        post_img_block = ("<div style='color:#ffb454;font-size:12px;margin:0 0 12px;'>"
                          "※今回は添付画像を生成できませんでした（画像なしで投稿してください）。</div>")

    # 投稿手順・投稿ブロックは linkless（検索ban対策）で切り替える
    if linkless:
        steps_block = (
            '<div style="background:#2a2413;border:1px solid #4a3f1a;border-radius:12px;'
            'padding:14px 16px;margin:0 0 12px;color:#e8dfb8;font-size:13px;line-height:1.7;">'
            '<b style="color:#ffd966;">⚠ 検索ban対策モード（リンク無し・単発投稿）</b><br>'
            '下の投稿を<b>画像を添付して</b>そのままポスト。<b>記事リンクは貼らない</b>（リプ返信も不要）。<br>'
            '<span style="color:#b9ae82;font-size:12px;">'
            '※ban解除後に元の「2ステップ（リプに記事リンク）」へ戻すには config.X_LINKLESS_MODE を False に。</span>'
            '</div>'
        )
        main_label = "投稿（画像添付・リンクなし）"
        reply_block = ""  # リプ欄は出さない
    else:
        steps_block = (
            '<div style="background:#1a2531;border:1px solid #2a3a48;border-radius:12px;'
            'padding:14px 16px;margin:0 0 12px;color:#c7d2dc;font-size:13px;line-height:1.7;">'
            '<b style="color:#f5f7fa;">🧵 2ステップ投稿の手順</b><br>'
            '① 下の「親ポスト」を<b>下の画像を添付して</b>ポスト（リンクを貼らずインプレッションを稼ぐ）<br>'
            '② 投稿できたら、その<b>自分のポストのリプ欄</b>に「返信」をぶら下げる（記事リンクはここだけ）'
            '</div>'
        )
        main_label = "① 親ポスト（画像添付・リンクなし）"
        reply_block = f"""
      <div style="background:#16202b;border:1px solid #24313d;border-radius:12px;padding:16px;">
        <div style="color:#9aa7b4;font-size:12px;margin:0 0 8px;">② 返信（親ポストのリプ欄に貼る・記事リンク）{_weight_span(rw)}</div>
        <div style="color:#f5f7fa;font-size:15px;line-height:1.7;">{reply_html}</div>
        <div style="margin-top:12px;">
          <a href="{html.escape(intent_reply)}" style="background:#1d9bf0;color:#fff;text-decoration:none;
             padding:9px 18px;border-radius:8px;font-size:14px;">Xで開く（返信）</a>
        </div>
      </div>"""

    body = f"""<!DOCTYPE html><html><body style="background:#0f1720;margin:0;padding:16px;
      font-family:'Hiragino Kaku Gothic ProN','Yu Gothic',sans-serif;">
      <div style="color:#f5f7fa;font-size:18px;font-weight:bold;margin:0 0 4px;">{badge} 新着記事ができました</div>
      <div style="color:#9aa7b4;font-size:12px;margin:0 0 16px;">{now} ／ {etype} ／ 内容を確認し、良ければ手動でポストしてください（自動投稿はしません）</div>

      <div style="background:#16202b;border:1px solid #24313d;border-radius:12px;padding:16px;margin:0 0 16px;">
        <span style="background:#3cc7ff;color:#0f1720;font-weight:bold;padding:3px 12px;border-radius:8px;">{category}</span>
        <div style="color:#f5f7fa;font-size:17px;font-weight:bold;margin:10px 0;">{title}</div>
        <div style="color:#c7d2dc;font-size:14px;line-height:1.7;">{lead}</div>
        <div style="margin-top:14px;">
          <a href="{link_href}" style="background:#1a9e5a;color:#fff;text-decoration:none;
             padding:9px 18px;border-radius:8px;font-size:14px;">{link_label}</a>
        </div>
        {link_note}
      </div>

      {steps_block}

      <div style="background:#16202b;border:1px solid #24313d;border-radius:12px;padding:16px;margin:0 0 12px;">
        <div style="color:#9aa7b4;font-size:12px;margin:0 0 8px;">{main_label}{_weight_span(mw)}</div>
        {post_img_block}
        <div style="color:#f5f7fa;font-size:15px;line-height:1.7;">{main_html}</div>
        <div style="margin-top:12px;">
          <a href="{html.escape(intent_main)}" style="background:#1d9bf0;color:#fff;text-decoration:none;
             padding:9px 18px;border-radius:8px;font-size:14px;">Xで開く（投稿）</a>
        </div>
      </div>
      {reply_block}
    </body></html>"""

    if linkless:
        plain = (f"新着記事 ({now})\n{article.get('title','')}\n\n"
                 f"{article.get('lead','')}\n\n記事: {public_url or local_path}\n\n"
                 f"[検索ban対策・リンク無し単発投稿]\n投稿（画像添付・リンクなし）:\n{main_txt}\n"
                 f"（用の画像は添付ファイル {img_name} を保存して添付）\n"
                 f"※記事リンクは貼らない（リプ返信も不要）\n")
    else:
        plain = (f"新着記事 ({now})\n{article.get('title','')}\n\n"
                 f"{article.get('lead','')}\n\n記事: {public_url or local_path}\n\n"
                 f"[2ステップ投稿]\n① 親ポスト（画像添付・リンクなし）:\n{main_txt}\n"
                 f"（親ポスト用の画像は添付ファイル {img_name} を保存して添付）\n\n"
                 f"② 返信（リプ欄・記事リンク）:\n{reply_txt}\n")

    alt.attach(MIMEText(plain, "plain", "utf-8"))
    alt.attach(MIMEText(body, "html", "utf-8"))
    related.attach(alt)

    if img_data:
        inline = MIMEImage(img_data, _subtype="png")
        inline.add_header("Content-ID", f"<{cid}>")
        inline.add_header("Content-Disposition", "inline", filename=img_name)
        related.attach(inline)

    root.attach(related)

    if img_data:
        attach = MIMEImage(img_data, _subtype="png")
        attach.add_header("Content-Disposition", "attachment", filename=img_name)
        root.attach(attach)

    return root


def send_article_email(article: dict, thread: dict, public_url: str, local_path: str,
                       hero_url: str, image_path: str | None, host: str, port: int,
                       user: str, password: str, from_addr: str, to_addr: str) -> None:
    """記事通知メールをSMTP(STARTTLS)で送信する。失敗時は例外送出。"""
    msg = build_article_message(article, thread, public_url, local_path, hero_url,
                                image_path, from_addr, to_addr)
    with smtplib.SMTP(host, port, timeout=30) as s:
        s.starttls()
        s.login(user, password)
        s.send_message(msg)


def send_review_email(result: dict, host: str, port: int,
                     user: str, password: str, from_addr: str, to_addr: str) -> None:
    """SMTP(STARTTLS)でレビューメールを送信する。失敗時は例外送出。"""
    msg = build_message(result, from_addr, to_addr)
    with smtplib.SMTP(host, port, timeout=30) as s:
        s.starttls()
        s.login(user, password)
        s.send_message(msg)


if __name__ == "__main__":
    # 送信せず .eml を書き出して中身を確認するテスト
    import json
    import sys

    src = sys.argv[1] if len(sys.argv) > 1 else "output/game_draft_20260705_1730.json"
    result = json.load(open(src, encoding="utf-8"))
    msg = build_message(result, "from@example.com", "to@example.com")
    out = "output/review_email_preview.eml"
    with open(out, "wb") as f:
        f.write(msg.as_bytes())
    print("wrote:", out, "| parts:", len(msg.get_payload()))
