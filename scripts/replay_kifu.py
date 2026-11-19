"""Replay a saved Reversi kifu (JSON or screen-copied text log) through the existing Game API.

Uses only the Python standard library. Creates (or attaches to) a game on the server,
joins both Black and White seats, and submits moves with comments one by one so you can
watch the match live in the browser spectator UI.

Examples:
    # Replay a screen-copied text log (e.g. Antigravity vs Claude)
    python scripts/replay_kifu.py c:\\temp\\antifravity_vs_clade.log \\
        --black Antigravity --white Claude --title "Antigravity vs Claude" --delay 1.5 --open

    # Step through move by move (press Enter for each move)
    python scripts/replay_kifu.py c:\\temp\\antifravity_vs_clade.log \\
        --black Antigravity --white Claude --step --open

    # Replay a JSON kifu downloaded from the browser ("💾 棋譜保存")
    python scripts/replay_kifu.py saved_match.json --delay 1.0 --open

    # Fast-forward to move 40 and stop so you can inspect or continue playing
    python scripts/replay_kifu.py saved_match.json --stop-at 40 --delay 0 --no-pause
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass
from pathlib import Path

MOVE_HEADER_RE = re.compile(r"^#(\d+)\s*$")
FLIP_LINE_RE = re.compile(r"^(?:反転\s*(\d+)\s*枚|flipped\s*:?\s*(\d+))$", re.IGNORECASE)
COORD_RE = re.compile(r"^[A-Ha-h][1-8]$")


@dataclass
class ReplayEntry:
    move_number: int
    position: str | None = None
    color: str | None = None
    passed: bool = False
    resigned: bool = False
    comment: str | None = None


@dataclass
class ParsedKifu:
    title: str
    black_name: str
    white_name: str
    entries: list[ReplayEntry]


def request_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
    token: str | None = None,
    timeout: float = 30.0,
) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["X-Player-Token"] = token
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} on {method} {url}: {body}") from exc


def parse_text_log(text: str, *, default_title: str = "Replay Match") -> ParsedKifu:
    """Parse screen-copied move log blocks (#1 / D3 / 反転 1枚 / comment)."""
    lines = [line.rstrip("\r") for line in text.splitlines()]
    blocks: list[list[str]] = []
    current: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if MOVE_HEADER_RE.match(stripped):
            if current:
                blocks.append(current)
            current = [stripped]
        elif current:
            current.append(stripped)
    if current:
        blocks.append(current)

    if not blocks:
        raise ValueError("No move blocks found (expected lines starting with '#1', '#2', ...).")

    entries: list[ReplayEntry] = []
    for block in blocks:
        m_header = MOVE_HEADER_RE.match(block[0])
        assert m_header is not None
        move_num = int(m_header.group(1))
        if len(block) < 2:
            raise ValueError(f"Incomplete entry at #{move_num}")

        action_line = block[1]
        rest = block[2:]
        if rest and FLIP_LINE_RE.match(rest[0]):
            rest = rest[1:]
        comment = "\n".join(rest).strip() or None

        lower = action_line.lower()
        if "パス" in action_line or "pass" in lower:
            entries.append(ReplayEntry(move_number=move_num, passed=True, comment=comment))
            continue

        if "投了" in action_line or "resign" in lower:
            color = None
            if "(B)" in action_line or "黒" in action_line:
                color = "B"
            elif "(W)" in action_line or "白" in action_line:
                color = "W"
            entries.append(
                ReplayEntry(move_number=move_num, color=color, resigned=True, comment=comment)
            )
            continue

        if not COORD_RE.match(action_line):
            raise ValueError(f"Invalid coordinate '{action_line}' at #{move_num}")

        entries.append(
            ReplayEntry(
                move_number=move_num,
                position=action_line.upper(),
                comment=comment,
            )
        )

    return ParsedKifu(
        title=default_title,
        black_name="Black",
        white_name="White",
        entries=entries,
    )


def parse_json_kifu(text: str, *, default_title: str = "Replay Match") -> ParsedKifu:
    """Parse JSON saved from the browser ('💾 棋譜保存') or GET /api/games/{id}."""
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("Kifu JSON root must be an object.")

    raw_moves = data.get("history")
    if raw_moves is None:
        raw_moves = data.get("moves")
    if not isinstance(raw_moves, list):
        raise ValueError("Kifu JSON must contain a 'history' or 'moves' list.")

    players = data.get("players") or {}

    def extract_name(side: str, fallback: str) -> str:
        val = players.get(side)
        if isinstance(val, dict):
            return str(val.get("name") or fallback)
        if isinstance(val, str) and val.strip():
            return val.strip()
        return fallback

    entries: list[ReplayEntry] = []
    for idx, item in enumerate(raw_moves, start=1):
        entries.append(
            ReplayEntry(
                move_number=int(item.get("move_number", idx)),
                position=item.get("position"),
                color=item.get("color"),
                passed=bool(item.get("passed", False)),
                resigned=bool(item.get("resigned", False)),
                comment=item.get("comment"),
            )
        )

    return ParsedKifu(
        title=str(data.get("title") or default_title),
        black_name=extract_name("B", "Black"),
        white_name=extract_name("W", "White"),
        entries=entries,
    )


def load_kifu_file(path: Path) -> ParsedKifu:
    """Auto-detect JSON or screen-copied text log and load kifu entries."""
    raw = path.read_text(encoding="utf-8")
    stripped = raw.lstrip()
    default_title = path.stem.replace("_", " ")
    if stripped.startswith("{"):
        return parse_json_kifu(raw, default_title=default_title)
    return parse_text_log(raw, default_title=default_title)


def replay_via_api(
    kifu: ParsedKifu,
    *,
    base_url: str = "http://127.0.0.1:8000",
    game_id: str | None = None,
    delay: float = 1.5,
    step_mode: bool = False,
    stop_at: int | None = None,
    no_pause: bool = False,
    open_browser: bool = False,
    save_json_path: Path | None = None,
) -> dict:
    base = base_url.rstrip("/")

    if game_id:
        state = request_json(f"{base}/api/games/{game_id}")
    else:
        state = request_json(
            f"{base}/api/games",
            method="POST",
            payload={"title": kifu.title},
        )
        game_id = state["game_id"]

    join_b = request_json(
        f"{base}/api/games/{game_id}/join",
        method="POST",
        payload={"color": "B", "name": kifu.black_name},
    )
    join_w = request_json(
        f"{base}/api/games/{game_id}/join",
        method="POST",
        payload={"color": "W", "name": kifu.white_name},
    )
    tokens = {"B": join_b["token"], "W": join_w["token"]}
    state = join_w["state"]

    spectate_url = f"{base}/?game={game_id}"
    print(f"[Replay] Game ready: {kifu.title}")
    print(f"[Replay] Black={kifu.black_name} vs White={kifu.white_name}")
    print(f"[Replay] Spectator URL: {spectate_url}")

    if open_browser:
        webbrowser.open(spectate_url)

    if not no_pause and not step_mode and sys.stdin.isatty():
        input("ブラウザで観戦画面を開いたら Enter キーを押して再生を開始してください...")

    played = 0
    for entry in kifu.entries:
        if stop_at is not None and played >= stop_at:
            break
        if entry.passed:
            # Server automatically records auto-pass on the preceding move
            continue
        if state["status"] != "playing":
            break

        if step_mode and sys.stdin.isatty():
            cmd = input(f"[Step] 次 (#{entry.move_number}: {entry.position or 'resign'}) Enter=進む / q=終了 > ")
            if cmd.strip().lower() == "q":
                break
        elif delay > 0 and played > 0:
            time.sleep(delay)

        turn = entry.color or state["current_turn"]
        if entry.resigned:
            state = request_json(
                f"{base}/api/games/{game_id}/resign",
                method="POST",
                token=tokens[turn],
                payload={"comment": entry.comment},
            )
            print(f"#{entry.move_number:02d} [{turn}] RESIGN | {entry.comment or ''}")
        else:
            state = request_json(
                f"{base}/api/games/{game_id}/moves",
                method="POST",
                token=tokens[turn],
                payload={"position": entry.position, "comment": entry.comment},
            )
            cnt = state["counts"]
            print(
                f"#{entry.move_number:02d} [{turn}] {entry.position} "
                f"(B={cnt['B']:02d} W={cnt['W']:02d}) | {entry.comment or ''}"
            )
        played += 1

    if save_json_path is not None:
        exported = {
            "game_id": state["game_id"],
            "title": state["title"],
            "created_at": state["created_at"],
            "status": state["status"],
            "winner": state["winner"],
            "end_reason": state["end_reason"],
            "players": {
                "B": state["players"]["B"]["name"] or kifu.black_name,
                "W": state["players"]["W"]["name"] or kifu.white_name,
            },
            "counts": state["counts"],
            "history": state["history"],
        }
        save_json_path.parent.mkdir(parents=True, exist_ok=True)
        save_json_path.write_text(
            json.dumps(exported, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[Replay] Saved standardized kifu JSON to: {save_json_path}")

    cnt = state["counts"]
    if state["status"] == "finished":
        print(
            f"[Replay] Finished! Winner={state['winner']} "
            f"(B={cnt['B']} - W={cnt['W']})"
        )
    else:
        print(
            f"[Replay] Stopped at move {played} (status={state['status']}, "
            f"turn={state['current_turn']}, B={cnt['B']} - W={cnt['W']})"
        )
        print(f"[Replay] Tokens to continue this game:")
        print(f"  B ({kifu.black_name}): {tokens['B']}")
        print(f"  W ({kifu.white_name}): {tokens['W']}")

    return state


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replay a Reversi kifu (JSON or screen text log) via the existing game API"
    )
    parser.add_argument("kifu_file", type=Path, help="Path to kifu JSON or screen-copied text log")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Server base URL")
    parser.add_argument("--game", dest="game_id", default=None, help="Existing empty game ID to replay into")
    parser.add_argument("--title", default=None, help="Override match title")
    parser.add_argument("--black", dest="black_name", default=None, help="Override Black player name")
    parser.add_argument("--white", dest="white_name", default=None, help="Override White player name")
    parser.add_argument("--delay", type=float, default=1.5, help="Seconds between moves (default: 1.5)")
    parser.add_argument("--step", action="store_true", help="Press Enter before each move")
    parser.add_argument("--stop-at", type=int, default=None, help="Stop after N moves (for mid-game study)")
    parser.add_argument("--no-pause", action="store_true", help="Start replaying immediately without waiting for Enter")
    parser.add_argument("--open", dest="open_browser", action="store_true", help="Open spectator URL in default browser")
    parser.add_argument("--save-json", type=Path, default=None, help="Also save the replayed match as standard kifu JSON")
    args = parser.parse_args()

    kifu = load_kifu_file(args.kifu_file)
    if args.title:
        kifu.title = args.title
    if args.black_name:
        kifu.black_name = args.black_name
    if args.white_name:
        kifu.white_name = args.white_name

    replay_via_api(
        kifu,
        base_url=args.base_url,
        game_id=args.game_id,
        delay=args.delay,
        step_mode=args.step,
        stop_at=args.stop_at,
        no_pause=args.no_pause,
        open_browser=args.open_browser,
        save_json_path=args.save_json,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
