from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from reversi.app import create_app
from reversi.store import GameStore


def make_client() -> TestClient:
    return TestClient(create_app(GameStore()))


def test_health_guide_and_index() -> None:
    client = make_client()
    health = client.get("/api/health")
    assert health.json() == {"status": "ok"}
    assert health.headers["content-type"] == "application/json; charset=utf-8"

    guide = client.get("/api/guide")
    assert guide.status_code == 200
    assert "Reversi Agent Arena" in guide.text
    assert guide.text.isascii()
    assert "HTTP 429" in guide.text
    assert "UTF-8" in guide.text

    index = client.get("/")
    assert index.status_code == 200
    assert "Reversi Agent Arena" in index.text


def test_create_list_join_and_auth_flow() -> None:
    client = make_client()

    # Create game
    created = client.post("/api/games", json={"title": "Test Match"})
    assert created.status_code == 201
    game_id = created.json()["game_id"]
    assert created.json()["title"] == "Test Match"

    # List games
    games = client.get("/api/games").json()
    assert len(games) == 1
    assert games[0]["game_id"] == game_id

    # Join Black
    join_b = client.post(
        f"/api/games/{game_id}/join",
        json={"color": "B", "name": "Alice"},
    )
    assert join_b.status_code == 200
    token_b = join_b.json()["token"]
    assert join_b.json()["color"] == "B"

    # Duplicate Black seat rejected
    dup_b = client.post(
        f"/api/games/{game_id}/join",
        json={"color": "B", "name": "Intruder"},
    )
    assert dup_b.status_code == 409

    # Auto-join takes White
    join_w = client.post(
        f"/api/games/{game_id}/join",
        json={"color": "auto", "name": "Claude"},
    )
    assert join_w.status_code == 200
    token_w = join_w.json()["token"]
    assert join_w.json()["color"] == "W"

    # 3rd player rejected
    join_3 = client.post(
        f"/api/games/{game_id}/join",
        json={"color": "auto", "name": "Third"},
    )
    assert join_3.status_code == 409

    # Move without token -> 401
    no_tok = client.post(f"/api/games/{game_id}/moves", json={"position": "D3"})
    assert no_tok.status_code == 401

    # White trying to move on Black's turn -> 409
    wrong_turn = client.post(
        f"/api/games/{game_id}/moves",
        headers={"X-Player-Token": token_w},
        json={"position": "D3"},
    )
    assert wrong_turn.status_code == 409

    # Black illegal move -> 400 with legal_moves list
    bad_move = client.post(
        f"/api/games/{game_id}/moves",
        headers={"X-Player-Token": token_b},
        json={"position": "A1"},
    )
    assert bad_move.status_code == 400
    assert bad_move.json()["detail"]["legal_moves"] == ["C4", "D3", "E6", "F5"]

    # Black valid move via header
    move1 = client.post(
        f"/api/games/{game_id}/moves",
        headers={"X-Player-Token": token_b},
        json={"position": "D3", "comment": "Opening move"},
    )
    assert move1.status_code == 200
    state1 = move1.json()
    assert state1["current_turn"] == "W"
    assert state1["counts"] == {"B": 4, "W": 1}
    assert state1["last_move"]["comment"] == "Opening move"

    # White valid move via body token fallback
    move2 = client.post(
        f"/api/games/{game_id}/moves",
        json={"position": "C3", "token": token_w, "comment": "Counter"},
    )
    assert move2.status_code == 200
    assert move2.json()["current_turn"] == "B"

    # Resign
    resign = client.post(
        f"/api/games/{game_id}/resign",
        headers={"X-Player-Token": token_b},
        json={"comment": "GG"},
    )
    assert resign.status_code == 200
    assert resign.json()["status"] == "finished"
    assert resign.json()["winner"] == "W"


def test_wait_long_polling_wakes_up_and_times_out() -> None:
    async def scenario() -> None:
        app = create_app(GameStore())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            gid = (await client.post("/api/games", json={"title": "WaitTest"})).json()["game_id"]
            tok_b = (
                await client.post(f"/api/games/{gid}/join", json={"color": "B", "name": "B"})
            ).json()["token"]
            await client.post(f"/api/games/{gid}/join", json={"color": "W", "name": "W"})

            # Timeout case when White waits and nobody moves
            timed = await client.get(f"/api/games/{gid}/wait", params={"color": "W", "timeout": 0.2})
            assert timed.status_code == 200
            assert timed.json()["timed_out"] is True
            assert timed.json()["ready"] is False

            # Wakeup case: White waits while Black plays shortly after
            async def black_moves_soon() -> None:
                await asyncio.sleep(0.08)
                res = await client.post(
                    f"/api/games/{gid}/moves",
                    headers={"X-Player-Token": tok_b},
                    json={"position": "D3"},
                )
                assert res.status_code == 200

            mover = asyncio.create_task(black_moves_soon())
            waited = await client.get(
                f"/api/games/{gid}/wait",
                params={"color": "W", "timeout": 5.0},
            )
            await mover

            assert waited.status_code == 200
            data = waited.json()
            assert data["ready"] is True
            assert data["timed_out"] is False
            assert data["state"]["current_turn"] == "W"
            assert data["state"]["last_move"]["position"] == "D3"

    asyncio.run(scenario())


def test_sse_streams_initial_and_updated_state() -> None:
    async def scenario() -> None:
        store = GameStore()
        app = create_app(store)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            gid = (await client.post("/api/games", json={"title": "SSETest"})).json()["game_id"]
            tok_b = (
                await client.post(f"/api/games/{gid}/join", json={"color": "B", "name": "B"})
            ).json()["token"]

            received: list[dict] = []
            disconnect_event = asyncio.Event()
            first_event_seen = asyncio.Event()
            status_code: int | None = None

            scope = {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": f"/api/games/{gid}/events",
                "raw_path": f"/api/games/{gid}/events".encode(),
                "query_string": b"",
                "headers": [],
                "client": ("127.0.0.1", 12345),
                "server": ("test", 80),
            }

            async def receive() -> dict:
                await disconnect_event.wait()
                return {"type": "http.disconnect"}

            async def send(message: dict) -> None:
                nonlocal status_code
                if message["type"] == "http.response.start":
                    status_code = message["status"]
                elif message["type"] == "http.response.body":
                    chunk = message.get("body", b"").decode("utf-8")
                    for line in chunk.splitlines():
                        if line.startswith("data: "):
                            received.append(json.loads(line[len("data: ") :]))
                            first_event_seen.set()
                            if len(received) >= 2:
                                disconnect_event.set()

            sse_task = asyncio.create_task(app(scope, receive, send))
            await asyncio.wait_for(first_event_seen.wait(), timeout=2.0)

            move_res = await client.post(
                f"/api/games/{gid}/moves",
                headers={"X-Player-Token": tok_b},
                json={"position": "F5"},
            )
            assert move_res.status_code == 200
            await asyncio.wait_for(sse_task, timeout=2.0)

            assert status_code == 200
            assert len(received) == 2
            assert received[0]["counts"] == {"B": 2, "W": 2}
            assert received[1]["counts"] == {"B": 4, "W": 1}
            assert received[1]["last_move"]["position"] == "F5"
            # Subscriber should be cleaned up on disconnect
            assert len(store._games[gid].subscribers) == 0

    asyncio.run(scenario())


def test_full_game_playthrough_via_api() -> None:
    client = make_client()
    gid = client.post("/api/games", json={"title": "Full Game"}).json()["game_id"]
    tok_b = client.post(f"/api/games/{gid}/join", json={"color": "B", "name": "B"}).json()["token"]
    tok_w = client.post(f"/api/games/{gid}/join", json={"color": "W", "name": "W"}).json()["token"]
    tokens = {"B": tok_b, "W": tok_w}

    state = client.get(f"/api/games/{gid}").json()
    steps = 0
    while state["status"] == "playing":
        turn = state["current_turn"]
        move = state["legal_moves"][0]
        res = client.post(
            f"/api/games/{gid}/moves",
            headers={"X-Player-Token": tokens[turn]},
            json={"position": move},
        )
        assert res.status_code == 200
        state = res.json()
        steps += 1
        assert steps <= 64

    assert state["status"] == "finished"
    assert state["winner"] in ("B", "W", "draw")
    assert state["counts"]["B"] + state["counts"]["W"] <= 64


def test_wait_rate_limit_blocks_runaway_loop_and_resets_on_new_version() -> None:
    client = make_client()
    gid = client.post("/api/games", json={"title": "RateLimit"}).json()["game_id"]
    tok_b = client.post(f"/api/games/{gid}/join", json={"color": "B", "name": "B"}).json()["token"]

    # It is already Black's turn; first 3 rapid /wait calls on unchanged version succeed
    for _ in range(3):
        res = client.get(f"/api/games/{gid}/wait", params={"color": "B", "timeout": 1.0})
        assert res.status_code == 200
        assert res.json()["ready"] is True

    # 4th rapid /wait on the exact same version triggers 429 Too Many Requests
    blocked = client.get(f"/api/games/{gid}/wait", params={"color": "B", "timeout": 1.0})
    assert blocked.status_code == 429
    assert blocked.headers.get("retry-after") == "2"
    assert blocked.json()["detail"]["code"] == "rate_limited"

    # Submitting a move advances version -> /wait works again (and resign -> finished loop also guarded)
    move = client.post(
        f"/api/games/{gid}/moves",
        headers={"X-Player-Token": tok_b},
        json={"position": "D3"},
    )
    assert move.status_code == 200

    resign = client.post(
        f"/api/games/{gid}/resign",
        headers={"X-Player-Token": tok_b},
    )
    assert resign.status_code == 200
    assert resign.json()["last_move"]["comment"] == "Resigned"

    # Post-finish runaway loop also hits 429 on 4th rapid call
    for _ in range(3):
        r = client.get(f"/api/games/{gid}/wait", params={"color": "W", "timeout": 1.0})
        assert r.status_code == 200
        assert r.json()["state"]["status"] == "finished"

    post_finish_blocked = client.get(
        f"/api/games/{gid}/wait", params={"color": "W", "timeout": 1.0}
    )
    assert post_finish_blocked.status_code == 429
