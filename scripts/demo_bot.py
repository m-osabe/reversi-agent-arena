"""Standalone HTTP-only Reversi demo bot for testing Human-vs-Bot or Bot-vs-Bot matches.

Uses only the Python standard library so it works with zero extra packages.

Usage:
    python scripts/demo_bot.py --game <GAME_ID> --color W --name "CornerBot" --strategy smart
    python scripts/demo_bot.py --create --color B --name "Bot-Black"
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# Classic positional evaluation weights for 8x8 Reversi
WEIGHTS: list[list[int]] = [
    [120, -20, 20, 5, 5, 20, -20, 120],
    [-20, -40, -5, -5, -5, -5, -40, -20],
    [20, -5, 15, 3, 3, 15, -5, 20],
    [5, -5, 3, 3, 3, 3, -5, 5],
    [5, -5, 3, 3, 3, 3, -5, 5],
    [20, -5, 15, 3, 3, 15, -5, 20],
    [-20, -40, -5, -5, -5, -5, -40, -20],
    [120, -20, 20, 5, 5, 20, -20, 120],
]
COLS = "ABCDEFGH"


def request_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
    token: str | None = None,
    timeout: float = 70.0,
) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Player-Token"] = token
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} on {method} {url}: {body}") from exc


def choose_move(legal_moves: list[str], strategy: str) -> tuple[str, str]:
    if strategy == "random":
        pos = random.choice(legal_moves)
        return pos, f"ランダムに {pos} を選びました"

    def score_pos(pos: str) -> int:
        col = COLS.index(pos[0])
        row = int(pos[1]) - 1
        return WEIGHTS[row][col]

    best_score = max(score_pos(m) for m in legal_moves)
    best_moves = [m for m in legal_moves if score_pos(m) == best_score]
    pos = random.choice(best_moves)

    if best_score >= 100:
        comment = f"角 {pos} を確保します！"
    elif best_score >= 15:
        comment = f"好位置 {pos} で主導権を狙います"
    elif best_score <= -20:
        comment = f"苦しいですが {pos} に打ちます"
    else:
        comment = f"堅実に {pos} へ着手します"
    return pos, comment


def main() -> int:
    parser = argparse.ArgumentParser(description="HTTP-only Reversi demo bot")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Server base URL")
    parser.add_argument("--game", dest="game_id", default=None, help="Existing game ID to join")
    parser.add_argument("--create", action="store_true", help="Create a new game before joining")
    parser.add_argument("--title", default="Demo Bot Match", help="Title when using --create")
    parser.add_argument("--color", choices=["B", "W", "auto"], default="auto")
    parser.add_argument("--name", default="DemoBot")
    parser.add_argument("--strategy", choices=["smart", "random"], default="smart")
    parser.add_argument("--delay", type=float, default=0.5, help="Delay seconds before each move")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")

    game_id = args.game_id
    if args.create:
        created = request_json(f"{base}/api/games", method="POST", payload={"title": args.title})
        game_id = created["game_id"]
        print(f"[Bot] Created game: {game_id} ({base}/?game={game_id})")
    elif not game_id:
        games = request_json(f"{base}/api/games")
        if not games:
            print("No existing games found. Pass --create or --game <GAME_ID>.", file=sys.stderr)
            return 1
        game_id = games[0]["game_id"]
        print(f"[Bot] Selected latest game: {game_id}")

    joined = request_json(
        f"{base}/api/games/{game_id}/join",
        method="POST",
        payload={"color": args.color, "name": args.name},
    )
    my_color = joined["color"]
    token = joined["token"]
    print(f"[Bot] Joined game {game_id} as {my_color} ({args.name})")

    while True:
        query = urllib.parse.urlencode({"color": my_color, "timeout": 30})
        wait_res = request_json(f"{base}/api/games/{game_id}/wait?{query}")
        state = wait_res["state"]

        if state["status"] == "finished":
            print(
                f"[Bot] Game finished! Winner={state['winner']} "
                f"(B={state['counts']['B']} - W={state['counts']['W']})"
            )
            return 0

        if wait_res["timed_out"] or state["current_turn"] != my_color:
            continue

        legal_moves = state["legal_moves"]
        if not legal_moves:
            continue

        if args.delay > 0:
            time.sleep(args.delay)

        pos, comment = choose_move(legal_moves, args.strategy)
        after = request_json(
            f"{base}/api/games/{game_id}/moves",
            method="POST",
            token=token,
            payload={"position": pos, "comment": comment},
        )
        print(
            f"[Bot:{my_color}] Played {pos} -> "
            f"B={after['counts']['B']} W={after['counts']['W']} (turn={after['current_turn']})"
        )


if __name__ == "__main__":
    raise SystemExit(main())
