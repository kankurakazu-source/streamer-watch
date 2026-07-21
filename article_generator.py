"""
article_generator.py
---------------------
ローカル記事生成パイプライン（アフィリエイト記事の自動下書き→サイト掲載→通知）。

流れ:
  1. game_watch.collect_all() で最新のゲームデータを収集
  2. 直近に公開済みのトピックを避けて、Claudeに「1本の記事」を書かせる
  3. Steam公式アート/割引を各タイトルに付与（画像・購入ボックス用）
  4. 記事HTMLを site/articles/<slug>.html として書き出す
  5. トップページ(index.html)の記事一覧を最新記事で更新
  6. Xポスト文面（要約＋記事リンク）を組み立て、メールで通知

投稿はしない（人間がメールを確認して手動でXポスト）。まずローカル検証用。

使い方:
    .venv\\Scripts\\python.exe article_generator.py
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import requests
from PIL import Image

import config
import storage
import article_render
import deals_tracker
import game_watch
import image_card
import trend_detector
from collectors import steam_collector
from collectors import rakuten_collector

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"

SYSTEM_PROMPT = """\
あなたは国内外のゲームトレンドをデータで解説する、アフィリエイト型ゲーム情報サイトの
編集ライターです。denfaminicogamer や FPS_G33KS のように、数字と一次情報に基づいた
読み応えのある記事を書きます。読者が「これは買い/要チェック」と判断できる実用記事が目標です。

【集客戦略＝トレンド・ハイジャック】
このメディアの生命線は「話題の瞬発力」です。新作の発売/配信開始、大型アップデート（新マップ・
新エージェント・パッチノート公開）、人気ゲーミングデバイスの予約開始/発売やGPUのベンチマーク、
プロ大会（eスポーツ）の結果——こうした「今まさに検索されているイベント」に乗って、
『どこよりも早いまとめ』を出すのが狙いです。速さと網羅性そのものが価値になります。
提供される「今狙うべきイベント」から最も鮮度と関心が高いものを主役に選んでください。

【反応（口コミ）の扱い＝重要】
「ユーザーのリアルな反応」は、Steam同接の前日比、Twitch視聴者数、YouTube急上昇といった
"測定できる数字"だけを根拠に要約します。実在しないSNSコメントや感想・引用を創作しては
いけません（例「盛り上がっている」ではなく「前日同接比+○%と数字が伸びている」と書く）。

記事の狙い:
- 提供データ／検出イベントから、今いちばん検索・話題になっているトピックを1つ選ぶ。
- 同時接続数の絶対値だけでなく「勢い（前日比の伸び）」も重視し、まだ無名でも伸びている作品を拾う。
- セール対象タイトルは購入導線（buy）を付け、読者の行動につなげる。

【文体＝必ず守る】
本文（lead / tldr / 各section.body / conclusion）は「だ・である調（常体）」で統一する。
「〜です」「〜ます」「〜ください」等の敬体は使わない。denfaminicogamer のような報道・
ゲームメディア調で、事実を簡潔に言い切る。
例:「販売中だ」「値下げが出そろった」「実用性の高い一台だ」「押さえておきたい」「〜といえる」
「〜が特徴だ」「〜する必要がある」。敬体（です・ます）が混ざらないよう最後に必ず見直す。

厳守する制約:
1. 本文は提供データ・公開情報に基づく事実のみ。数字は可能な範囲で入れるが、無い数字を作らない。
2. 価格（円）は書かない。割引率はデータにある discount_percent のみ触れてよい（無ければ触れない）。
3. 未確認の噂・リーク・発売日・炎上を断定しない。「〜のもよう」「〜との情報も」等でヘッジする。
4. 開発元や人物の発言、ユーザーの感想・口コミを捏造・誇張しない。反応は測定可能な数字のみ。
5. 比較値は「前日同接比+○%」のように具体的に。曖昧な「前回比」やデータに無い比較は書かない。
6. 半角ダブルクォート(")やバックスラッシュを本文に使わない。強調は「」を使う。
7. 内部データの英字フィールド名（prev_day_players_pct 等）を本文にそのまま書かない。
8. 大きな数字は読みやすく（例: 514299人→約51万人）。販売本数など切りの良い公式値はそのまま。
9. 誠実なトーン。過度な煽り・誹謗中傷はしない。読者の判断材料を提供する姿勢。
"""

# 安定プレフィックス（system＋この本文＝1回の実行の2記事で共通なのでプロンプトキャッシュ対象にする）。
# 可変部分（重複回避リスト・avoid_note）は USER_VOLATILE として後ろに置く（キャッシュ非対象）。
USER_STABLE = """\
以下は本日収集したゲーム関連データ（Twitch人気ゲーム/YouTube急上昇日米/Steam売上・新作・セール・同接/
国内外ニュース）です。

{data_json}

■ 今狙うべきイベント（トレンド・ハイジャック候補。スコア順。速報性・関心の高い順）:
{hot_events}

上記イベントの中から、いま最も検索・話題になっていて記事価値が高いものを【1つだけ】選び、
『どこよりも早いまとめ』となる1本の記事を書いてください。明確なイベントが無い場合のみ、
データから通常の考察記事を書いてかまいません。読者が得をする実用記事にします。

カテゴリは次から1つ選ぶ: {categories}

記事の構成:
- title: 具体的で内容が伝わる見出し（誇張しすぎない。速報なら「【速報】」等を付けてよい）
- category: 上記から1つ
- event_type: 乗ったイベントの種別。次から1つ: 新作・アプデ / デバイス / eスポーツ / 通常
- is_breaking: 速報性が高い（発売当日・アプデ直後・大会直後など鮮度勝負）なら true、じっくり考察なら false
- main_game: 記事の主役となる単一ゲームの正式名称（Steam画像検索用。日本語名可。複数まとめで主役が定まらなければ空）。
    ※デバイス記事(event_type=デバイス)では、この欄はSteam画像検索専用なので基本は空でよい（製品はSteamに無いため）。
- lead: リード文（2〜3文。何が起きていて、なぜ今読む価値があるか）
- tldr: 結論を一言で（迷ったら何を見る/買うべきか）
- sections: 3〜5個。各 {{heading, body, game_name, spec_table}}。
    heading: 小見出し（タイトル名を含めてよい）
    body: 2〜4文の本文（数字や根拠を入れる。段落は改行で区切ってよい）
    game_name: そのセクションで購入導線(buy)を出す対象の正式名称。ゲームならタイトル名、
    デバイス記事なら製品名（例: Lamzu Atlantis / RTX 5080 / 〇〇ゲーミングモニター）を入れてよい。
    購入導線が不要なら空文字。※デバイスはSteam画像が付かないが、Amazon/楽天の検索リンクは製品名から生成される。
    基本無料タイトル（例: Counter-Strike 2 / Dota 2 / Marvel Rivals / Apex Legends / VALORANT /
    Fortnite等）や、購入をすすめる文脈でない定番タイトルには購入導線を付けず空文字にする。
    購入導線は「いま買う理由がある」（セール中・新作・購入判断がテーマ）場合に限る。
    spec_table: 基本は空配列 []。動作環境・製品スペックのような「項目名: 値」型の仕様情報を
    整理するセクションでのみ、提供データにある値を {{label, value}} の行配列で表にする
    （例: label=推奨GPU, value=GeForce RTX 3060）。同接数・割引率・視聴者数など本文で述べる
    数字を表に重複させない。値の捏造は厳禁。
- conclusion: まとめ（2〜3文）
- x_main: 【親ポスト】記事リンクを貼らないX投稿本文。ここでインプレッションを稼ぐフック。
    「思わず手が止まる最新情報・数字・比較の要点」をテキストだけで完結させる（画像は別途こちらが添付）。
    日本語で約100字以内（全角2換算で230程度まで）。末尾に関連ハッシュタグを1〜2個入れてよい。
    URLは絶対に含めない（Xはリンク付き投稿の表示を大きく下げるため）。煽りすぎない。
- x_reply: 【リプ用の誘導文】親ポストのリプ欄に貼る一言。記事へ誘導するCTA。
    例「詳しいスペック比較はこちらの記事にまとめています👇」。URLは書かない（こちらで付ける）。20〜40字。
- hashtags: 0〜2個（#は付けず語だけ。トレンドに乗る語を選ぶ。例: VALORANT / Steamセール）
- topic_key: 重複検知用のキー。主役ゲーム名 or トピックを短い日本語で（例「Steamサマーセール」）
- faq: 読者が検索しそうな質問と回答を2〜3個。qは疑問文、aは1〜3文で、本文・提供データにある
    事実の範囲で答える（だ・である調）。
- ranking: ランキング連載記事（別途指示がある場合）専用。通常記事では value_label を空文字、
    rows を空配列 [] にする。
"""

# 可変部分（記事ごとに変わる＝重複回避リストと avoid_note）。安定部分の後ろに連結する（キャッシュ非対象）。
USER_VOLATILE = """\

直近に公開済みの記事タイトル（これらと重複しないトピックを選ぶこと。同一トピックは避ける）:
{recent_titles}{avoid_line}

※同一ゲームの扱い: 上記リストで同じゲームが直近2回以上主役になっている場合、そのゲームの
「続報だけの記事」（同接数値の更新のみ等）は書かない。発売・大型アップデート・歴代記録更新級の
明確な新イベントがある場合のみ再び主役にしてよい。それ以外は別のトピックを選ぶこと。
※同一イベントの扱いも同様: 上記リストに同じ大会・発表会・キャンペーン等のイベントを扱った記事が既に2本以上ある場合、そのイベントの続報は選ばない（優勝決定・正式発表のような決定的な新情報がある場合のみ例外）。"""

# エバーグリーン記事（資産型）指示。USER_STABLE（プロンプトキャッシュ対象）は変更したくないため、
# volatile側（avoid_noteと同じ経路）に連結する。週2回・2本目のみに付与する運用（main()参照）。
EVERGREEN_NOTE = """\

【重要: 今回は速報ニュースではなく、エバーグリーン記事（資産型）を書くこと】
今回は「今まさに検索されているイベント」の速報ではなく、半年後に読まれても価値が
失われない『比較・ランキング・選び方・買い時ガイド』型の記事を書く。トレンド・
ハイジャックの1トピック速報ではなく、じっくり読ませる保存版の記事にする。

- title: 検索されやすいキーワードを自然に含める（例:「おすすめ」「比較」「選び方」
    「初心者」「2026年版」等）。誇張しすぎない具体的な見出しにする。
- category: 「ガイド」を選ぶ。
- event_type: 「通常」を選ぶ。
- is_breaking: false にする（速報ではないため）。
- sections: 4〜6個で構成し、じっくり比較・整理する（3個以下にしない）。各セクションで
    扱うゲーム/製品には game_name を必ず入れて購入導線(buy)を付ける。
- 提供データ（Steam同接・割引・Twitch/YouTubeの数字）を根拠として活用すること。
    データに無い数字・スペックを作ってはいけない（既存の制約と同じ）。
- x_main: 「保存したくなるまとめ」系のフックにする（「保存版」「まとめて比較」等、
    後で読み返したくなる見出し）。速報感を出す煽り文句は避ける。
"""

# スペック解説記事（資産型）指示。EVERGREEN_NOTEと同じ経路（volatile側）で連結する。
SPEC_NOTE = """\

【重要: 今回は速報ではなく『推奨スペック・必要動作環境』解説記事（資産型）を書くこと】
今回は「今まさに検索されているイベント」の速報ではなく、Steam公式のPC動作環境データを
根拠にした『推奨スペック・必要動作環境』解説記事を書く。半年後に読まれても価値が失われない
保存版の記事にする。

以下の候補データ（Steam公式のPC動作環境。minimum=最低、recommended=推奨）から
1タイトルだけ選ぶこと:
{requirements_json}

- title:「◯◯の推奨スペック・必要動作環境まとめ」系。検索されやすい語（推奨スペック/
    必要スペック/快適に遊ぶ）を自然に含める。
- category:「ガイド」を選ぶ。event_type:「通常」を選ぶ。is_breaking: false にする。
- main_game: 選んだタイトルの正式名称。topic_key:「スペック:タイトル名」の形にする。
- sections: 4〜6個で構成する。
    ①最低動作環境の解説 ②推奨環境の解説（①か②のいずれかのsectionのspec_tableに、提供された
    動作環境の項目を {{label, value}} 行で必ず整理する。値は提供データにあるものだけ）
    ③GPU/CPUクラス別の快適度の目安（提供requirementsに書かれた型番を基準に「◯◯以上なら
    推奨環境を満たす」といった言い方にする。データに無いベンチ数値・fps・スコアを作らない）
    ④おすすめのGPU・ゲーミングPCの選び方（game_nameにGPU名等を入れて購入導線を付ける。
    例: RTX 4060 / RTX 5070） ⑤まとめ的なQ&A的整理など。
- 提供requirementsに無いスペック値・数値は絶対に書かない。requirementsが英語表記なら
    そのまま型番は英語でよい。
- 価格（円）は書かない。データに無い数字を作らない。文体は既存の制約通り、だ・である調で統一する。
"""

# セール買い時解説記事（資産型）指示。EVERGREEN_NOTEと同じ経路（volatile側）で連結する。
SALE_NOTE = """\

【重要: 今回は速報ではなく『セール買い時』解説記事（資産型）を書くこと】
今回は「今まさに検索されているイベント」の速報ではなく、当サイトが毎日自動記録している
割引実績データを根拠にした『セール買い時』解説記事を書く。半年後に読まれても価値が
失われない保存版の記事にする。

以下は当サイトが毎日自動記録している主要タイトルの割引実績データ（current_discount=現在割引%、
max_discount=計測期間内の最大割引%、last_sale_date=直近セール日、tracked_days=計測日数、
verdict=当サイトの買い時判定）:
{deals_json}

- この中から記事価値が最も高い1タイトルを主役に選ぶ（優先: verdictが「買い時」→「セール中」→
    max_discount>0でtracked_daysが長いもの。直近公開済みタイトル一覧に同タイトルの買い時記事が
    あるものは選ばない）。
- title:「◯◯のセールはいつ?過去の割引実績から見る買い時」系。category:「セール分析」を選ぶ。
    is_breaking: 現在セール中ならtrueでもよい。main_game: 選んだタイトル。
    topic_key:「買い時:タイトル名」の形にする。
- sections: 現在のセール状況→計測データから見る割引傾向→買い時判定の根拠→今買うべきか
    待つべきか、等で構成する。主役タイトルを扱うsectionにgame_nameを入れて購入導線を付ける。
- 割引%と日付は提供データの値のみ使う。計測期間が短い場合（tracked_daysが小さい）は
    「計測開始からN日のデータに基づく」と正直に書く。円価格・セール終了日の断定は書かない。
- データに無い数字を作らない。文体は既存の制約通り、だ・である調で統一する。
"""

# 週間定点レポート（Steam同接ランキング）指示。EVERGREEN_NOTEと同じ経路（volatile側）で連結する。
WEEKLY_NOTE = """\

【重要: 今回は週間定点レポート『Steam同接ランキング』を書くこと（毎週日曜の連載）】
今回は「今まさに検索されているイベント」の速報ではなく、当サイト実測の週間同接データを
根拠にした定点観測レポートを書く。毎週日曜に公開する連載企画である。

集計期間: {week_range}

以下は当サイト実測の週間同接データ（latest=最新同接、peak=週間ピーク、week_ago=約1週間前、
growth_pct=週間増減%、peak降順）:
{weekly_json}

- title:「【週間】Steam同接ランキングTOP◯」＋期間がわかる表現にする。category:「データ分析」を
    選ぶ。event_type:「通常」を選ぶ。is_breaking: false にする。
    main_game: ランキング1位のタイトル。topic_key:「週間同接:{week_range}」の形にする。
- sections: TOP3の動向解説 → 伸び率が大きい注目株 → 下がったタイトルとその背景（データで
    言える範囲）→ 来週の注目ポイント、等で構成する。
- 購入導線(game_name)は「いま買う理由がある」タイトル（セール中・急伸中の買い切りタイトル等）
    最大2セクションに絞る。基本無料タイトルには付けない。加えて1セクションで「快適に遊ぶための
    デバイス」を提案してよい（該当ランキングの人気ジャンルに合う具体的な製品カテゴリ。例:
    ゲーミングヘッドセット / 240Hzモニター / RTX 4060。そのsectionのgame_nameに製品名を入れる）。
    デバイスのスペック値を捏造しないこと（一般的な用途提案にとどめる）。
- ranking: 提供データの全タイトル（最大10件）を必ず埋める。rankはpeak降順の順位（1から）。
    nameはタイトル名。valueは週間ピーク値を読みやすく（例: 約141万人 / 約7万8000人）。
    changeはgrowth_pctを「+16.4%」「-13.3%」形式で（値がnullなら「-」）。value_labelは
    「週間ピーク同接」とする。
- 数字は提供データのみ使う。データに無い順位変動理由を断定しない（「〜が要因とみられる」
    程度のヘッジにとどめる）。価格（円）は書かない。文体は既存の制約通り、だ・である調で統一する。
"""

# 週間定点レポート（配信で人気のゲームランキング）指示。WEEKLY_NOTEと同じ経路（volatile側）で連結する。
STREAM_NOTE = """\

【重要: 今回は週間定点レポート『配信で人気のゲームランキング』を書くこと（毎週月曜の連載）】
速報ではなく、当サイト実測のTwitch配信データを根拠にした定点観測レポートを書く。

集計期間: {week_range}

以下は当サイト実測の週間配信データ（latest=最新値、peak=週間ピーク、week_ago=約1週間前、
growth_pct=週間増減%、peak降順）。※この数字は「Twitchの上位配信に含まれる合計視聴者数」を
当サイトが定点集計したもので、Twitch全体の視聴者総数ではない:
{stream_json}

- title:「【週間】配信で人気のゲームランキングTOP◯」＋期間がわかる表現にする。category:
    「データ分析」を選ぶ。event_type:「通常」を選ぶ。is_breaking: false にする。
    main_game: ランキング1位のタイトル。topic_key:「週間配信:{week_range}」の形にする。
- 記事冒頭〜早い段階で、数字の意味（上位配信の合計視聴者数の定点集計であり全体総数ではない）を
    1文で正確に説明すること。
- sections: TOP3の配信動向 → 伸び率が大きい急上昇タイトル → 「見て楽しむゲームか、自分で遊ぶ
    ゲームか」（提供データ内にSteam同接があるタイトルは配信視聴と実プレイの比較で考察） →
    来週の注目、等で構成する。
- 購入導線(game_name)は「いま買う理由がある」タイトル（セール中・急伸中の買い切りタイトル等）
    最大2セクションに絞る。基本無料タイトルには付けない。加えて1セクションで「快適に遊ぶための
    デバイス」を提案してよい（該当ランキングの人気ジャンルに合う具体的な製品カテゴリ。例:
    ゲーミングヘッドセット / 240Hzモニター / RTX 4060。そのsectionのgame_nameに製品名を入れる）。
    デバイスのスペック値を捏造しないこと（一般的な用途提案にとどめる）。
- ranking: 提供データの全タイトル（最大10件）を必ず埋める。rankはpeak降順の順位（1から）。
    nameはタイトル名。valueは週間ピーク値を読みやすく（例: 約141万人 / 約7万8000人）。
    changeはgrowth_pctを「+16.4%」「-13.3%」形式で（値がnullなら「-」）。value_labelは
    「週間ピーク視聴者数」とする。
- 数字は提供データのみ使う。配信者個人の名前・動向は書かない（データに無いため）。データに
    無い順位変動理由を断定しない（「〜とみられる」程度のヘッジにとどめる）。価格（円）は
    書かない。文体は既存の制約通り、だ・である調で統一する。
"""

# Twitchの上位カテゴリに入る非ゲーム枠。「配信で人気のゲーム」ランキングから除外する。
_NON_GAME_TWITCH = {
    "Just Chatting", "IRL", "Music", "Sports", "Special Events", "Slots",
    "Casino", "Virtual Casino", "ASMR", "Talk Shows & Podcasts", "Art",
    "Pools, Hot Tubs, and Beach", "Animals, Aquariums, and Zoos", "Watch Parties",
}

ARTICLE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "category": {"type": "string", "enum": config.ARTICLE_CATEGORIES},
        "event_type": {"type": "string", "enum": ["新作・アプデ", "デバイス", "eスポーツ", "通常"]},
        "is_breaking": {"type": "boolean"},
        "main_game": {"type": "string"},
        "lead": {"type": "string"},
        "tldr": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "heading": {"type": "string"},
                    "body": {"type": "string"},
                    "game_name": {"type": "string"},
                    "spec_table": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "value": {"type": "string"},
                            },
                            "required": ["label", "value"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["heading", "body", "game_name", "spec_table"],
                "additionalProperties": False,
            },
        },
        "conclusion": {"type": "string"},
        "x_main": {"type": "string"},
        "x_reply": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "topic_key": {"type": "string"},
        "faq": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "q": {"type": "string"},
                    "a": {"type": "string"},
                },
                "required": ["q", "a"],
                "additionalProperties": False,
            },
        },
        "ranking": {
            "type": "object",
            "properties": {
                "value_label": {"type": "string"},
                "rows": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "rank": {"type": "integer"},
                            "name": {"type": "string"},
                            "value": {"type": "string"},
                            "change": {"type": "string"},
                        },
                        "required": ["rank", "name", "value", "change"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["value_label", "rows"],
            "additionalProperties": False,
        },
    },
    "required": ["title", "category", "event_type", "is_breaking", "main_game", "lead", "tldr",
                 "sections", "conclusion", "x_main", "x_reply", "hashtags", "topic_key", "faq",
                 "ranking"],
    "additionalProperties": False,
}


def _build_steam_maps(collected: dict) -> tuple[dict, dict]:
    """収集済みSteamデータから name->appid と appid->discount_percent を作る。"""
    name_to_appid: dict[str, int] = {}
    appid_discount: dict[int, int] = {}
    for r in collected.get("steam_players", []) or []:
        if r.get("appid") and r.get("name"):
            name_to_appid[r["name"]] = r["appid"]
    feat = collected.get("steam_featured") or {}
    for cat_items in feat.values():
        for it in cat_items or []:
            if it.get("appid") and it.get("name"):
                name_to_appid[it["name"]] = it["appid"]
                if it.get("discount_percent"):
                    appid_discount[int(it["appid"])] = int(it["discount_percent"])
    return name_to_appid, appid_discount


def _resolve_game(name: str, name_to_appid: dict, appid_discount: dict) -> dict:
    """ゲーム名 -> {name, image_url, discount_percent?, appid}。解決できなければ画像なし。"""
    out = {"name": name}
    if not name:
        return out
    try:
        appid = game_watch._resolve_steam_appid({"main_game": name}, name_to_appid)
    except Exception:
        appid = None
    if appid:
        out["appid"] = appid
        out["image_url"] = article_render.steam_image_url(appid)
        if appid_discount.get(appid):
            out["discount_percent"] = appid_discount[appid]
    return out


def _enrich(article: dict, collected: dict, deals_data: list[dict] | None = None) -> tuple[str, list[str]]:
    """
    記事にSteam画像/割引を付与。hero と各セクションのバナー画像には、同じ画像の使い回しを
    避けるため「まだ使っていない別の画像（スクリーンショット等）」を順に割り当てる。
    deals_data があれば、各セクションの購入導線(buy)に買い時トラッカーの判定情報も付与する
    （描画側との契約。キー名厳守）。
    戻り値: (hero画像URL, 割引付きゲーム名の一覧ログ)。
    """
    name_to_appid, appid_discount = _build_steam_maps(collected)
    deal_by_appid = {d["appid"]: d for d in (deals_data or []) if d.get("appid")}
    log = []

    pools: dict[int, list[str]] = {}   # appid -> 画像URL群（キャッシュ）
    used: set[str] = set()             # 記事内で既に使った画像
    rk_cache: dict[str, str] = {}      # 製品名 -> 楽天商品画像URL（キャッシュ）

    def rakuten_image(name: str) -> str:
        """Steam画像が無い製品(デバイス等)向け: 楽天商品検索で実画像を1枚取得。"""
        name = (name or "").strip()
        if not name or not (config.RAKUTEN_APP_ID and config.RAKUTEN_ACCESS_KEY):
            return ""
        if name not in rk_cache:
            try:
                rk_cache[name] = rakuten_collector.search_image(
                    name, config.RAKUTEN_APP_ID,
                    access_key=config.RAKUTEN_ACCESS_KEY,
                    referrer=config.RAKUTEN_REFERRER,
                )
            except Exception:
                rk_cache[name] = ""
        return rk_cache[name]

    def pick_image(appid, name: str, fallback: str) -> str:
        """appidのSteam画像→無ければ楽天商品画像→fallback、の順で1枚選ぶ（記事内で重複回避）。"""
        if appid:
            if appid not in pools:
                try:
                    pools[appid] = steam_collector.fetch_image_urls(appid)
                except Exception:
                    pools[appid] = []
            for u in pools[appid]:
                if u not in used:
                    used.add(u)
                    return u
            if pools[appid]:
                return pools[appid][0]
        # Steam画像なし → 楽天商品画像（製品名で検索）
        r = rakuten_image(name)
        if r:
            if r not in used:
                used.add(r)
            return r
        return fallback

    # hero画像（主役ゲーム。デバイス記事は main_game 空のことが多いので先頭セクションの製品名で代替）
    hero_url = ""
    main = (article.get("main_game") or "").strip()
    if main:
        g = _resolve_game(main, name_to_appid, appid_discount)
        article["main_appid"] = g.get("appid")  # 親ポスト添付画像の生成に使う
        hero_url = pick_image(g.get("appid"), main, g.get("image_url", ""))
    if not hero_url:
        secs = article.get("sections", []) or []
        first_name = next((s.get("game_name", "").strip() for s in secs if s.get("game_name")), "")
        if first_name:
            hero_url = rakuten_image(first_name)
    article["hero_image_url"] = hero_url

    # 各セクション：購入ボックス＋（使い回さない）バナー画像
    for sec in article.get("sections", []):
        gname = (sec.get("game_name") or "").strip()
        if not gname:
            sec["buy"] = {}
            sec["image_url"] = ""
            continue
        g = _resolve_game(gname, name_to_appid, appid_discount)
        img = pick_image(g.get("appid"), gname, g.get("image_url", ""))
        if img and not g.get("image_url"):
            g["image_url"] = img   # 購入ボックスのサムネにも楽天画像を反映
        sec["buy"] = g
        sec["image_url"] = img
        deal = deal_by_appid.get(g.get("appid"))
        if deal:
            sec["buy"]["deal"] = {
                "current_discount": deal.get("current_discount"),
                "max_discount": deal.get("max_discount"),
                "last_sale_date": deal.get("last_sale_date"),
                "verdict": deal.get("verdict"),
                "tracked_days": deal.get("tracked_days"),
            }
        if g.get("discount_percent"):
            log.append(f"{gname} -{g['discount_percent']}%")
    return hero_url, log


def generate_article(collected: dict, recent: list[dict], api_key: str,
                     hot_events_text: str = "", model: str = "claude-sonnet-5",
                     avoid_note: str = "", extra_note: str = "") -> dict:
    """Claudeに1本の記事を書かせる。structured outputsでJSON構造を強制。
    avoid_note: 同一実行で複数本書く際、既出カテゴリ/トピックを避けさせる追加指示。
    extra_note: 特殊記事スロット（エバーグリーン/スペック/セール/週間レポート）用の追加指示。
    空文字なら通常記事のまま。_build_special_note() で組み立てたNOTEを渡す想定。

    プロンプトキャッシュ: 安定部分(system＋収集データ＋イベント＋構成指示)を cache_control で
    キャッシュし、可変部分(重複回避リスト・avoid_note・extra_note)を後ろに置く。1回の実行で
    2本目以降は巨大な入力(収集データ)がキャッシュ読み出しになり入力コストが大幅に下がる。出力は不変。
    extra_note は USER_STABLE ではなく volatile 側に付けることでキャッシュを壊さない。"""
    recent_titles = "\n".join(f"- {r['title']}" for r in recent) or "(まだありません)"
    stable_text = USER_STABLE.format(
        data_json=json.dumps(collected, ensure_ascii=False, indent=2),
        hot_events=hot_events_text or "（検出なし）",
        categories=" / ".join(config.ARTICLE_CATEGORIES),
    )
    volatile_text = USER_VOLATILE.format(
        recent_titles=recent_titles,
        avoid_line=("\n" + avoid_note if avoid_note else ""),
    )
    if extra_note:
        volatile_text += extra_note
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    body = {
        "model": model,
        "max_tokens": 4000,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": stable_text, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": volatile_text},
        ]}],
        "thinking": {"type": "disabled"},
        "output_config": {"format": {"type": "json_schema", "schema": ARTICLE_SCHEMA}},
    }
    resp = requests.post(ANTHROPIC_API_URL, headers=headers, json=body, timeout=90)
    # 万一キャッシュ指定が拒否されても記事生成を止めない（プレーン結合で再送＝品質は同じ）
    if resp.status_code == 400 and "cache" in resp.text.lower():
        print("[WARN] prompt cache 非対応のためフォールバック（コスト削減は無効・品質は不変）")
        body["messages"] = [{"role": "user", "content": stable_text + volatile_text}]
        resp = requests.post(ANTHROPIC_API_URL, headers=headers, json=body, timeout=90)
    if resp.status_code != 200:
        raise RuntimeError(f"Anthropic API失敗 (HTTP {resp.status_code}): {resp.text}")
    result = resp.json()
    u = result.get("usage", {}) or {}
    print(f"[usage] input={u.get('input_tokens')} "
          f"cache_write={u.get('cache_creation_input_tokens')} "
          f"cache_read={u.get('cache_read_input_tokens')} output={u.get('output_tokens')}")
    text = "\n".join(c["text"] for c in result.get("content", []) if c.get("type") == "text").strip()
    text = text.replace("```json", "").replace("```", "").strip()
    return json.loads(text)


def _fallback_hero_card(article: dict, slug: str) -> str:
    """
    hero画像が無い記事（Steamにも楽天にも画像が無いタイトル）向けの最終フォールバック。
    Xポスト用のテキストカード生成(image_card.render_card)を流用し、著作権リスクのない
    オリジナルのタイトルカードをhero画像として作る。
    成功時は "/assets/cards/{slug}.jpg" を返す。失敗時は例外を握りつぶして空文字を返す。
    """
    cards_dir = os.path.join(config.SITE_DIR, "assets", "cards")
    tmp_png = os.path.join(cards_dir, f"{slug}.tmp.png")
    out_jpg = os.path.join(cards_dir, f"{slug}.jpg")
    try:
        os.makedirs(cards_dir, exist_ok=True)
        draft = {
            "type": "速報" if article.get("is_breaking") else "考察",
            "headline": article.get("title", ""),
            "bullets": [article["tldr"]] if article.get("tldr") else [],
        }
        image_card.render_card(draft, tmp_png)
        with Image.open(tmp_png) as im:
            im.convert("RGB").save(out_jpg, "JPEG", quality=82, optimize=True)
        return f"/assets/cards/{slug}.jpg"
    except Exception as e:
        print(f"[WARN] フォールバックhero画像の生成に失敗: {e}")
        return ""
    finally:
        if os.path.exists(tmp_png):
            os.remove(tmp_png)


def publish(article: dict, hero_url: str, seq: int = 1) -> dict:
    """記事HTMLを書き出し、DBに登録し、トップページの一覧を更新。戻り値: メタ情報。
    seq: 同一実行で複数本公開する際にスラッグを分けるための連番(1,2,...)。"""
    now = datetime.now()
    slug = article_render.slugify(now, seq=seq)
    articles_dir = os.path.join(config.SITE_DIR, config.ARTICLES_SUBDIR)
    os.makedirs(articles_dir, exist_ok=True)

    # OGP用の正規URL（公開URLベースがあれば）。render_article がog:url/canonicalに使う。
    article["canonical_url"] = build_public_url(slug)

    # 連載記事（週間同接:/週間配信:）の前回記事リンクを付与（描画側との契約。無ければキー自体なし）
    topic_key = (article.get("topic_key") or "")
    if topic_key.startswith("週間") and ":" in topic_key:
        prefix = topic_key.split(":", 1)[0] + ":"
        try:
            prev = storage.latest_article_by_topic_prefix(config.HISTORY_DB, prefix, exclude_slug=slug)
            if prev:
                article["prev_in_series"] = prev
        except Exception as e:
            print(f"[WARN] 連載前回記事の取得に失敗: {e}")

    # hero画像が無い記事（Steamにも楽天にも画像が無いタイトル）は、テキストカードで代替する。
    if not hero_url:
        fb = _fallback_hero_card(article, slug)
        if fb:
            hero_url = fb
            article["hero_image_url"] = fb

    # 記事HTML（末尾の「人気の記事」用に既存記事を渡す。current はまだ未登録なので自然に除外される）
    try:
        related = storage.list_articles(config.HISTORY_DB, limit=12)
    except Exception:
        related = []
    html_str = article_render.render_article(article, related=related)
    article_path = os.path.join(articles_dir, f"{slug}.html")
    with open(article_path, "w", encoding="utf-8") as f:
        f.write(html_str)

    # DB登録（抜粋はleadを流用）
    excerpt = (article.get("lead") or "")[:120]
    storage.save_article(
        config.HISTORY_DB, slug, article.get("title", ""), article.get("category", ""),
        article.get("topic_key", ""), excerpt, hero_url,
        is_breaking=bool(article.get("is_breaking")),
        event_type=article.get("event_type", ""),
    )

    # トップページの記事一覧を最新12件で再生成
    index_path = os.path.join(config.SITE_DIR, "index.html")
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            index_html = f.read()
        arts = storage.list_articles(config.HISTORY_DB, limit=12)
        new_index = article_render.inject_homepage(index_html, arts)
        if new_index != index_html:
            with open(index_path, "w", encoding="utf-8") as f:
                f.write(new_index)
    except FileNotFoundError:
        print("[WARN] site/index.html が見つからず、一覧更新はスキップしました。")

    # 全記事アーカイブ／カテゴリ別ページを最新の記事一覧で再生成（失敗しても記事公開は止めない）
    try:
        all_arts = storage.list_articles(config.HISTORY_DB, limit=10000)
        article_render.render_archive_pages(all_arts, config.SITE_DIR, config.SITE_BASE_URL)
        article_render.render_category_pages(all_arts, config.SITE_DIR, config.SITE_BASE_URL)
    except Exception as e:
        print(f"[WARN] アーカイブ/カテゴリページの生成に失敗: {e}")

    # sitemap.xml / robots.txt を最新の記事一覧・アーカイブ/カテゴリページで更新
    try:
        article_render.write_sitemap(config.SITE_DIR, config.SITE_BASE_URL)
    except Exception as e:
        print(f"[WARN] sitemap生成に失敗: {e}")

    return {"slug": slug, "path": article_path}


def build_post_image(article: dict, slug: str) -> str | None:
    """
    親ポストにそのまま添付できる画像(PNG)を生成して返す。
    - Steam公式アートが取れれば、それを敷いた「連想カード」(種別バッジ付き)。
    - 取れなければ、タイトル＋結論のテキストカード（著作権リスクなし）。
    失敗時は None。
    """
    # メール添付専用（デプロイ不要）なので output/ 配下に出す
    card_dir = os.path.join(config.OUTPUT_DIR, "cards")
    out_path = os.path.join(card_dir, f"article_{slug}.png")
    ctype = "速報" if article.get("is_breaking") else "考察"
    appid = article.get("main_appid")

    try:
        if appid:
            # 記事ごとに絵柄が変わるよう、スクショ群から1枚を選んで使う（無ければ従来のヘッダー系）
            img_bytes = None
            urls = steam_collector.fetch_image_urls(appid)
            if urls:
                idx = abs(hash(slug)) % len(urls)
                try:
                    r = requests.get(urls[idx], timeout=10)
                    if r.status_code == 200 and len(r.content) > 3000:
                        img_bytes = r.content
                except requests.exceptions.RequestException:
                    img_bytes = None
            if not img_bytes:
                img_bytes = steam_collector.fetch_game_image_bytes(appid)
            if img_bytes:
                draft = {"type": ctype, "headline": article.get("title", "")}
                return image_card.render_art_card(img_bytes, draft, out_path, "画像: Steam")
        else:
            # Steam非対象（デバイス等）: 楽天等の商品画像(hero_image_url)を全体表示カードに
            hero = (article.get("hero_image_url") or "").strip()
            if hero:
                try:
                    r = requests.get(hero, timeout=10)
                    if r.status_code == 200 and len(r.content) > 2000:
                        draft = {"type": ctype, "headline": article.get("title", "")}
                        return image_card.render_product_card(
                            r.content, draft, out_path, "画像: 楽天市場")
                except requests.exceptions.RequestException:
                    pass
        # フォールバック: タイトル＋結論のテキストカード
        draft = {
            "type": ctype,
            "headline": article.get("title", ""),
            "bullets": [b for b in [article.get("tldr", "")] if b],
        }
        return image_card.render_card(draft, out_path)
    except Exception as e:
        print(f"[WARN] 親ポスト画像の生成に失敗: {e}")
        return None


def build_public_url(slug: str) -> str:
    """公開URL（SITE_BASE_URLがあれば）。無ければ空（未公開＝Xにはまだ載せられない）。
    Cloudflare Pagesが.html付きURLを拡張子なしに308リダイレクトするため、拡張子なしの正規URLを返す。
    """
    base = (config.SITE_BASE_URL or "").strip()
    if not base:
        return ""
    return f"{base.rstrip('/')}/{config.ARTICLES_SUBDIR}/{slug}"


# --- 実行成功マーカー（1時間後リトライの二重生成防止に使う） ---
_RUNS_DIR = os.path.join(config.OUTPUT_DIR, "runs")
_LAST_SUCCESS = os.path.join(_RUNS_DIR, "last_success.txt")


def _mark_success():
    """記事を作成できた時刻を記録する。"""
    try:
        os.makedirs(_RUNS_DIR, exist_ok=True)
        with open(_LAST_SUCCESS, "w", encoding="utf-8") as f:
            f.write(datetime.now().isoformat())
    except Exception as e:
        print(f"[WARN] 成功マーカーの記録に失敗: {e}")


def _recent_success(minutes: int = 70) -> bool:
    """直近 minutes 分以内に記事作成が成功していれば True。"""
    try:
        t = datetime.fromisoformat(open(_LAST_SUCCESS, encoding="utf-8").read().strip())
        return (datetime.now() - t).total_seconds() < minutes * 60
    except Exception:
        return False


def _publish_and_notify(article: dict, collected: dict, seq: int,
                        deals_data: list[dict] | None = None) -> None:
    """1本の記事を公開し、Xポスト文面を表示、メール通知する。
    deals_data: セール・買い時トラッカーの集計結果（buyの買い時情報付与用）。"""
    hero_url, disc_log = _enrich(article, collected, deals_data=deals_data)

    # 同接推移グラフ用データ付与（描画側との契約。キー構造厳守）
    appid = article.get("main_appid")
    if appid:
        try:
            pts = storage.get_metric_history(config.HISTORY_DB, "steam", str(appid),
                                              "player_count", days=14)
            dates = {p[0][:10] for p in pts}
            if len(pts) >= 5 and len(dates) >= 3:
                article["player_chart"] = {
                    "name": article.get("main_game", ""),
                    "points": [{"t": t, "v": int(v)} for t, v in pts],
                }
        except Exception as e:
            print(f"[WARN] 同接推移データの取得に失敗: {e}")

    # ランキング記事のセクション別グラフ（描画側との契約。sec["player_chart"]は既存article["player_chart"]と同形）
    ranking = article.get("ranking") or {}
    if ranking.get("rows"):
        added = 0
        for sec in article.get("sections", []):
            if added >= 2:
                break
            buy = sec.get("buy") or {}
            sec_appid = buy.get("appid")
            if not sec_appid or sec_appid == appid:
                continue
            try:
                pts = storage.get_metric_history(config.HISTORY_DB, "steam", str(sec_appid),
                                                  "player_count", days=14)
                dates = {p[0][:10] for p in pts}
                if len(pts) >= 5 and len(dates) >= 3:
                    sec["player_chart"] = {
                        "name": buy.get("name", ""),
                        "points": [{"t": t, "v": int(v)} for t, v in pts],
                    }
                    added += 1
            except Exception as e:
                print(f"[WARN] セクション別同接推移データの取得に失敗: {e}")

    meta = publish(article, hero_url, seq=seq)
    public_url = build_public_url(meta["slug"])
    linkless = getattr(config, "X_LINKLESS_MODE", False)
    thread = article_render.build_x_thread(article, public_url, linkless=linkless)
    post_image = build_post_image(article, meta["slug"])  # 親ポストに添付するPNG

    print(f"=== 公開: {meta['path']} ===")
    print(f"タイトル: {article.get('title','')}")
    print(f"種別: {article.get('event_type','-')} / 速報={article.get('is_breaking')} "
          f"/ カテゴリ: {article.get('category','')} / セクション{len(article.get('sections',[]))}個"
          + (f" / セール検知: {', '.join(disc_log)}" if disc_log else ""))
    if linkless:
        print("\n--- Xポスト（リンク無し・単発／検索ban対策モード）---")
        print(f"[投稿・画像付き/リンクなし] ({thread['main_weight']}/280)")
        print(thread["main"])
        print(f"[添付画像] {post_image or '(生成なし)'}")
    else:
        print("\n--- Xポスト（2ステップ）---")
        print(f"[親ポスト・画像付き/リンクなし] ({thread['main_weight']}/280)")
        print(thread["main"])
        print(f"[親ポスト添付画像] {post_image or '(生成なし)'}")
        print(f"\n[リプ・記事リンク] ({thread['reply_weight']}/280)")
        print(thread["reply"])
    if not public_url:
        print(f"\n[INFO] 公開URL未設定（config.SITE_BASE_URL が空）。ローカル確認用パス:")
        print(f"  file:///{os.path.abspath(meta['path']).replace(os.sep, '/')}")

    if config.email_enabled():
        try:
            import emailer
            local_path = os.path.abspath(meta["path"])
            emailer.send_article_email(
                article, thread, public_url, local_path, hero_url, post_image,
                config.SMTP_HOST, config.SMTP_PORT,
                config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD,
                config.GMAIL_ADDRESS, config.EMAIL_TO,
            )
            print(f"=== メール送信済み: {config.EMAIL_TO} ===")
        except Exception as e:
            print(f"[WARN] メール送信失敗: {e}")
    else:
        print("[INFO] メール未設定（.envにGMAIL_ADDRESS/GMAIL_APP_PASSWORDを入れると送信します）")


# 縮小運転(2026-07-22〜)の特殊記事プラン: 15時枠は曜日ごとに種別を割り当てる。
# 月=stream(週末の配信データ直後) 火=evergreen 水=sale 木=spec 金=evergreen 土=sale 日=weekly
_SLOT_15 = {0: "stream", 1: "evergreen", 2: "sale", 3: "spec",
            4: "evergreen", 5: "sale", 6: "weekly"}


def _special_slot(now: datetime | None = None) -> str | None:
    """自動で特殊記事を混ぜる時間帯かどうかを判定し、種別を返す（無ければNone）。
    該当実行の2本目の記事だけを特殊記事にする運用（main()側で判定）。

    縮小運転（1日2回: primary15:00/23:00＋retry16:00/翌0:00）用の割り当て:
    - 15時枠（14〜17時台。primary15:00/retry16:00をカバー）: _SLOT_15 の曜日別割り当て。
        evergreen=比較・選び方ガイド / spec=推奨スペック解説 / sale=買い時解説 /
        weekly=週間Steam同接ランキング(日曜連載) / stream=配信人気ランキング(月曜連載)
    - 23時枠（22〜23時台。primary23:00のみ）: 月曜=spec（週2本目のスペック記事）。
        ※翌0時のretryは日付をまたぎ判定が変わるため特殊記事にはならない（通常記事で代替）。
    """
    now = now or datetime.now()
    # Python の weekday(): 月=0, 火=1, 水=2, 木=3, 金=4, 土=5, 日=6
    weekday, hour = now.weekday(), now.hour
    if 14 <= hour <= 17:
        return _SLOT_15.get(weekday)
    if weekday == 0 and 22 <= hour <= 23:
        return "spec"
    return None


def _build_special_note(kind: str | None, collected: dict, deals_data: list[dict],
                        recent: list[dict]) -> str:
    """特殊記事スロット用の追加指示(NOTE)を組み立てる。
    kind: "evergreen" / "spec" / "sale" / "weekly" / "stream" / None(通常記事)。
    データが足りず組み立てられない場合は空文字を返し、呼び出し側で通常記事にフォールバックさせる。
    特殊記事の失敗で通常記事生成そのものを止めないよう、例外はすべて握りつぶす。"""
    if not kind:
        return ""
    try:
        if kind == "evergreen":
            return EVERGREEN_NOTE

        if kind == "sale":
            if not deals_data:
                print("[WARN] 買い時記事用のdeals_dataが空のため通常記事にフォールバックします。")
                return ""
            picks = [
                {
                    "appid": d.get("appid"),
                    "name": d.get("name"),
                    "current_discount": d.get("current_discount"),
                    "max_discount": d.get("max_discount"),
                    "last_sale_date": d.get("last_sale_date"),
                    "tracked_days": d.get("tracked_days"),
                    "verdict": d.get("verdict"),
                }
                for d in deals_data
            ]
            return SALE_NOTE.format(deals_json=json.dumps(picks, ensure_ascii=False))

        if kind == "weekly":
            weekly = storage.weekly_player_summary(config.HISTORY_DB)
            if not weekly:
                print("[WARN] 週間レポート用のデータが無いため通常記事にフォールバックします。")
                return ""
            now_jst = datetime.now()
            week_range = (f"{(now_jst - timedelta(days=7)).strftime('%Y-%m-%d')}"
                          f"〜{now_jst.strftime('%Y-%m-%d')}")
            return WEEKLY_NOTE.format(
                week_range=week_range,
                weekly_json=json.dumps(weekly, ensure_ascii=False),
            )

        if kind == "stream":
            # Just Chatting等の非ゲームカテゴリはランキングから除外する（余分に取って絞る）
            raw = storage.weekly_twitch_summary(config.HISTORY_DB, limit=16)
            stream = [r for r in raw if r.get("name") not in _NON_GAME_TWITCH][:10]
            if not stream:
                print("[WARN] 週間配信レポート用のデータが無いため通常記事にフォールバックします。")
                return ""
            now_jst = datetime.now()
            week_range = (f"{(now_jst - timedelta(days=7)).strftime('%Y-%m-%d')}"
                          f"〜{now_jst.strftime('%Y-%m-%d')}")
            return STREAM_NOTE.format(
                week_range=week_range,
                stream_json=json.dumps(stream, ensure_ascii=False),
            )

        if kind == "spec":
            candidates = []
            seen_appids = set()
            for r in (collected.get("steam_players") or [])[:5]:
                if r.get("appid") and r["appid"] not in seen_appids:
                    seen_appids.add(r["appid"])
                    candidates.append({"appid": r["appid"], "name": r.get("name", "")})
            new_releases = (collected.get("steam_featured") or {}).get("new_releases", []) or []
            for it in new_releases[:5]:
                if it.get("appid") and it["appid"] not in seen_appids:
                    seen_appids.add(it["appid"])
                    candidates.append({"appid": it["appid"], "name": it.get("name", "")})

            # 直近にスペック記事化済みのタイトルは除外する
            def _already_spec_covered(name: str) -> bool:
                key = (name or "")[:10]
                if not key:
                    return False
                for r in recent or []:
                    text = f"{r.get('title', '')} {r.get('topic_key', '')}"
                    if key in text and "スペック" in text:
                        return True
                return False

            picked = []
            for c in candidates:
                if _already_spec_covered(c["name"]):
                    continue
                try:
                    reqs = steam_collector.fetch_requirements(c["appid"])
                except Exception:
                    reqs = {"minimum": "", "recommended": ""}
                time.sleep(0.5)
                if reqs.get("minimum") or reqs.get("recommended"):
                    picked.append({
                        "name": c["name"], "appid": c["appid"],
                        "minimum": reqs.get("minimum", ""),
                        "recommended": reqs.get("recommended", ""),
                    })
                if len(picked) >= 3:
                    break

            if not picked:
                print("[WARN] スペック記事用の動作環境データが取得できず通常記事にフォールバックします。")
                return ""
            return SPEC_NOTE.format(requirements_json=json.dumps(picked, ensure_ascii=False))
    except Exception as e:
        print(f"[WARN] 特殊記事({kind})の準備に失敗したため通常記事にフォールバックします: {e}")
        return ""
    return ""


def main():
    ap = argparse.ArgumentParser(description="ガジェゲ 記事生成")
    ap.add_argument("--count", type=int, default=1, help="1回で作る記事数（既定1）")
    ap.add_argument("--mode", choices=["primary", "retry"], default="primary",
                    help="retry は直近70分以内に成功していればスキップ（1時間後リトライ用）")
    ap.add_argument("--evergreen", action="store_true",
                    help="指定時はこの実行の全記事をエバーグリーン記事にする（手動テスト・強制用、--special evergreen と同義）")
    ap.add_argument("--special", choices=["evergreen", "spec", "sale", "weekly", "stream"], default=None,
                    help="この実行の全記事を指定タイプにする（手動テスト・強制用）")
    args = ap.parse_args()
    count = max(1, args.count)

    if not config.ANTHROPIC_API_KEY:
        print("[ERROR] ANTHROPIC_API_KEY が未設定です。.env を確認してください。")
        return

    # リトライ実行: 直近の定時実行が成功済みなら二重生成しない
    if args.mode == "retry" and _recent_success(minutes=70):
        print("[retry] 直近70分以内に成功済みのためスキップします。")
        return

    print(f"=== 記事生成: データ収集開始（{count}本 / mode={args.mode}）===")
    collected = game_watch.collect_all()

    # セール・買い時トラッカー: 割引記録＋deals.html生成（失敗しても記事生成は止めない）
    try:
        deals_tracker.record_discounts(collected)
        deals_data = deals_tracker.build_deals_data(config.HISTORY_DB)
        deals_tracker.render_deals_page(deals_data, os.path.join(config.SITE_DIR, "deals.html"))
        print(f"=== セール・買い時トラッカー更新: {len(deals_data)}件 ===")
    except Exception as e:
        deals_data = []
        print(f"[WARN] セール・買い時トラッカーの更新に失敗: {e}")

    # トップページの「いま買い時」ストリップをdeals_dataで更新
    try:
        index_path = os.path.join(config.SITE_DIR, "index.html")
        with open(index_path, "r", encoding="utf-8") as f:
            index_html = f.read()
        new_index = deals_tracker.inject_deals_strip(index_html, deals_data)
        if new_index != index_html:
            with open(index_path, "w", encoding="utf-8") as f:
                f.write(new_index)
    except Exception as e:
        print(f"[WARN] いま買い時ストリップの更新に失敗: {e}")

    if not game_watch.has_signal(collected):
        print("収集データが無かったため、記事生成を中止しました。")
        return

    recent = storage.recent_article_topics(config.HISTORY_DB, days=21)
    print(f"直近公開済み: {len(recent)}件（重複回避）")

    # トレンド・ハイジャック: 狙うべきイベントを検出してプロンプトに渡す
    detection = trend_detector.detect(collected)
    hot_text = trend_detector.format_for_prompt(detection)
    print(f"=== 検出イベント: {sum(detection['counts'].values())}件"
          f"（速報級={'あり' if detection['has_breaking'] else 'なし'}） ===")
    for e in detection["hot_events"][:5]:
        print(f"  [{e['event_type']}] {e['headline'][:48]}  (score={e['score']})")

    produced = []  # [(category, title), ...]
    for i in range(count):
        avoid_note = ""
        if produced:
            done = "／".join(f"{c}「{t[:20]}」" for c, t in produced)
            avoid_note = (f"※重要: 今回の実行では既に次の記事を作成済み: {done}。"
                          "今回は必ず別カテゴリ・別トピックを選び、内容が重複しないようにすること。")
        # 特殊記事スロット判定:
        #   --special / --evergreen 指定時はこの実行の全記事を指定タイプにする（手動テスト・強制用）。
        #   自動ルール: 曜日・時間帯ごとの特殊スロット（_special_slot参照）は、2本目の記事だけ
        #   （1本目は通常ニュースのまま速報性を維持しつつ、2本目で検索資産を積む）。
        special = args.special or ("evergreen" if args.evergreen else None) or \
            (_special_slot() if i == 1 else None)
        extra_note = _build_special_note(special, collected, deals_data, recent)
        if special and not extra_note:
            special = None  # データ不足時は通常記事にフォールバック
        print(f"\n=== Claudeで記事を執筆中 ({i + 1}/{count}) ===")
        if special:
            print(f"[特殊記事モード: {special}] 該当スロット用の資産記事を生成します。")
        try:
            article = generate_article(collected, recent, config.ANTHROPIC_API_KEY,
                                       hot_events_text=hot_text, avoid_note=avoid_note,
                                       extra_note=extra_note)
        except Exception as e:
            print(f"[ERROR] 記事生成に失敗しました: {e}")
            break
        _publish_and_notify(article, collected, seq=i + 1, deals_data=deals_data)
        title = article.get("title", "")
        produced.append((article.get("category", ""), title))
        recent = recent + [{"title": title, "topic_key": article.get("topic_key", "")}]

    if produced:
        _mark_success()
    print(f"\n=== 完了: {len(produced)}本を生成しました ===")


if __name__ == "__main__":
    main()
