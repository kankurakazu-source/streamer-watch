"""
run_watch.py
------------
「ストリーマーウォッチ」合言葉で呼ばれる実行スクリプト。
main.py のパイプライン（収集→保存→急増検知→AI下書き生成→ファイル出力）を実行し、
生成された draft_post だけをチャットに表示しやすい形で標準出力に書き出す。

投稿は一切行わない（下書き生成まで）。出力を人間がレビューして手動投稿する運用は不変。

使い方:
    .venv\\Scripts\\python.exe run_watch.py
"""

import glob
import json
import os
import sys

# Windowsコンソール(cp932)でも日本語が文字化けしないよう標準出力をUTF-8化
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import config
import main as m


def main():
    # 収集～下書き生成～ファイル出力までを実行（ログは main 側が出す）
    m.main()

    # 直近の出力ファイルを読み、draft_post を抜き出して表示する
    files = sorted(glob.glob(os.path.join(config.OUTPUT_DIR, "draft_*.json")))
    if not files:
        print("\n===DRAFTS_START===")
        print("(出力ファイルが見つかりませんでした)")
        print("===DRAFTS_END===")
        return

    latest = files[-1]
    with open(latest, encoding="utf-8") as f:
        data = json.load(f)

    drafts = data.get("drafts", [])
    print("\n===DRAFTS_START===")
    if not drafts:
        print("(今回は下書きなし：注目対象が無かったか、AI生成がスキップされました)")
    else:
        for i, d in enumerate(drafts, 1):
            if "draft_post" in d:
                print(f"[{i}] {d.get('streamer', '')}")
                print(d["draft_post"])
                print()
            else:
                # 例外時（JSONパース失敗など）は生データをそのまま出す
                print(f"[{i}] {json.dumps(d, ensure_ascii=False)}")
                print()
    print("===DRAFTS_END===")
    print(f"file: {latest}")


if __name__ == "__main__":
    main()
