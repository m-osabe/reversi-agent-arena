from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


def _load_replay_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "replay_kifu.py"
    spec = importlib.util.spec_from_file_location("replay_kifu", script_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["replay_kifu"] = mod
    spec.loader.exec_module(mod)
    return mod


SAMPLE_LOG = """#1
D3
反転 1枚
初手D3です
#2
C5
反転 1枚
縦取りのC5です
#3
黒 (B) パス
Auto-pass (no legal moves)
#4
白 (W) 投了
参りました
"""


def test_parse_text_log_handles_moves_pass_and_resign() -> None:
    mod = _load_replay_module()
    parsed = mod.parse_text_log(SAMPLE_LOG, default_title="Test Log")
    assert parsed.title == "Test Log"
    assert len(parsed.entries) == 4
    assert parsed.entries[0].position == "D3"
    assert parsed.entries[0].comment == "初手D3です"
    assert parsed.entries[1].position == "C5"
    assert parsed.entries[2].passed is True
    assert parsed.entries[3].resigned is True
    assert parsed.entries[3].color == "W"
    assert parsed.entries[3].comment == "参りました"


def test_parse_json_kifu_accepts_browser_download_and_api_state() -> None:
    mod = _load_replay_module()
    browser_json = json.dumps(
        {
            "title": "Antigravity vs Claude",
            "players": {"B": "Antigravity", "W": "Claude"},
            "history": [
                {"move_number": 1, "color": "B", "position": "D3", "comment": "Opening"},
                {"move_number": 2, "color": "W", "position": "C5", "comment": "Reply"},
            ],
        },
        ensure_ascii=False,
    )
    parsed = mod.parse_json_kifu(browser_json)
    assert parsed.title == "Antigravity vs Claude"
    assert parsed.black_name == "Antigravity"
    assert parsed.white_name == "Claude"
    assert [e.position for e in parsed.entries] == ["D3", "C5"]

    # Raw GET /api/games/{id} format where players[side] is an object
    api_state_json = json.dumps(
        {
            "title": "Raw API State",
            "players": {
                "B": {"name": "Alice", "joined": True},
                "W": {"name": "Bob", "joined": True},
            },
            "history": [{"move_number": 1, "color": "B", "position": "F5"}],
        }
    )
    parsed_api = mod.parse_json_kifu(api_state_json)
    assert parsed_api.black_name == "Alice"
    assert parsed_api.white_name == "Bob"
    assert parsed_api.entries[0].position == "F5"


def test_parse_text_log_rejects_empty_input() -> None:
    mod = _load_replay_module()
    with pytest.raises(ValueError):
        mod.parse_text_log("just random text without move numbers")
