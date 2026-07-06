# streamer_watch

ストリーマー関連情報を自動収集し、X投稿の下書きをAIで生成するツールです。
**投稿の最終判断・実行は必ず人間(あなた)が行う設計**になっています。

## できること

1. Twitch / YouTubeの配信状況・視聴者数を定期取得
2. 前回実行との比較で「急増」を検知
3. （X API契約時のみ）Xでの言及数も取得
4. 上記データをClaude APIに渡し、「事実＋考察」形式の投稿下書きをJSON生成
5. `output/` フォルダに下書きファイルを出力（Xへの自動投稿は行わない）

## セットアップ

### 1. 仮想環境の作成と依存パッケージのインストール

Windows (PowerShell) の例:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

以降、`python` の代わりに `.venv\Scripts\python.exe` を使うと仮想環境で実行できます。

### 2. APIキーの準備

`.env.example` を `.env` にコピーし、それぞれのAPIキーを設定してください。

| キー | 取得先 | 必須度 |
|---|---|---|
| TWITCH_CLIENT_ID / SECRET | https://dev.twitch.tv/console/apps | 必須 |
| YOUTUBE_API_KEY | Google Cloud Console (YouTube Data API v3を有効化) | VTuber等YouTube配信者を追う場合は必須 |
| ANTHROPIC_API_KEY | https://console.anthropic.com/ | 必須（下書き生成に使用） |
| X_BEARER_TOKEN | X Developer Portal（Basicプラン以上、月額費用あり） | 任意（無くても動く） |

`.env` は `config.py` が `python-dotenv` で**自動読み込み**します。
手動での `export` / `set` は不要です（`.env` は `.gitignore` 済みでコミットされません）。

### 3. `config.py` の配信者情報を正確に埋める

**重要**: `config.py` 内の `twitch_login` は仮の値が入っているものがあります。
実際のTwitchログイン名・YouTubeチャンネルIDに必ず置き換えてください
（TwitchのプロフィールURL `twitch.tv/○○○` の `○○○` がログイン名、
YouTubeは `youtube.com/channel/UC...` の `UC` で始まるIDが必要）。

### 4. APIキーの疎通確認（実機スモークテスト）

本番実行の前に、キーが正しく機能するか確認できます（読み取り専用・投稿はしません）:

```powershell
.venv\Scripts\python.exe test_apis.py
```

Twitch / YouTube / Anthropic それぞれ `[OK]` / `[FAIL]` / `[SKIP]` で結果が表示されます。

### 5. 実行

```powershell
.venv\Scripts\python.exe main.py
```

`output/draft_YYYYMMDD_HHMM.json` に下書きが出力されます。

## 定期実行の設定（cron例）

毎日 9時・13時・20時・24時に実行する場合:

```bash
crontab -e
```

```
0 9,13,20,0 * * * cd /path/to/streamer_watch && /usr/bin/python3 main.py >> log.txt 2>&1
```

## 運用フロー（推奨）

```
① cronで自動収集・下書き生成
② output/の下書きJSONを1日1〜2回チェック（5〜10分程度）
③ 良い下書きを選んで自分の言葉で微調整
④ 手動でXに投稿
```

完全自動投稿はスパム判定・アカウント凍結リスクがあるため、
**③④の人間チェックは省略しないことを強く推奨します。**

## 安全に関する注意

- 未確認の噂・炎上情報を断定的に投稿しない
- 配信者本人の発言を捏造・誇張して引用しない
- データは公開されている数値（視聴者数など）のみを使用する
- X・Twitch・YouTubeそれぞれの利用規約・APIガイドラインを順守する

## 拡張アイデア

- Slack/LINE Notifyへの通知機能を追加し、下書きをスマホで確認できるようにする
- 配信者ごとの「同接推移グラフ」を週次で自動生成し、note用の素材にする
- 複数日分のデータを蓄積し、「今週の伸び率TOP3」を自動集計する投稿フォーマットを追加する
