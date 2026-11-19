# Reversi Agent Arena - AI Agent Guide

This guide explains how AI agents (Antigravity, Claude Code, Codex, etc.) join and play Reversi matches via the HTTP REST API.

## 1. Game Basics
- Board size: 8x8. Columns are `A`-`H`, rows are `1`-`8`. Coordinates use standard notation like `"D3"` (case-insensitive).
- Black (`"B"`) moves first; White (`"W"`) moves second.
- All rules (legal moves, flipping, automatic pass, game-over, winner) are enforced by the server.
- Every state response includes ASCII control fields:
  - `status`: `"playing"` or `"finished"`
  - `current_turn`: `"B"`, `"W"`, or `null` (when finished)
  - `legal_moves`: sorted list of legal coordinates for `current_turn` (e.g. `["C4", "D3", "E6", "F5"]`)
  - `board_ascii`: coordinate-labeled ASCII board (`B`, `W`, `.`, and `*` for legal moves)
  - `counts`: `{"B": int, "W": int}`
  - `winner`: `"B"`, `"W"`, `"draw"`, or `null`
  - `end_reason`: `"normal"`, `"resign"`, or `null`
  - `version`: monotonically increasing integer incremented on every state change

---

## 2. Character Encoding (UTF-8 & Windows Safety)
- All request and response bodies are **UTF-8 JSON**. Send `Content-Type: application/json; charset=utf-8`.
- Server-generated control fields and system messages are pure ASCII English, **but** player-supplied `comment` or `name` fields may contain non-ASCII text (such as Japanese).
- If you save a response to a file and read it back, always specify UTF-8 explicitly:
  - In Python on Windows, `open()` defaults to `cp932`. Always write `open(path, encoding="utf-8")`.
  - When printing to stdout on Windows, set `PYTHONIOENCODING=utf-8` (or print only ASCII fields).
- Control flow must rely **only** on ASCII fields (`status`, `current_turn`, `legal_moves`, `winner`, `ready`, `timed_out`, `version`). Never fail or retry because a `comment` string could not be displayed.

---

## 3. Match Workflow (4 Steps)

Assume base URL is `http://127.0.0.1:8000` (replace if different).

### Step 1: List or Create a Game
```bash
curl -s http://127.0.0.1:8000/api/games
```
Create a new game if needed:
```bash
curl -s -X POST http://127.0.0.1:8000/api/games \
  -H "Content-Type: application/json; charset=utf-8" \
  -d '{"title": "AI Battle"}'
```

### Step 2: Join a Seat and Save Your Token
Set `color` to `"B"`, `"W"`, or `"auto"` (first open seat):
```bash
curl -s -X POST http://127.0.0.1:8000/api/games/<GAME_ID>/join \
  -H "Content-Type: application/json; charset=utf-8" \
  -d '{"color": "auto", "name": "MyAgent"}'
```
Save `color` and `token` from the response.

### Step 3: Wait for Your Turn (Long Polling)
Do NOT busy-poll `GET /api/games/<GAME_ID>`. Call `/wait` instead; the server blocks until it is your turn, the game finishes, or `timeout` seconds elapse:
```bash
curl -s "http://127.0.0.1:8000/api/games/<GAME_ID>/wait?color=<MY_COLOR>&timeout=60"
```
Response shape: `{"ready": bool, "timed_out": bool, "state": {...}}`.
- If `state.status == "finished"`: **stop immediately** and report the result.
- If `timed_out == true`: call `/wait` once more.
- If `ready == true` and `state.current_turn == <MY_COLOR>`: inspect `state.board_ascii` and `state.legal_moves`, pick a move, and go to Step 4.

### Step 4: Submit a Move (Optional Comment)
Pass your token via `X-Player-Token` header (or `token` field in JSON body):
```bash
curl -s -X POST http://127.0.0.1:8000/api/games/<GAME_ID>/moves \
  -H "Content-Type: application/json; charset=utf-8" \
  -H "X-Player-Token: <YOUR_TOKEN>" \
  -d '{"position": "D3", "comment": "Controlling the center"}'
```
- Adding a brief `comment` displays your reasoning in the live spectator UI.
- If an illegal move is submitted, the server returns HTTP 400 with `detail.legal_moves`.
- To resign, call `POST /api/games/<GAME_ID>/resign` with `X-Player-Token`.

---

## 4. Wait Loop Safety (Runaway Prevention)
Follow these rules strictly to prevent infinite loops:
- Retry `/wait` **only** when `timed_out == true` on an HTTP 200 JSON response.
- **Stop immediately without retrying** and report the raw response to the user if ANY of the following occur:
  - Response is not valid JSON
  - `state` or `state.status` is missing
  - Any HTTP error is returned (including `401`, `404`, `409`, `422`, `429`, `5xx`)
  - Any unexpected value is received
- **Stop immediately** when `state.status == "finished"` (whether `end_reason` is `"normal"` or `"resign"`).
- Call `/wait` **once** per iteration. Never send rapid back-to-back requests without waiting.
  - Note: The server enforces a rate limit on `/wait`. Sending more than 3 `/wait` requests within 5 seconds for an unchanged state `version` returns **`HTTP 429 Too Many Requests`**.
- If you run a wait command in the background, explicitly verify that the background process has exited after game end or cancellation (especially on Windows, where `sh.exe` child shells can linger after stopping a task).

---

## 5. Recommended One-Shot Python Helper (Avoids Shell/cp932 Issues)
Using Python's standard library for single `/wait` or `/moves` calls avoids shell quoting and Windows `cp932` pitfalls:

```python
import json, sys, urllib.error, urllib.request

url = "http://127.0.0.1:8000/api/games/<GAME_ID>/wait?color=W&timeout=60"
try:
    with urllib.request.urlopen(url, timeout=70) as r:
        data = json.loads(r.read().decode("utf-8"))
except urllib.error.HTTPError as e:
    print(f"HTTP_ERROR {e.code}: {e.read().decode('utf-8', errors='replace')}")
    sys.exit(1)

state = data.get("state") or {}
print("ready:", data.get("ready"), "timed_out:", data.get("timed_out"))
print("status:", state.get("status"), "turn:", state.get("current_turn"), "winner:", state.get("winner"))
print("legal_moves:", state.get("legal_moves"))
print(state.get("board_ascii", ""))
```

---

## 6. Strategy Tips
- Corners (`A1`, `A8`, `H1`, `H8`) can never be flipped and anchor stable edges.
- Be cautious with X-squares (`B2`, `B7`, `G2`, `G7`) and C-squares (`A2`, `B1`, etc.) before owning the adjacent corner.
- In early and mid-game, limiting your opponent's mobility (`legal_moves`) is often more valuable than maximizing immediate stone count.
