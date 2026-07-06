# GitHub Actions で定期自動実行（PCを閉じていてもOK）

クラウド上のGitHubが定期的に `game_watch.py` を実行し、下書き＋画像をメールで送ります。
PCの電源とは無関係に動きます。**APIキーはコードには載せず、GitHubのSecretsに保存します。**

## 全体像
1. GitHubで**非公開(private)リポジトリ**を作る
2. このプロジェクトを push する（`.env` は除外され、キーは含まれません）
3. GitHubに**Secrets（APIキー類）を登録**する
4. Actionsを有効化し、**手動実行(Run workflow)でテスト** → メールが届けば完成
5. 以降は cron（JST 8時/13時/20時）で自動実行

---

## 手順

### 1. GitHubリポジトリを作る
- https://github.com/new → Repository name（例: `streamer-watch`）→ **Private** を選択 → Create
- 作成後に表示される `https://github.com/<あなた>/streamer-watch.git` を控える

### 2. push する（PowerShellでこのフォルダから）
```powershell
cd C:\ClaudeCode\streamer_watch
git branch -M main
git remote add origin https://github.com/<あなた>/streamer-watch.git
git push -u origin main
```
※ push時にGitHubのログイン（ブラウザ認証）を求められたら承認してください。

### 3. Secretsを登録する
リポジトリの **Settings → Secrets and variables → Actions → New repository secret** で、
以下を1つずつ登録（名前は完全一致で）:

| Name | 値 |
|---|---|
| `TWITCH_CLIENT_ID` | .envのTWITCH_CLIENT_IDの値 |
| `TWITCH_CLIENT_SECRET` | 同上 |
| `YOUTUBE_API_KEY` | 同上 |
| `ANTHROPIC_API_KEY` | 同上 |
| `GMAIL_ADDRESS` | （あなたのGmailアドレス） |
| `GMAIL_APP_PASSWORD` | Gmailアプリパスワード（16桁） |
| `EMAIL_TO` | （通知先メールアドレス。未指定なら送信元と同じ） |

（`.env` の値をそのままコピーすればOK。値は登録後は表示されません＝安全）

### 4. テスト実行
- リポジトリの **Actions** タブ → 左の「game-watch」→ **Run workflow**（手動実行）
- 数分待って緑チェックになり、**メールが届けば成功**
- 赤×なら、その実行のログを開いてエラー箇所を私に貼ってください

### 5. 自動実行
- 上でテストが通れば、以降は自動で JST 8:00 / 13:00 / 20:00 に実行されメールが届きます
- 時刻を変えたい場合は `.github/workflows/gamewatch.yml` の cron を調整（UTC表記）

---

## メモ
- **前日比**は、クラウドで1日以上実行が蓄積されると出るようになります（履歴DBは毎回リポジトリに自動保存）。
- 実行頻度を変えるとYouTube APIクォータ消費が増えます。1日3回なら余裕です。
- 投稿は自動化しません（メールで確認→手動ポストのまま）。
