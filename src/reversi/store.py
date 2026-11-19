from __future__ import annotations

import asyncio
import secrets
import uuid
from dataclasses import dataclass, field
from typing import Literal

from reversi.engine import BLACK, WHITE, Color, MoveError, ReversiEngine, utc_now_iso
from reversi.models import (
    GameStateResponse,
    GameSummaryResponse,
    JoinGameResponse,
    MoveRecordResponse,
    PlayerInfo,
    WaitResponse,
)


@dataclass
class PlayerSeat:
    name: str | None = None
    token: str | None = None

    @property
    def joined(self) -> bool:
        return self.token is not None

    def to_info(self) -> PlayerInfo:
        return PlayerInfo(name=self.name, joined=self.joined)


@dataclass
class GameSession:
    game_id: str
    title: str
    created_at: str = field(default_factory=utc_now_iso)
    engine: ReversiEngine = field(default_factory=ReversiEngine)
    seats: dict[Color, PlayerSeat] = field(
        default_factory=lambda: {BLACK: PlayerSeat(), WHITE: PlayerSeat()}
    )
    version: int = 1
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    subscribers: set[asyncio.Queue[GameStateResponse]] = field(default_factory=set)
    wait_calls: dict[str, tuple[int, list[float]]] = field(default_factory=dict)

    def check_wait_rate_limit(
        self,
        color_key: str,
        now: float,
        *,
        window_seconds: float = 5.0,
        max_same_version_calls: int = 3,
    ) -> None:
        """Detect tight runaway /wait loops on an unchanged game version."""
        prev = self.wait_calls.get(color_key)
        if prev is not None and prev[0] == self.version:
            recent = [t for t in prev[1] if now - t < window_seconds]
        else:
            recent = []

        if len(recent) >= max_same_version_calls:
            raise MoveError(
                (
                    f"Too many /wait requests for unchanged game state "
                    f"(version={self.version}, status={self.engine.status}). "
                    "Stop retrying and inspect the game status or submit a move."
                ),
                code="rate_limited",
                status_code=429,
            )

        recent.append(now)
        self.wait_calls[color_key] = (self.version, recent)

    def color_for_token(self, token: str | None) -> Color | None:
        if not token:
            return None
        for color, seat in self.seats.items():
            if seat.token and secrets.compare_digest(seat.token, token):
                return color
        return None

    def to_state(self) -> GameStateResponse:
        history = [
            MoveRecordResponse(
                move_number=m.move_number,
                color=m.color,
                position=m.position,
                flipped=list(m.flipped),
                passed=m.passed,
                resigned=m.resigned,
                comment=m.comment,
                timestamp=m.timestamp,
            )
            for m in self.engine.history
        ]
        return GameStateResponse(
            game_id=self.game_id,
            title=self.title,
            created_at=self.created_at,
            status=self.engine.status,
            current_turn=self.engine.current_turn,
            winner=self.engine.winner,
            end_reason=self.engine.end_reason,
            version=self.version,
            players={
                BLACK: self.seats[BLACK].to_info(),
                WHITE: self.seats[WHITE].to_info(),
            },
            counts=self.engine.counts(),
            legal_moves=self.engine.legal_moves(),
            last_move=history[-1] if history else None,
            board=[list(row) for row in self.engine.board],
            board_ascii=self.engine.render_ascii(show_legal=True),
            history=history,
        )

    def to_summary(self) -> GameSummaryResponse:
        return GameSummaryResponse(
            game_id=self.game_id,
            title=self.title,
            created_at=self.created_at,
            status=self.engine.status,
            current_turn=self.engine.current_turn,
            winner=self.engine.winner,
            version=self.version,
            move_count=len(self.engine.history),
            players={
                BLACK: self.seats[BLACK].to_info(),
                WHITE: self.seats[WHITE].to_info(),
            },
            counts=self.engine.counts(),
        )

    def notify_subscribers(self, state: GameStateResponse) -> None:
        for queue in list(self.subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(state)
            except asyncio.QueueFull:
                pass


class GameStore:
    """In-memory game session repository with asyncio turn-wait and SSE pub/sub."""

    def __init__(self) -> None:
        self._games: dict[str, GameSession] = {}

    def _get_session(self, game_id: str) -> GameSession:
        session = self._games.get(game_id)
        if session is None:
            raise MoveError(
                f"Game '{game_id}' not found.",
                code="game_not_found",
                status_code=404,
            )
        return session

    def create_game(self, title: str = "Reversi Match") -> GameStateResponse:
        game_id = uuid.uuid4().hex[:8]
        cleaned_title = title.strip() or "Reversi Match"
        session = GameSession(game_id=game_id, title=cleaned_title)
        self._games[game_id] = session
        return session.to_state()

    def list_games(self) -> list[GameSummaryResponse]:
        # Most recently created first
        return [s.to_summary() for s in reversed(self._games.values())]

    def get_game(self, game_id: str) -> GameStateResponse:
        return self._get_session(game_id).to_state()

    async def join_game(
        self,
        game_id: str,
        color: Literal["B", "W", "auto"],
        name: str,
    ) -> JoinGameResponse:
        session = self._get_session(game_id)
        cleaned_name = name.strip() or "Player"

        async with session.condition:
            chosen: Color | None = None
            if color == "auto":
                if not session.seats[BLACK].joined:
                    chosen = BLACK
                elif not session.seats[WHITE].joined:
                    chosen = WHITE
                else:
                    raise MoveError(
                        "Both Black and White seats are already taken.",
                        code="seats_full",
                        status_code=409,
                    )
            else:
                if session.seats[color].joined:
                    raise MoveError(
                        f"Seat '{color}' is already taken by '{session.seats[color].name}'.",
                        code="seat_taken",
                        status_code=409,
                    )
                chosen = color

            token = secrets.token_urlsafe(18)
            session.seats[chosen] = PlayerSeat(name=cleaned_name, token=token)
            session.version += 1
            state = session.to_state()
            session.notify_subscribers(state)
            session.condition.notify_all()

        return JoinGameResponse(
            game_id=session.game_id,
            color=chosen,
            name=cleaned_name,
            token=token,
            state=state,
        )

    async def make_move(
        self,
        game_id: str,
        token: str | None,
        position: str,
        comment: str | None = None,
    ) -> GameStateResponse:
        session = self._get_session(game_id)

        async with session.condition:
            color = session.color_for_token(token)
            if color is None:
                raise MoveError(
                    "Invalid or missing player token. Join the game first via POST /api/games/{game_id}/join.",
                    code="invalid_token",
                    status_code=401,
                )

            session.engine.apply_move(color=color, position=position, comment=comment)
            session.version += 1
            state = session.to_state()
            session.notify_subscribers(state)
            session.condition.notify_all()
            return state

    async def resign(
        self,
        game_id: str,
        token: str | None,
        comment: str | None = None,
    ) -> GameStateResponse:
        session = self._get_session(game_id)

        async with session.condition:
            color = session.color_for_token(token)
            if color is None:
                raise MoveError(
                    "Invalid or missing player token.",
                    code="invalid_token",
                    status_code=401,
                )

            session.engine.resign(color=color, comment=comment)
            session.version += 1
            state = session.to_state()
            session.notify_subscribers(state)
            session.condition.notify_all()
            return state

    async def wait_for_state(
        self,
        game_id: str,
        *,
        color: Color | None = None,
        timeout: float = 30.0,
        since_version: int | None = None,
    ) -> WaitResponse:
        session = self._get_session(game_id)
        clamped_timeout = max(0.1, min(timeout, 120.0))
        loop = asyncio.get_running_loop()
        now = loop.time()
        deadline = now + clamped_timeout
        color_key = color if color is not None else "__spectator__"

        async with session.condition:
            session.check_wait_rate_limit(color_key, now)
            target_version = session.version if since_version is None else since_version

            def is_ready() -> bool:
                if session.engine.status == "finished":
                    return True
                if color is not None:
                    if since_version is not None and session.version <= since_version:
                        return False
                    return session.engine.current_turn == color
                return session.version > target_version

            while not is_ready():
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    return WaitResponse(ready=False, timed_out=True, state=session.to_state())
                try:
                    await asyncio.wait_for(session.condition.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    return WaitResponse(ready=False, timed_out=True, state=session.to_state())

            return WaitResponse(ready=True, timed_out=False, state=session.to_state())

    def register_subscriber(
        self, game_id: str
    ) -> tuple[GameSession, asyncio.Queue[GameStateResponse]]:
        session = self._get_session(game_id)
        queue: asyncio.Queue[GameStateResponse] = asyncio.Queue(maxsize=32)
        session.subscribers.add(queue)
        return session, queue

    def unregister_subscriber(
        self, session: GameSession, queue: asyncio.Queue[GameStateResponse]
    ) -> None:
        session.subscribers.discard(queue)
