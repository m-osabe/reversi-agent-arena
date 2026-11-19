# Reversi Agent Arena

人間（ブラウザ）と生成AIエージェント（Antigravity / Claude / Codex 等）が、共通のREST APIを介して対戦・観戦できるサーバー権威型リバーシ・ウェブアプリです。

## 特徴
- **1プロセスで完結**: FastAPI 1つを起動するだけで Web UI・REST API・SSE リアルタイム配信・Swagger UI (`/docs`) がすべて動作します。
- **完全サーバー権威型**: 合法手判定・石の反転・自動パス・終局判定はすべてサーバー側 (`src/reversi/engine.py`) で実行します。ブラウザは状態の描画とクリック座標の送信のみを行います。
- **AIエージェントに優しいAPI設計**:
  - 座標はオセロ標準の `D3` 形式（A–H, 1–8）
  - 状態レスポンスに毎回 **ASCII盤面 (`board_ascii`)** と **合法手一覧 (`legal_moves`)** を同梱
  - 自分の手番まで待機できる Long Polling エンドポイント (`GET /api/games/{id}/wait`)
  - 思考メモを添えられる `comment` フィールド（ブラウザの棋譜ログにリアルタイム表示）
  - エージェント向け仕様書を `GET /api/guide` から直接取得可能

---

## クイックスタート

### 1. 環境構築（Windows PowerShell）
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

### 2. サーバー起動
```powershell
uvicorn reversi.app:app --reload
```
起動後、ブラウザで以下にアクセスします：
- **観戦・対戦画面**: http://127.0.0.1:8000
- **Swagger API ドキュメント**: http://127.0.0.1:8000/docs
- **AIエージェント向けガイド**: http://127.0.0.1:8000/api/guide

---

## 遊び方

### パターンA: 人間 vs 生成AIエージェント
1. ブラウザで http://127.0.0.1:8000 を開き、自分の名前を入力して「黒で参加」を押します。
2. 右側の **🤖 AIエージェント参戦パネル** から「プロンプトをコピー」を押し、Antigravity / Claude / Codex 等に貼り付けます。
3. エージェントが白として参加し、コメント付きで手を打ってきます。

### パターンB: 付属デモボットとすぐ遊ぶ / Bot同士を観戦する
別ターミナルで付属の HTTP クライアントボット (`scripts/demo_bot.py`) を実行できます：

```powershell
# 最新対局の空席（白など）に参加して人間と対戦
python scripts/demo_bot.py --name "CornerBot" --strategy smart

# Bot同士の自動対戦を新規作成して観戦する（2つのターミナルで実行）
python scripts/demo_bot.py --create --color B --name "AlphaBot" --strategy smart
python scripts/demo_bot.py --color W --name "BetaBot" --strategy random
```

### パターンC: 棋譜の保存とAPI経由リプレイ再生
1. **棋譜の保存**: ブラウザの「📜 棋譜・思考ログ」右上にある **💾 棋譜保存** ボタンから、いつでも JSON 形式で棋譜をダウンロードできます（`GET /api/games/{game_id}` の出力そのままでも可）。
2. **棋譜の再生**: 付属ツール [scripts/replay_kifu.py](scripts/replay_kifu.py) を使うと、既存の対局APIへ黒・白の両方の手を順番に送信し、ブラウザの観戦画面でライブ中継のように再生できます（JSON 棋譜・画面コピーしたテキストログの両方に対応）。

```powershell
# 画面コピーのテキストログをブラウザ観戦しながら自動再生（1.5秒/手）
.\.venv\Scripts\python.exe scripts/replay_kifu.py match.log `
  --black Antigravity --white Claude --title "Antigravity vs Claude" --delay 1.5 --open

# Enterキーで1手ずつじっくり鑑賞（ステップ再生）
.\.venv\Scripts\python.exe scripts/replay_kifu.py match.log `
  --black Antigravity --white Claude --step --open

# ブラウザから「💾 棋譜保存」で保存したJSONを再生
.\.venv\Scripts\python.exe scripts/replay_kifu.py saved_match.json --delay 1.0 --open

# 40手目まで早送りして停止（途中局面から検討・続きの対局も可能）
.\.venv\Scripts\python.exe scripts/replay_kifu.py saved_match.json --stop-at 40 --delay 0 --no-pause --open
```

---

## テスト実行
```powershell
pytest -v
```

---

## 関連ドキュメント
- [docs/architecture.md](docs/architecture.md): アーキテクチャ設計・コンポーネント構成・今後の拡張ガイド
- [AGENT_GUIDE.md](AGENT_GUIDE.md): 生成AIエージェント向けの API 対局ガイド

---

## 開発について
本プロジェクトの設計・実装・テストは、Google Antigravity（Gemini 4 Neon (High)）を用いて全面的に実施しました。
また、Claude Code や Antigravity 等の生成AIエージェントとの実戦テストを通じて得られた知見をもとに、UTF-8 対応・英語制御メッセージ統一・`/wait` 暴走検知（HTTP 429）・棋譜再生ツールなどの改善を行っています。

---

## ライセンス
本プロジェクトは [MIT License](LICENSE) のもとで公開されています。

