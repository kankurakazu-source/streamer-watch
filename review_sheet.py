"""
review_sheet.py
---------------
1回の実行結果（下書き＋カード画像）を、人間がレビューしやすい1枚のHTMLにまとめる。
各投稿について「カード画像」「投稿文面（コピー用）」「添付すべき画像ファイル名」
「Xで下書きを開くボタン（文面プリフィル）」を並べる。

運用フロー: このHTMLを開く → 文面を確認 → 良ければ「Xで開く」→ 画像を添付 → 手動ポスト。
（投稿自体はこのツールでは行わない）
"""

import base64
import html
import os
import urllib.parse
from datetime import datetime


def _img_data_uri(path: str) -> str | None:
    """画像をbase64のdata URIにして返す（HTMLを自己完結・可搬にする）。"""
    try:
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        return f"data:image/png;base64,{b64}"
    except OSError:
        return None


def _charcount_html(draft: dict) -> str:
    cw = draft.get("char_weight")
    if cw is None:
        return ""
    color = "#3cc7ff" if cw <= 280 else "#ff5a3c"
    return f"<span style='margin-left:auto;color:{color};font-size:13px;'>{cw}/280</span>"


def _card_html(index: int, draft: dict, html_dir: str) -> str:
    dtype = draft.get("type", "")
    badge_class = "news" if dtype == "速報" else "trend"
    topic = html.escape(draft.get("topic", ""))
    post = draft.get("draft_post", "")
    post_esc = html.escape(post)

    # 画像は base64 で埋め込む（HTMLだけで完結し、どこで開いても表示される）
    img_tag = "<div class='noimg'>画像なし<br><span style='font-size:12px'>本文に根拠リンクを掲載</span></div>"
    labels = {"steam": "Steam公式画像", "youtube": "YouTubeサムネイル"}
    if draft.get("image_path"):
        data_uri = _img_data_uri(draft["image_path"])
        img_name = os.path.basename(draft["image_path"])
        src_label = labels.get(draft.get("image_source"), "画像")
        if data_uri:
            img_tag = (
                f"<img src='{data_uri}' alt='card'>"
                f"<div class='imgmeta'>{src_label}：<code>{html.escape(img_name)}</code></div>"
            )

    intent = "https://x.com/intent/tweet?text=" + urllib.parse.quote(post)

    return f"""
    <section class="post">
      <div class="col img">{img_tag}</div>
      <div class="col body">
        <div class="head">
          <span class="badge {badge_class}">{html.escape(dtype)}</span>
          <span class="topic">#{index} {topic}</span>
          {_charcount_html(draft)}
        </div>
        <textarea readonly onclick="this.select()">{post_esc}</textarea>
        <div class="actions">
          <button onclick="copyText(this)">文面をコピー</button>
          <button onclick="copyImg(this)">画像をコピー</button>
          <a class="xbtn" href="{html.escape(intent)}" target="_blank" rel="noopener">Xで下書きを開く</a>
        </div>
        <div class="note">おすすめ手順：「Xで開く」で文面入力 → このカードの「画像をコピー」→ X投稿欄で Ctrl+V 貼付 → ポスト。</div>
      </div>
    </section>
    """


def build_review_sheet(result: dict, out_path: str) -> str:
    """result（drafts を含む辞書）から レビュー用HTML を生成して保存する。"""
    html_dir = os.path.dirname(os.path.abspath(out_path))
    drafts = [d for d in result.get("drafts", []) if isinstance(d, dict) and d.get("draft_post")]
    generated = result.get("generated_at", datetime.now().isoformat())

    cards = "\n".join(_card_html(i, d, html_dir) for i, d in enumerate(drafts, 1))
    if not cards:
        cards = "<p class='empty'>今回は下書きがありませんでした。</p>"

    doc = f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ゲームウォッチ レビュー {generated}</title>
<style>
  body {{ background:#0f1720; color:#f5f7fa; font-family:"Yu Gothic","Meiryo",sans-serif; margin:0; padding:24px; }}
  h1 {{ font-size:20px; margin:0 0 4px; }}
  .sub {{ color:#9aa7b4; font-size:13px; margin-bottom:20px; }}
  .post {{ display:flex; gap:20px; background:#16202b; border:1px solid #24313d; border-radius:14px; padding:18px; margin-bottom:18px; }}
  .col.img {{ flex:0 0 46%; }}
  .col.img img {{ width:100%; border-radius:10px; display:block; }}
  .imgmeta {{ color:#9aa7b4; font-size:12px; margin-top:8px; }}
  .noimg {{ color:#9aa7b4; font-size:13px; padding:40px; text-align:center; border:1px dashed #35424f; border-radius:10px; }}
  .col.body {{ flex:1; display:flex; flex-direction:column; }}
  .head {{ display:flex; align-items:center; gap:10px; margin-bottom:10px; }}
  .badge {{ font-weight:bold; padding:3px 12px; border-radius:8px; color:#0f1720; font-size:14px; }}
  .badge.news {{ background:#ff5a3c; }}
  .badge.trend {{ background:#3cc7ff; }}
  .topic {{ color:#c7d2dc; font-size:14px; }}
  textarea {{ width:100%; min-height:150px; box-sizing:border-box; background:#0f1720; color:#f5f7fa;
             border:1px solid #35424f; border-radius:8px; padding:12px; font-size:15px; line-height:1.6;
             font-family:inherit; resize:vertical; }}
  .actions {{ display:flex; gap:10px; margin-top:10px; }}
  button, .xbtn {{ font-size:14px; padding:8px 16px; border-radius:8px; border:none; cursor:pointer; text-decoration:none; }}
  button {{ background:#2a3a47; color:#f5f7fa; }}
  .xbtn {{ background:#1d9bf0; color:#fff; }}
  .note {{ color:#9aa7b4; font-size:12px; margin-top:8px; }}
  .empty {{ color:#9aa7b4; }}
</style></head><body>
  <h1>🎮 ゲームウォッチ 下書きレビュー</h1>
  <div class="sub">生成: {html.escape(str(generated))} ／ 確認後、良いものだけ手動でポストしてください（自動投稿はしません）</div>
  {cards}
  <div id="toast"></div>
  <script>
    function toast(msg) {{
      var t = document.getElementById('toast');
      t.textContent = msg; t.className = 'show';
      setTimeout(function(){{ t.className = ''; }}, 1800);
    }}
    function copyText(btn) {{
      var ta = btn.closest('.post').querySelector('textarea');
      navigator.clipboard.writeText(ta.value).then(function(){{ toast('文面をコピーしました'); }});
    }}
    async function copyImg(btn) {{
      var img = btn.closest('.post').querySelector('img');
      if (!img) {{ toast('画像がありません'); return; }}
      try {{
        var res = await fetch(img.src);
        var blob = await res.blob();
        await navigator.clipboard.write([new ClipboardItem({{'image/png': blob}})]);
        toast('画像をコピーしました（X投稿欄で Ctrl+V）');
      }} catch (e) {{
        toast('コピー不可のブラウザです。画像を右クリックで保存してください');
      }}
    }}
  </script>
  <style>
    #toast {{ position:fixed; left:50%; bottom:28px; transform:translateX(-50%) translateY(20px);
             background:#1d9bf0; color:#fff; padding:10px 20px; border-radius:10px; opacity:0;
             transition:all .2s; pointer-events:none; font-size:14px; }}
    #toast.show {{ opacity:1; transform:translateX(-50%) translateY(0); }}
  </style>
</body></html>
"""
    os.makedirs(html_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(doc)
    return out_path


if __name__ == "__main__":
    import json
    import sys

    src = sys.argv[1] if len(sys.argv) > 1 else "output/game_draft_20260705_1730.json"
    result = json.load(open(src, encoding="utf-8"))
    out = src.replace("game_draft_", "review_").replace(".json", ".html")
    print("saved:", build_review_sheet(result, out))
