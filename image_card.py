"""
image_card.py
-------------
下書き(draft)から、X添付用のカード画像(PNG)を生成する。

2モード:
- 画像あり: Steam公式のゲーム画像を上部に敷き、下にダーク帯で見出し＋箇条書き。
  出典クレジット（例: 画像: Steam）を明記（ニュース引用の運用前提）。
- 画像なし: 見出し＋箇条書きのみのオリジナルデータカード（著作権リスクなし）。

依存: Pillow。日本語は Windows 同梱の Yu Gothic を使用。
"""

import io
import os
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

# ---- レイアウト定数 ----
W, H = 1200, 675  # X向き 16:9
MARGIN = 64
ACCENT_BAR_W = 14

BG = (15, 23, 32)          # ダークネイビー
TEXT = (245, 247, 250)
MUTED = (154, 167, 180)
ACCENT_NEWS = (255, 90, 60)    # 速報: オレンジレッド
ACCENT_TREND = (60, 199, 255)  # 考察: シアン

# 日本語フォントはOS非依存で解決する（Windows=Yu Gothic / Linux=Noto CJK）。
# 環境変数 FONT_BOLD / FONT_MED で明示指定も可能。
_BOLD_CANDIDATES = [
    os.environ.get("FONT_BOLD", ""),
    r"C:\Windows\Fonts\YuGothB.ttc",
    r"C:\Windows\Fonts\meiryob.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Bold.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
]
_MED_CANDIDATES = [
    os.environ.get("FONT_MED", ""),
    r"C:\Windows\Fonts\YuGothM.ttc",
    r"C:\Windows\Fonts\meiryo.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
]


def _first_existing(paths: list[str]) -> str | None:
    for p in paths:
        if p and os.path.exists(p):
            return p
    return None


FONT_BOLD = _first_existing(_BOLD_CANDIDATES)
FONT_MED = _first_existing(_MED_CANDIDATES) or FONT_BOLD


def _font(path, size: int) -> ImageFont.FreeTypeFont:
    path = path or FONT_BOLD
    return ImageFont.truetype(path, size)


def _wrap(draw, text: str, font, max_width: int) -> list[str]:
    """日本語（スペース無し）対応の折り返し。1文字ずつ幅を測って詰める。"""
    lines, cur = [], ""
    for ch in text:
        if ch == "\n":
            lines.append(cur)
            cur = ""
            continue
        test = cur + ch
        if draw.textlength(test, font=font) <= max_width:
            cur = test
        else:
            lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    return lines


def _cover(im: Image.Image, w: int, h: int) -> Image.Image:
    """アスペクト比を保って (w,h) を覆うようにリサイズし中央クロップ。"""
    im = im.convert("RGB")
    sw, sh = im.size
    scale = max(w / sw, h / sh)
    nw, nh = int(sw * scale), int(sh * scale)
    im = im.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - w) // 2, (nh - h) // 2
    return im.crop((left, top, left + w, top + h))


def _badge(d, x, y, is_news, accent):
    label = "速報" if is_news else "考察"
    bf = _font(FONT_BOLD, 30)
    bw = d.textlength(label, font=bf)
    d.rounded_rectangle([x, y, x + bw + 40, y + 52], radius=10, fill=accent)
    d.text((x + 20, y + 8), label, font=bf, fill=BG)


def _draw_body(d, draft, y_start, accent, bullets_max,
              headline_size=50, bullet_size=34, max_y=H - MARGIN - 40):
    """見出し→区切り→箇条書き を描画。max_y を超えないよう打ち切る。"""
    x = MARGIN
    y = y_start
    hf = _font(FONT_BOLD, headline_size)
    line_h = headline_size + 12
    for line in _wrap(d, draft.get("headline", ""), hf, W - MARGIN * 2)[:2]:
        d.text((x, y), line, font=hf, fill=TEXT)
        y += line_h
    y += 12
    d.line([x, y, W - MARGIN, y], fill=(45, 58, 70), width=2)
    y += 22

    lf = _font(FONT_MED, bullet_size)
    bline_h = bullet_size + 10
    for b in draft.get("bullets", [])[:bullets_max]:
        b = str(b).strip()
        if not b:
            continue
        wrapped = _wrap(d, b, lf, W - MARGIN * 2 - 40)[:2]
        # この箇条書きを描くと max_y を超えるなら打ち切る
        if y + bline_h * len(wrapped) > max_y:
            break
        d.ellipse([x + 2, y + bullet_size // 2 - 2, x + 15, y + bullet_size // 2 + 11], fill=accent)
        for wl in wrapped:
            d.text((x + 32, y), wl, font=lf, fill=TEXT)
            y += bline_h
        y += 6
    return y


def _footer(d, credit: str | None):
    ff = _font(FONT_MED, 26)
    base = f"ガジェゲ  |  {datetime.now().strftime('%Y.%m.%d')}"
    if credit:
        base += f"  |  {credit}"
    d.text((MARGIN, H - MARGIN - 6), base, font=ff, fill=MUTED)


def render_art_card(image_bytes: bytes, draft: dict, out_path: str,
                   credit: str | None = None) -> str:
    """
    公式画像を主役にした「連想させる」カード。投稿内容の要約は載せない。
    画像を16:9で敷き、左上に種別バッジ、右下に小さなブランド＋出典のみ。
    """
    is_news = draft.get("type") == "速報"
    accent = ACCENT_NEWS if is_news else ACCENT_TREND

    base = Image.open(io.BytesIO(image_bytes))
    img = _cover(base, W, H)

    # 上部だけごく薄いダーク帯（種別バッジの視認性確保）
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for i in range(140):
        a = int(150 * (1 - i / 140))
        od.line([(0, i), (W, i)], fill=(0, 0, 0, a))
    img = img.convert("RGBA")
    img.alpha_composite(overlay)
    img = img.convert("RGB")

    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, ACCENT_BAR_W, H], fill=accent)
    _badge(d, MARGIN, 34, is_news, accent)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    img.save(out_path, "PNG")
    return out_path


def render_card(draft: dict, out_path: str,
               game_image_bytes: bytes | None = None, credit: str | None = None) -> str:
    """1件の下書きをカード画像にして out_path に保存し、パスを返す。"""
    is_news = draft.get("type") == "速報"
    accent = ACCENT_NEWS if is_news else ACCENT_TREND

    img = Image.new("RGB", (W, H), BG)

    game_img = None
    if game_image_bytes:
        try:
            game_img = Image.open(io.BytesIO(game_image_bytes))
        except Exception:
            game_img = None

    if game_img is not None:
        # --- 画像ありレイアウト（画像分だけ縦が狭いので控えめに） ---
        IMG_H = 258
        banner = _cover(game_img, W, IMG_H)
        img.paste(banner, (0, 0))

        # 画像下部をBG色へフェード（テキストへの繋ぎ）
        fade_h = 100
        overlay = Image.new("RGBA", (W, fade_h), (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        for i in range(fade_h):
            a = int(255 * (i / fade_h))
            od.line([(0, i), (W, i)], fill=(BG[0], BG[1], BG[2], a))
        img.paste(overlay, (0, IMG_H - fade_h), overlay)

        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, ACCENT_BAR_W, H], fill=accent)
        _badge(d, MARGIN, 24, is_news, accent)
        _draw_body(d, draft, IMG_H + 16, accent, bullets_max=3,
                  headline_size=46, bullet_size=32)
        _footer(d, credit)
    else:
        # --- テキストのみレイアウト ---
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, ACCENT_BAR_W, H], fill=accent)
        _badge(d, MARGIN, MARGIN, is_news, accent)
        _draw_body(d, draft, MARGIN + 84, accent, bullets_max=4)
        _footer(d, credit)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    img.save(out_path, "PNG")
    return out_path


if __name__ == "__main__":
    sample = {
        "type": "速報",
        "headline": "【サイバーパンク2077】70%オフセールでSteam同接7.5万人",
        "bullets": [
            "Steamで70%オフのセール中でトップセラー入り",
            "同接プレイヤー数は約7万5000人を記録",
            "拡張版「仮初めの自由」も40%オフで併売中",
            "続編アニメ第2弾ティーザーも公開との情報",
        ],
    }
    from collectors import steam_collector
    hit = steam_collector.search_game("サイバーパンク2077")
    b = steam_collector.fetch_game_image_bytes(hit["appid"]) if hit else None
    p = render_card(sample, "output/sample_card_img.png", b, "画像: Steam" if b else None)
    print("saved:", p, "| image:", bool(b))
