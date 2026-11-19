from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from reversi.engine import Color, GameStatus, Winner


class CreateGameRequest(BaseModel):
    title: str = Field(default="Reversi Match", max_length=80)


class JoinGameRequest(BaseModel):
    color: Literal["B", "W", "auto"] = Field(
        default="auto",
        description="Seat color to take: 'B' (Black), 'W' (White), or 'auto' for first open seat.",
    )
    name: str = Field(default="Player", min_length=1, max_length=40)


class MoveRequest(BaseModel):
    position: str = Field(..., description="Board coordinate such as 'D3' (A-H, 1-8).")
    comment: str | None = Field(
        default=None,
        max_length=280,
        description="Optional short thinking note or commentary displayed in the move log.",
    )
    token: str | None = Field(
        default=None,
        description="Optional player token fallback if X-Player-Token header is not set.",
    )


class ResignRequest(BaseModel):
    comment: str | None = Field(default=None, max_length=280)
    token: str | None = Field(default=None)


class PlayerInfo(BaseModel):
    name: str | None = None
    joined: bool = False


class MoveRecordResponse(BaseModel):
    move_number: int
    color: Color
    position: str | None = None
    flipped: list[str] = Field(default_factory=list)
    passed: bool = False
    resigned: bool = False
    comment: str | None = None
    timestamp: str


class GameStateResponse(BaseModel):
    game_id: str
    title: str
    created_at: str
    status: GameStatus
    current_turn: Color | None
    winner: Winner | None
    end_reason: Literal["normal", "resign"] | None
    version: int
    players: dict[Color, PlayerInfo]
    counts: dict[Color, int]
    legal_moves: list[str]
    last_move: MoveRecordResponse | None
    board: list[list[str]]
    board_ascii: str
    history: list[MoveRecordResponse]


class GameSummaryResponse(BaseModel):
    game_id: str
    title: str
    created_at: str
    status: GameStatus
    current_turn: Color | None
    winner: Winner | None
    version: int
    move_count: int
    players: dict[Color, PlayerInfo]
    counts: dict[Color, int]


class JoinGameResponse(BaseModel):
    game_id: str
    color: Color
    name: str
    token: str
    state: GameStateResponse


class WaitResponse(BaseModel):
    ready: bool
    timed_out: bool
    state: GameStateResponse

