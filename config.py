"""
config.py
---------
APIキーや対象配信者リストを一括管理する設定ファイル。

APIキーは環境変数から読み込む想定（.envファイル推奨）。
このリポジトリをGitにpushする場合は .env を必ず .gitignore に入れること。
"""

import os

# .env ファイルを自動で読み込む（python-dotenv）。
# 同ディレクトリの .env を探して環境変数に流し込むので、
# 手動で set/export しなくても config.* から参照できるようになる。
# python-dotenv 未インストールでも import 自体は失敗しないようにしておく。
try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
except ImportError:
    pass

# ============================================
# APIキー（環境変数から取得。直接書き込まないこと）
# ============================================
TWITCH_CLIENT_ID = os.environ.get("TWITCH_CLIENT_ID", "")
TWITCH_CLIENT_SECRET = os.environ.get("TWITCH_CLIENT_SECRET", "")
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# X(Twitter) APIは有料プラン(Basic以上)が必要。
# 契約していない場合は x_mentions_collector.py の該当処理をスキップする設定にできる。
X_BEARER_TOKEN = os.environ.get("X_BEARER_TOKEN", "")

# ============================================
# 対象配信者リスト
# - twitch_login       : twitch.tv/○○○ の ○○○（全小文字）。無ければ空文字
# - youtube_handle     : youtube.com/@○○○ のハンドル（@付き）。無ければ空文字
# - youtube_channel_id : UCで始まるチャンネルID。空ならhandleから自動解決される。
#                        （resolveを毎回省きたい場合はここにUC IDを直書きしてもよい）
# ============================================
STREAMERS = [
    {
        "display_name": "加藤純一",
        "twitch_login": "kato_junichi0817",
        "youtube_handle": "@unkochankirinuki",  # 注: 切り抜きチャンネルの可能性。ライブ配信は本人メインchでないと拾えない
        "youtube_channel_id": "",
    },
    {
        "display_name": "布団ちゃん",
        "twitch_login": "indegnasen0706",
        "youtube_handle": "@MAX-zc4zq",
        "youtube_channel_id": "",
    },
    {
        "display_name": "もこう",
        "twitch_login": "mokouliszt1",
        "youtube_handle": "@mokoustream",
        "youtube_channel_id": "",
    },
    {
        "display_name": "はんじょう",
        "twitch_login": "hanjoudesu",
        "youtube_handle": "@Hanjou",
        "youtube_channel_id": "",
    },
    {
        "display_name": "たいじ",
        "twitch_login": "yaritaiji",
        "youtube_handle": "@Yaritaiji",
        "youtube_channel_id": "",
    },
    {
        "display_name": "バトラ",
        "twitch_login": "batora324",
        "youtube_handle": "@batoradayo",
        "youtube_channel_id": "",
    },
    {
        "display_name": "GON",
        "twitch_login": "gon_vl",
        "youtube_handle": "@gon_vl",
        "youtube_channel_id": "",
    },
    {
        "display_name": "Laz",
        "twitch_login": "lazvell",
        "youtube_handle": "@Lazvell",
        "youtube_channel_id": "",
    },
    {
        "display_name": "柊つるぎ",
        "twitch_login": "hiiragitsurugi",
        "youtube_handle": "@HiiragiTsurugi",
        "youtube_channel_id": "",
    },
    {
        "display_name": "関優太",
        "twitch_login": "stylishnoob4",
        "youtube_handle": "@StylishNoob4",
        "youtube_channel_id": "",
    },
]

# ============================================
# ゲーム情報収集の設定（新方針: 話題のゲーム情報を定期発信）
# ============================================
# YouTube急上昇を取得するリージョン（両方バランス方針で日本＋米国）
YOUTUBE_TREND_REGIONS = ["JP", "US"]
# Twitchの上位配信を何ページ分集計するか（1ページ=最大100配信）
TWITCH_TOP_PAGES = 2

# Steam（キー不要の公開エンドポイントを使用）
STEAM_CC = "jp"          # 国コード（価格・地域別ランキング用）
STEAM_LANG = "japanese"  # 表示言語
# 常時ウォッチする主要タイトル（appid: 表示名）。売上上位に入らなくても
# 同接が急増したら「旧作復活」等のネタになるため定点観測する。
STEAM_WATCHLIST = {
    730: "Counter-Strike 2",
    570: "Dota 2",
    578080: "PUBG: BATTLEGROUNDS",
    1172470: "Apex Legends",
    271590: "Grand Theft Auto V",
    1245620: "ELDEN RING",
    1086940: "Baldur's Gate 3",
    1623730: "Palworld",
    2767030: "Marvel Rivals",
    1091500: "Cyberpunk 2077",
    359550: "Rainbow Six Siege",
    252490: "Rust",
    1938090: "Call of Duty",
    2358720: "Black Myth: Wukong",
    413150: "Stardew Valley",
}
# 同接の前回比がこの%を超えたら「急増」として注目対象に含める
STEAM_SPIKE_THRESHOLD = 25.0

# ゲームメディアのRSS（速報・裏取り用。国内＋海外バランス）
# {"name": 表示名, "url": フィードURL}
RSS_FEEDS = [
    {"name": "4Gamer", "url": "https://www.4gamer.net/rss/index.xml"},
    {"name": "AUTOMATON", "url": "https://automaton-media.com/feed/"},
    {"name": "GameSpark", "url": "https://www.gamespark.jp/rss/index.rdf"},
    {"name": "GameWatch", "url": "https://game.watch.impress.co.jp/data/rss/1.0/gmw/feed.rdf"},
    {"name": "IGN", "url": "https://feeds.feedburner.com/ign/all"},
    {"name": "PCGamer", "url": "https://www.pcgamer.com/rss/"},
]
# 各フィードから拾う最大件数、および「直近何時間以内」を速報候補とみなすか
RSS_PER_FEED_LIMIT = 8
RSS_RECENT_HOURS = 30

# ============================================
# メール送信（スマホでリモート確認するため）
# Gmail の「アプリパスワード」を GMAIL_APP_PASSWORD に入れる（通常のログインPWは不可）
# ============================================
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "")          # 送信元Gmailアドレス
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")  # 16桁のアプリパスワード
EMAIL_TO = os.environ.get("EMAIL_TO", "") or GMAIL_ADDRESS    # 宛先（未指定なら自分宛）


def email_enabled() -> bool:
    return bool(GMAIL_ADDRESS and GMAIL_APP_PASSWORD and EMAIL_TO)


# ============================================
# データ保存先
# ============================================
DATA_DIR = "data"
OUTPUT_DIR = "output"
HISTORY_DB = f"{DATA_DIR}/history.sqlite3"

# ============================================
# 記事サイト（アフィリエイト記事の生成・公開先）
# ============================================
SITE_DIR = "site"                      # 静的サイトのルート
ARTICLES_SUBDIR = "articles"           # 記事HTMLの置き場所（site配下）
# 記事のX投稿に付ける公開URLのベース。末尾スラッシュ有無どちらでも可。
# Cloudflare Pages で公開中（汎用URL・個人名なし）。
# 環境変数 SITE_BASE_URL があればそれを優先する（独自ドメイン移行時に上書き可能）。
SITE_BASE_URL = os.environ.get("SITE_BASE_URL", "") or "https://game-watch.pages.dev"

# 記事のカテゴリ候補（AIはこの中から選ぶ）
ARTICLE_CATEGORIES = ["セール分析", "注目株", "新作", "eスポーツ", "データ分析", "考察"]

# アフィリエイト設定（取得後に各購入リンクへ付与する。未設定なら通常の検索リンク）
AMAZON_ASSOC_TAG = os.environ.get("AMAZON_ASSOC_TAG", "")        # Amazonアソシエイトのタグ(例: yourtag-22)
RAKUTEN_AFFILIATE_ID = os.environ.get("RAKUTEN_AFFILIATE_ID", "")  # 楽天アフィリエイトID
