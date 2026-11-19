# Reversi Agent Arena — Architecture & Design

このドキュメントは、Reversi Agent Arena の設計意図・全体構成・今後の拡張ポイントをまとめたリファレンスです。

---

## 1. 設計思想

- **完全サーバー権威型（Server-Authoritative）**:
  - 盤面状態、合法手算出、8方向反転、自動パス、終局・勝敗判定はすべてサーバー側（`src/reversi/engine.py`）に集約しています。
  - ブラウザ（人間）と生成AIエージェント（Antigravity / Claude / Codex 等）はまったく同じ REST API を通じて対局するため、ルール不整合が起きません。
- **1プロセス完結・最小依存**:
  - Python + FastAPI 単体で REST API、SSE リアルタイム配信、Swagger UI (`/docs`)、および静的 Web UI をまとめて配信します。
  - フロントエンドのビルド環境を不要とし、起動・デバッグ・改造のハードルを最小化しています。
- **LLM フレンドリーな API 設計**:
  - 座標はオセロ標準表記（`A`–`H`, `1`–`8` 例: `"D3"`）を採用。
  - 状態レスポンスに毎回 `board_ascii`（`*` で合法手を可視化した ASCII 盤面）と `legal_moves` を同梱し、反則エラー時も `detail.legal_moves` を返すことで AI の自己修正を容易にしています。
  - 相手の手番待ちには Long Polling (`GET /api/games/{id}/wait`) を提供し、エージェントのポーリング連打を防ぎます。
  - 着手時の任意コメント（`comment`）を棋譜ログに記録し、観戦体験を高めています。

---

## 2. ディレクトリ構成

```text
reversi-agent-arena/
├── LICENSE                     # MIT License
├── pyproject.toml              # 依存関係・パッケージ設定
├── README.md                   # セットアップ・起動方法・遊び方
├── AGENT_GUIDE.md              # 生成AIエージェント向け参加ガイド（GET /api/guide で配信）
├── docs/
│   └── architecture.md         # 本ドキュメント（設計・拡張ガイド）
├── src/
│   └── reversi/
│       ├── __init__.py
│       ├── engine.py           # 純粋なリバーシ・ルールエンジン（HTTP/非同期に非依存）
│       ├── models.py           # Pydantic リクエスト／レスポンススキーマ
│       ├── store.py            # インメモリ対局ストア（座席・トークン・asyncio通知）
│       ├── app.py              # FastAPI アプリ本体（REST API / SSE / 静的ファイル配信）
│       └── static/
│           ├── index.html      # 観戦・対戦画面（棋譜JSONダウンロード付き）
│           ├── styles.css      # UI・盤面スタイル
│           └── app.js          # 薄型クライアント（SSE購読・描画・API送信のみ）
├── scripts/
│   ├── demo_bot.py             # 標準ライブラリのみで動く HTTP 検証用ボット
│   └── replay_kifu.py          # 既存対局APIへ棋譜（JSON/テキストログ）を再生するCLIツール
└── tests/
    ├── test_engine.py          # ルールエンジンの単体テスト
    └── test_api.py             # API / 認証 / Long Polling / SSE の統合テスト
```

---

## 3. コンポーネントと責務の分離

### `engine.py` — ルールエンジン
- HTTP・FastAPI・asyncio に一切依存しない純粋な Python クラス `ReversiEngine`。
- 主な責務:
  - 座標変換 (`parse_position`, `format_position`)：大文字・小文字を正規化
  - 合法手算出 (`legal_moves`)・8方向挟み込み判定 (`get_flips`)
  - 着手適用 (`apply_move`)：反転 → 棋譜追記 → 相手合法手チェック → 自動パス or 終局判定
  - 投了処理 (`resign`)
  - ASCII 盤面生成 (`render_ascii`)

### `store.py` — 対局セッション・同期層
- 現在はインメモリ辞書 (`GameStore._games`) で複数対局を管理。
- 各対局 (`GameSession`) が以下を保持:
  - `seats`: 黒 (`B`) / 白 (`W`) ごとのプレイヤー名とランダムトークン (`secrets.token_urlsafe`)
  - `version`: 参加・着手・投了のたびに単調増加する状態バージョン
  - `condition` (`asyncio.Condition`): 状態変更の排他制御および `/wait` 待機クライアントの即時起床
  - `subscribers` (`set[asyncio.Queue]`): SSE 接続中クライアントへのファンアウト配信（満杯時は古い状態を破棄し最新を優先）
- **デッドロックレスな開始設計**: 黒が着席した時点ですぐ打ち始められ、後から白（人間またはAI）が合流しても自然に進行します。

### `app.py` — Web / API 層
- `create_app(store)` ファクトリ構成により、テストごとに独立した `GameStore` を注入可能。
- 開発・更新時のキャッシュ不整合を防ぐため、`/` と `/static/*` に `Cache-Control: no-store` を付与し、JSON レスポンスには `Content-Type: application/json; charset=utf-8` を明示するミドルウェアを搭載。
- エージェントが読み取る制御用メッセージ（自動パス・投了既定文言・エラー文・`AGENT_GUIDE.md`）はすべて純 ASCII 英語に統一し、Windows の `cp932` 起因の文字化け・パース失敗を防止（ユーザー任意入力の `comment` は UTF-8 で日本語も利用可能）。
- `/wait` エンドポイントは、同一 `(color, version)` に対する短時間連打（5秒以内に4回目以降）を検知して **`HTTP 429 Too Many Requests`**（`Retry-After: 2`）を返し、終局後やパース失敗時のクライアント暴走をサーバー側でも強制停止できる設計。
- 422 バリデーションエラー発生時は `Content-Type` と詳細をサーバーログに出力する例外ハンドラを実装。

### `static/app.js` — 薄型ブラウザクライアント
- ルール判定ロジックを一切持たず、サーバーから SSE (`event: state`) で届く `GameStateResponse` をそのまま描画。
- 自分の手番（`mySeat.color === state.current_turn && state.status === "playing"`）のときだけ `state.legal_moves` のマスをクリック可能にする。
- ブラウザの `sessionStorage` に対局ごとの着席トークンを保存し、リロード後も対局を継続可能。

---

## 4. API エンドポイント一覧

| メソッド | パス | 役割 |
| :--- | :--- | :--- |
| `GET` | `/api/health` | ヘルスチェック |
| `GET` | `/api/guide` | `AGENT_GUIDE.md` をプレーンテキスト返却 |
| `POST` | `/api/games` | 新規対局の作成 |
| `GET` | `/api/games` | 対局一覧（新しい順） |
| `GET` | `/api/games/{game_id}` | 現在の対局状態（盤面・ASCII・合法手・棋譜等） |
| `POST` | `/api/games/{game_id}/join` | 黒 (`B`) / 白 (`W`) / 自動 (`auto`) で着席しトークン取得 |
| `POST` | `/api/games/{game_id}/moves` | 石を置く（`X-Player-Token` ヘッダまたはボディの `token`） |
| `POST` | `/api/games/{game_id}/resign` | 投了する（要トークン） |
| `GET` | `/api/games/{game_id}/wait` | 指定色の手番または終局まで待機する Long Polling |
| `GET` | `/api/games/{game_id}/events` | 盤面更新をリアルタイム配信する SSE ストリーム |

---

## 5. 今後の拡張ガイド

層が分離されているため、以下の機能追加は既存コードへの影響を最小限にして実装できます。

1. **SQLite による永続化（棋譜保存・再起動耐性）**
   - `store.py` の `GameStore` のインターフェースを維持したまま、対局メタデータと `history`（各手の座標・コメント）を SQLite に保存。
   - 再起動時は `ReversiEngine` に履歴をリプレイして盤面を復元するか、スナップショット JSON を保存する構成がシンプルです。
2. **MCP (Model Context Protocol) サーバー対応**
   - 既存の REST API をそのまま呼び出す薄い MCP ラッパー（`list_games`, `join_game`, `wait_turn`, `play_move`, `resign_game`）を追加するだけで、Claude / Antigravity 等からネイティブツールとして対局可能になります。
3. **フロントエンドの TypeScript SPA 化（React / Vite 等）**
   - バックエンド API と SSE 仕様は完全に独立しているため、別フロントエンドからそのまま接続可能です（必要に応じて FastAPI に `CORSMiddleware` を追加し、本番ビルド成果物を `StaticFiles` で配信）。
4. **観戦・対戦機能の強化**
   - `history` に全着手が記録されているため、棋譜の巻き戻し再生（リプレイ）や評価値グラフ、持ち時間（フィッシャー／秒読み）タイマーなどの追加も容易です。
