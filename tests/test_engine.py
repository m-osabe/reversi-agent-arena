from __future__ import annotations

import pytest

from reversi.engine import (
    BLACK,
    EMPTY,
    WHITE,
    MoveError,
    ReversiEngine,
    format_position,
    parse_position,
)


def test_coordinate_parsing_and_formatting() -> None:
    assert parse_position("A1") == (0, 0)
    assert parse_position("d3") == (2, 3)
    assert parse_position("  h8 ") == (7, 7)
    assert format_position(2, 3) == "D3"

    for bad in ("", "A0", "A9", "I1", "D33", "3D"):
        with pytest.raises(ValueError):
            parse_position(bad)


def test_initial_setup_and_legal_moves() -> None:
    eng = ReversiEngine()
    assert eng.status == "playing"
    assert eng.current_turn == BLACK
    assert eng.counts() == {BLACK: 2, WHITE: 2}
    assert eng.legal_moves() == ["C4", "D3", "E6", "F5"]

    ascii_board = eng.render_ascii()
    assert "A B C D E F G H" in ascii_board
    assert "*" in ascii_board
    assert "Turn=B" in ascii_board


def test_apply_move_and_case_insensitive_position() -> None:
    eng = ReversiEngine()
    rec = eng.apply_move(BLACK, "d3", comment="  初手D3  ")
    assert rec.position == "D3"
    assert rec.flipped == ["D4"]
    assert rec.comment == "初手D3"
    assert eng.counts() == {BLACK: 4, WHITE: 1}
    assert eng.current_turn == WHITE
    assert eng.legal_moves() == ["C3", "C5", "E3"]


def test_illegal_and_wrong_turn_moves_provide_legal_moves() -> None:
    eng = ReversiEngine()

    with pytest.raises(MoveError) as exc_info:
        eng.apply_move(WHITE, "D3")
    assert exc_info.value.code == "not_your_turn"
    assert exc_info.value.status_code == 409

    with pytest.raises(MoveError) as exc_info2:
        eng.apply_move(BLACK, "A1")
    assert exc_info2.value.code == "illegal_move"
    assert exc_info2.value.legal_moves == ["C4", "D3", "E6", "F5"]


def test_multi_direction_flip() -> None:
    eng = ReversiEngine()
    eng.board = [[EMPTY for _ in range(8)] for _ in range(8)]
    # Place Black stones to bracket White horizontally and vertically from C3 (row=2, col=2)
    eng.board[2][3] = WHITE  # D3
    eng.board[2][4] = BLACK  # E3
    eng.board[3][2] = WHITE  # C4
    eng.board[4][2] = BLACK  # C5
    eng.board[3][3] = WHITE  # D4
    eng.board[4][4] = BLACK  # E5
    eng.current_turn = BLACK

    rec = eng.apply_move(BLACK, "C3")
    assert set(rec.flipped) == {"D3", "C4", "D4"}
    assert eng.counts() == {BLACK: 7, WHITE: 0}
    # White has no stones left -> game over immediately
    assert eng.status == "finished"
    assert eng.winner == BLACK
    assert eng.end_reason == "normal"


def test_auto_pass_when_opponent_has_no_legal_moves() -> None:
    eng = ReversiEngine()
    eng.board = [[EMPTY for _ in range(8)] for _ in range(8)]
    # Row 1: B W . . . . . .  -> Black plays C1, flips B1 -> row 1 is B B B
    # Row 2: B W . . . . . .  -> Wait: if B plays C1, W has no legal moves, but B still has C2!
    eng.board[0][0] = BLACK  # A1
    eng.board[0][1] = WHITE  # B1
    eng.board[1][0] = BLACK  # A2
    eng.board[1][1] = WHITE  # B2
    eng.current_turn = BLACK

    eng.apply_move(BLACK, "C1")
    # After C1: A1=B, B1=B, C1=B; A2=B, B2=W.
    # White has NO legal moves, while Black can play C2 (or C3).
    assert eng.status == "playing"
    assert eng.current_turn == BLACK
    assert len(eng.history) == 2
    assert eng.history[1].passed is True
    assert eng.history[1].color == WHITE
    assert eng.history[1].comment is not None
    assert eng.history[1].comment.isascii()


def test_draw_game_over() -> None:
    eng = ReversiEngine()
    eng.board = [[EMPTY for _ in range(8)] for _ in range(8)]
    # Make a 2 vs 2 draw after final move with no further legal moves
    eng.board[0][0] = BLACK  # A1
    eng.board[0][1] = WHITE  # B1
    eng.board[7][7] = WHITE  # H8 (isolated)
    eng.board[7][6] = WHITE  # G8 (isolated)
    eng.board[7][5] = WHITE  # F8 (isolated)
    # Wait: after Black plays C1 -> A1=B, B1=B, C1=B (3 Black) vs 3 White (H8, G8, F8)
    eng.current_turn = BLACK

    eng.apply_move(BLACK, "C1")
    assert eng.status == "finished"
    assert eng.counts() == {BLACK: 3, WHITE: 3}
    assert eng.winner == "draw"


def test_resign_finishes_game() -> None:
    eng = ReversiEngine()
    rec = eng.resign(BLACK, comment="参りました")
    assert rec.resigned is True
    assert rec.comment == "参りました"
    assert eng.status == "finished"
    assert eng.current_turn is None
    assert eng.winner == WHITE
    assert eng.end_reason == "resign"

    with pytest.raises(MoveError) as exc_info:
        eng.apply_move(WHITE, "C3")
    assert exc_info.value.code == "game_finished"
