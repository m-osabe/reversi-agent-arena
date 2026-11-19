from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger("uvicorn.error")

from reversi.engine import Color, MoveError
from reversi.models import (
    CreateGameRequest,
    GameStateResponse,
    GameSummaryResponse,
    JoinGameRequest,
    JoinGameResponse,
    MoveRequest,
    ResignRequest,
    WaitResponse,
)
from reversi.store import GameStore

STATIC_DIR = Path(__file__).resolve().parent / "static"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
AGENT_GUIDE_PATH = PROJECT_ROOT / "AGENT_GUIDE.md"


def _raise_http_error(exc: MoveError) -> None:
    detail: dict[str, object] = {
        "code": exc.code,
        "message": exc.message,
    }
    if exc.legal_moves is not None:
        detail["legal_moves"] = exc.legal_moves
    headers = {"Retry-After": "2"} if exc.status_code == 429 else None
    raise HTTPException(status_code=exc.status_code, detail=detail, headers=headers)


def create_app(store: GameStore | None = None) -> FastAPI:
    app = FastAPI(
        title="Reversi Agent Arena",
        version="0.1.0",
        description=(
            "Server-authoritative Reversi web app & REST API built for humans "
            "and generative AI agents (Antigravity, Claude, Codex, etc.)."
        ),
    )
    game_store = store or GameStore()
    app.state.store = game_store

    @app.middleware("http")
    async def response_headers_middleware(request: Request, call_next):
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        content_type = response.headers.get("content-type", "")
        if content_type.lower() == "application/json":
            response.headers["Content-Type"] = "application/json; charset=utf-8"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        logger.warning(
            "422 ValidationError on %s %s (Content-Type=%s): %s",
            request.method,
            request.url.path,
            request.headers.get("content-type"),
            exc.errors(),
        )
        return JSONResponse(status_code=422, content={"detail": exc.errors()})

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/guide", response_class=PlainTextResponse)
    async def get_agent_guide() -> str:
        if AGENT_GUIDE_PATH.exists():
            return AGENT_GUIDE_PATH.read_text(encoding="utf-8")
        return "AGENT_GUIDE.md not found."

    @app.post("/api/games", response_model=GameStateResponse, status_code=201)
    async def create_game(req: CreateGameRequest | None = None) -> GameStateResponse:
        title = req.title if req else "Reversi Match"
        return game_store.create_game(title=title)

    @app.get("/api/games", response_model=list[GameSummaryResponse])
    async def list_games() -> list[GameSummaryResponse]:
        return game_store.list_games()

    @app.get("/api/games/{game_id}", response_model=GameStateResponse)
    async def get_game(game_id: str) -> GameStateResponse:
        try:
            return game_store.get_game(game_id)
        except MoveError as exc:
            _raise_http_error(exc)

    @app.post("/api/games/{game_id}/join", response_model=JoinGameResponse)
    async def join_game(game_id: str, req: JoinGameRequest) -> JoinGameResponse:
        try:
            return await game_store.join_game(
                game_id=game_id,
                color=req.color,
                name=req.name,
            )
        except MoveError as exc:
            _raise_http_error(exc)

    @app.post("/api/games/{game_id}/moves", response_model=GameStateResponse)
    async def make_move(
        game_id: str,
        req: MoveRequest,
        x_player_token: str | None = Header(default=None, alias="X-Player-Token"),
    ) -> GameStateResponse:
        token = x_player_token or req.token
        try:
            return await game_store.make_move(
                game_id=game_id,
                token=token,
                position=req.position,
                comment=req.comment,
            )
        except MoveError as exc:
            _raise_http_error(exc)

    @app.post("/api/games/{game_id}/resign", response_model=GameStateResponse)
    async def resign_game(
        game_id: str,
        req: ResignRequest | None = None,
        x_player_token: str | None = Header(default=None, alias="X-Player-Token"),
    ) -> GameStateResponse:
        token = x_player_token or (req.token if req else None)
        comment = req.comment if req else None
        try:
            return await game_store.resign(
                game_id=game_id,
                token=token,
                comment=comment,
            )
        except MoveError as exc:
            _raise_http_error(exc)

    @app.get("/api/games/{game_id}/wait", response_model=WaitResponse)
    async def wait_turn(
        game_id: str,
        color: Color | None = Query(
            default=None,
            description="Wait until it is this color's turn ('B' or 'W') or the game finishes.",
        ),
        timeout: float = Query(default=30.0, ge=0.1, le=120.0),
        since_version: int | None = Query(default=None, ge=0),
    ) -> WaitResponse:
        try:
            return await game_store.wait_for_state(
                game_id,
                color=color,
                timeout=timeout,
                since_version=since_version,
            )
        except MoveError as exc:
            _raise_http_error(exc)

    @app.get("/api/games/{game_id}/events")
    async def stream_events(game_id: str, request: Request) -> StreamingResponse:
        try:
            session, queue = game_store.register_subscriber(game_id)
            initial_state = session.to_state()
        except MoveError as exc:
            _raise_http_error(exc)

        async def event_generator() -> AsyncIterator[str]:
            try:
                yield f"event: state\ndata: {initial_state.model_dump_json()}\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        state = await asyncio.wait_for(queue.get(), timeout=15.0)
                        yield f"event: state\ndata: {state.model_dump_json()}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                game_store.unregister_subscriber(session, queue)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

        @app.get("/", include_in_schema=False)
        async def serve_index() -> FileResponse:
            return FileResponse(STATIC_DIR / "index.html")

    return app


app = create_app()
