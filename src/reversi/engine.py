from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

Color = Literal["B", "W"]
Cell = Literal[".", "B", "W"]
GameStatus = Literal["playing", "finished"]
Winner = Literal["B", "W", "draw"]

EMPTY: Cell = "."
BLACK: Color = "B"
WHITE: Color = "W"
BOARD_SIZE = 8
COLS = "ABCDEFGH"
DIRECTIONS: tuple[tuple[int, int], ...] = (
    (-1, -1),
    (-1, 0),
    (-1, 1),
    (0, -1),
    (0, 1),
    (1, -1),
    (1, 0),
    (1, 1),
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def opponent(color: Color) -> Color:
    return WHITE if color == BLACK else BLACK


def parse_position(pos: str) -> tuple[int, int]:
    """Parse coordinate like 'D3' or 'd3' into (row, col) 0-indexed."""
    cleaned = pos.strip().upper()
    if len(cleaned) != 2:
        raise ValueError(f"Invalid coordinate '{pos}'. Expected format like 'D3' (A-H, 1-8).")
    col_ch, row_ch = cleaned[0], cleaned[1]
    if col_ch not in COLS or row_ch not in "12345678":
        raise ValueError(f"Coordinate '{pos}' is out of bounds (A-H, 1-8).")
    col = COLS.index(col_ch)
    row = int(row_ch) - 1
    return row, col


def format_position(row: int, col: int) -> str:
    """Format 0-indexed (row, col) into standard notation like 'D3'."""
    if not (0 <= row < BOARD_SIZE and 0 <= col < BOARD_SIZE):
        raise ValueError(f"Out of bounds: ({row}, {col})")
    return f"{COLS[col]}{row + 1}"


class MoveError(Exception):
    """Raised when an attempted move or action violates game rules."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "invalid_move",
        status_code: int = 400,
        legal_moves: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.legal_moves = legal_moves


@dataclass
class MoveRecord:
    move_number: int
    color: Color
    position: str | None = None
    flipped: list[str] = field(default_factory=list)
    passed: bool = False
    resigned: bool = False
    comment: str | None = None
    timestamp: str = field(default_factory=utc_now_iso)


class ReversiEngine:
    """Pure, authoritative Reversi (Othello) rules engine."""

    def __init__(self) -> None:
        self.board: list[list[Cell]] = [
            [EMPTY for _ in range(BOARD_SIZE)] for _ in range(BOARD_SIZE)
        ]
        # Standard Othello starting position
        self.board[3][3] = WHITE  # D4
        self.board[3][4] = BLACK  # E4
        self.board[4][3] = BLACK  # D5
        self.board[4][4] = WHITE  # E5

        self.current_turn: Color | None = BLACK
        self.status: GameStatus = "playing"
        self.winner: Winner | None = None
        self.end_reason: Literal["normal", "resign"] | None = None
        self.history: list[MoveRecord] = []

    def counts(self) -> dict[Color, int]:
        black = sum(cell == BLACK for row in self.board for cell in row)
        white = sum(cell == WHITE for row in self.board for cell in row)
        return {BLACK: black, WHITE: white}

    def get_flips(self, row: int, col: int, color: Color) -> list[tuple[int, int]]:
        """Return list of opponent stone coordinates that would flip if color plays (row, col)."""
        if not (0 <= row < BOARD_SIZE and 0 <= col < BOARD_SIZE):
            return []
        if self.board[row][col] != EMPTY:
            return []

        opp = opponent(color)
        flipped: list[tuple[int, int]] = []

        for dr, dc in DIRECTIONS:
            r, c = row + dr, col + dc
            line: list[tuple[int, int]] = []
            while 0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE and self.board[r][c] == opp:
                line.append((r, c))
                r += dr
                c += dc
            if line and 0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE and self.board[r][c] == color:
                flipped.extend(line)

        return flipped

    def legal_moves(self, color: Color | None = None) -> list[str]:
        """Return sorted legal coordinates (e.g., ['C4', 'D3', 'E6', 'F5'])."""
        if self.status != "playing":
            return []
        target_color = color or self.current_turn
        if target_color is None:
            return []

        moves: list[str] = []
        for r in range(BOARD_SIZE):
            for c in range(BOARD_SIZE):
                if self.get_flips(r, c, target_color):
                    moves.append(format_position(r, c))
        moves.sort()
        return moves

    def apply_move(
        self,
        color: Color,
        position: str,
        comment: str | None = None,
    ) -> MoveRecord:
        """Validate and apply a move, handling flips, auto-pass, and game-over."""
        if self.status != "playing":
            raise MoveError(
                "Game is already finished.",
                code="game_finished",
                status_code=409,
            )
        if color != self.current_turn:
            raise MoveError(
                f"It is not {color}'s turn (current turn: {self.current_turn}).",
                code="not_your_turn",
                status_code=409,
                legal_moves=self.legal_moves(),
            )

        try:
            row, col = parse_position(position)
        except ValueError as exc:
            raise MoveError(
                str(exc),
                code="invalid_coordinate",
                status_code=400,
                legal_moves=self.legal_moves(color),
            ) from exc

        flips = self.get_flips(row, col, color)
        if not flips:
            raise MoveError(
                f"Illegal move at '{format_position(row, col)}' for {color}.",
                code="illegal_move",
                status_code=400,
                legal_moves=self.legal_moves(color),
            )

        norm_pos = format_position(row, col)
        self.board[row][col] = color
        for fr, fc in flips:
            self.board[fr][fc] = color

        flipped_positions = sorted(format_position(fr, fc) for fr, fc in flips)
        cleaned_comment = comment.strip() if comment and comment.strip() else None

        record = MoveRecord(
            move_number=len(self.history) + 1,
            color=color,
            position=norm_pos,
            flipped=flipped_positions,
            passed=False,
            resigned=False,
            comment=cleaned_comment,
        )
        self.history.append(record)

        # Advance turn with automatic pass / game-over handling
        opp = opponent(color)
        if self.legal_moves(opp):
            self.current_turn = opp
        elif self.legal_moves(color):
            self.history.append(
                MoveRecord(
                    move_number=len(self.history) + 1,
                    color=opp,
                    position=None,
                    flipped=[],
                    passed=True,
                    resigned=False,
                    comment="Auto-pass (no legal moves)",
                )
            )
            self.current_turn = color
        else:
            self._finish_normal()

        return record

    def resign(self, color: Color, comment: str | None = None) -> MoveRecord:
        """Resign the game on behalf of color."""
        if self.status != "playing":
            raise MoveError(
                "Game is already finished.",
                code="game_finished",
                status_code=409,
            )

        cleaned_comment = comment.strip() if comment and comment.strip() else "Resigned"
        record = MoveRecord(
            move_number=len(self.history) + 1,
            color=color,
            position=None,
            flipped=[],
            passed=False,
            resigned=True,
            comment=cleaned_comment,
        )
        self.history.append(record)
        self.status = "finished"
        self.current_turn = None
        self.winner = opponent(color)
        self.end_reason = "resign"
        return record

    def _finish_normal(self) -> None:
        self.status = "finished"
        self.current_turn = None
        self.end_reason = "normal"
        cnt = self.counts()
        if cnt[BLACK] > cnt[WHITE]:
            self.winner = BLACK
        elif cnt[WHITE] > cnt[BLACK]:
            self.winner = WHITE
        else:
            self.winner = "draw"

    def render_ascii(self, show_legal: bool = True) -> str:
        """Render an LLM- and human-friendly ASCII board representation."""
        legal_set: set[tuple[int, int]] = set()
        if show_legal and self.status == "playing" and self.current_turn is not None:
            for pos in self.legal_moves(self.current_turn):
                legal_set.add(parse_position(pos))

        lines = ["  A B C D E F G H"]
        for r in range(BOARD_SIZE):
            row_cells: list[str] = []
            for c in range(BOARD_SIZE):
                cell = self.board[r][c]
                if cell == EMPTY and (r, c) in legal_set:
                    row_cells.append("*")
                else:
                    row_cells.append(cell)
            lines.append(f"{r + 1} {' '.join(row_cells)}")

        cnt = self.counts()
        if self.status == "playing":
            footer = f"# B={cnt[BLACK]} W={cnt[WHITE]} | Turn={self.current_turn} | *=Legal move"
        else:
            footer = f"# B={cnt[BLACK]} W={cnt[WHITE]} | Finished (Winner={self.winner})"
        lines.append(footer)
        return "\n".join(lines)
