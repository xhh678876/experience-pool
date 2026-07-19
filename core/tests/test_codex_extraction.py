from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


UPLOADER = _load_module("expool_test_uploader", ROOT / "dist-public" / "exp_uploader.py")
BACKFILL = _load_module(
    "expool_test_backfill",
    ROOT / "dist-public" / "session-extractor" / "extract_and_upload.py",
)


def _record(record_type: str, payload: dict, ts: str) -> str:
    return json.dumps({"timestamp": ts, "type": record_type, "payload": payload}) + "\n"


def _task_lines(turn_id: str, *, complete: bool, custom: bool = True) -> list[str]:
    lines = [
        _record(
            "event_msg",
            {"type": "task_started", "turn_id": turn_id},
            "2026-07-12T01:00:00Z",
        ),
        _record(
            "turn_context",
            {"turn_id": turn_id, "cwd": "/workspace/demo", "model": "gpt-5.6"},
            "2026-07-12T01:00:01Z",
        ),
        _record(
            "response_item",
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": f"fix {turn_id}"}]},
            "2026-07-12T01:00:02Z",
        ),
    ]
    if custom:
        lines.extend(
            [
                _record(
                    "response_item",
                    {
                        "type": "custom_tool_call",
                        "call_id": f"call-{turn_id}",
                        "name": "apply_patch",
                        "input": json.dumps({"patch": "focused change"}),
                    },
                    "2026-07-12T01:00:03Z",
                ),
                _record(
                    "response_item",
                    {
                        "type": "custom_tool_call_output",
                        "call_id": f"call-{turn_id}",
                        "output": json.dumps({"output": "patch applied"}),
                    },
                    "2026-07-12T01:00:04Z",
                ),
            ]
        )
    lines.append(
        _record(
            "response_item",
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "fixed and tested"}]},
            "2026-07-12T01:00:05Z",
        )
    )
    if complete:
        lines.append(
            _record(
                "event_msg",
                {"type": "task_complete", "turn_id": turn_id},
                "2026-07-12T01:00:06Z",
            )
        )
    return lines


def _claude_pair(index: int) -> str:
    ts = f"2026-07-12T02:00:{index:02d}Z"
    user = {
        "type": "user",
        "timestamp": ts,
        "cwd": "/workspace/demo",
        "version": "2.1.207",
        "message": {"role": "user", "content": f"repair component {index}"},
    }
    assistant = {
        "type": "assistant",
        "timestamp": ts,
        "message": {
            "role": "assistant",
            "model": "claude-opus-4-8",
            "content": [{"type": "text", "text": f"fixed component {index}"}],
        },
    }
    return json.dumps(user) + "\n" + json.dumps(assistant) + "\n"


def test_codex_tasks_resume_from_open_task(tmp_path: Path) -> None:
    rollout = tmp_path / "rollout-demo.jsonl"
    prefix = _record(
        "session_meta",
        {"cwd": "/workspace/demo", "cli_version": "0.144.1"},
        "2026-07-12T00:59:59Z",
    )
    first = _task_lines("turn-one", complete=True)
    second = _task_lines("turn-two", complete=False)
    rollout.write_text(prefix + "".join(first + second))

    tasks, offset = UPLOADER.CodexAdapter.parse_tasks(str(rollout))

    assert len(tasks) == 1
    assert tasks[0].session_id.endswith(":turn-one")
    assert tasks[0].model == "gpt-5.6"
    assert tasks[0].agent_version == "0.144.1"
    calls = [call for turn in tasks[0].trajectory for call in turn.tool_calls]
    assert calls == [
        {
            "id": "call-turn-one",
            "name": "apply_patch",
            "input": {"patch": "focused change"},
            "kind": "custom_tool_call",
        }
    ]
    tool_results = [turn for turn in tasks[0].trajectory if turn.role == "tool"]
    assert tool_results[0].tool_result_for == "call-turn-one"
    assert tool_results[0].content == "patch applied"
    assert 0 < offset < rollout.stat().st_size

    with rollout.open("a") as handle:
        handle.write(
            _record(
                "event_msg",
                {"type": "task_complete", "turn_id": "turn-two"},
                "2026-07-12T01:00:07Z",
            )
        )

    resumed, final_offset = UPLOADER.CodexAdapter.parse_tasks(
        str(rollout), start_offset=offset
    )
    assert [task.session_id.rsplit(":", 1)[-1] for task in resumed] == ["turn-two"]
    assert final_offset == rollout.stat().st_size


def test_long_claude_session_splits_into_stable_parent_linked_segments(
    tmp_path: Path,
) -> None:
    transcript = tmp_path / "claude-long.jsonl"
    transcript.write_text("".join(_claude_pair(index) for index in range(5)))

    initial = UPLOADER.ClaudeCodeAdapter.parse_tasks(
        transcript, max_turns=4, max_chars=100_000
    )

    assert [len(session.trajectory) for session in initial] == [4, 4, 2]
    assert [session.session_id for session in initial] == [
        "claude-long:seg-0001",
        "claude-long:seg-0002",
        "claude-long:seg-0003",
    ]
    assert all(
        session.extra["parent_session_id"] == "claude-long"
        for session in initial
    )
    assert [session.extra["segment_id"] for session in initial] == [
        "seg-0001",
        "seg-0002",
        "seg-0003",
    ]

    with transcript.open("a") as handle:
        handle.write(_claude_pair(5))
    resumed = UPLOADER.ClaudeCodeAdapter.parse_tasks(
        transcript, max_turns=4, max_chars=100_000
    )
    assert [len(session.trajectory) for session in resumed] == [4, 4, 4]
    assert resumed[-1].session_id == initial[-1].session_id


def test_backfill_long_claude_session_uses_same_stable_segment_shape() -> None:
    trajectory = []
    for index in range(6):
        trajectory.extend(
            [
                {"role": "user", "content": f"repair component {index}"},
                {"role": "assistant", "content": f"fixed component {index}"},
            ]
        )

    segments = BACKFILL._split_claude_trajectory(
        trajectory, max_turns=4, max_chars=100_000
    )

    assert [segment_id for segment_id, _, _ in segments] == [
        "seg-0001",
        "seg-0002",
        "seg-0003",
    ]
    assert [len(segment) for _, segment, _ in segments] == [4, 4, 4]
    assert [meta["source_turn_start"] for _, _, meta in segments] == [0, 4, 8]


def test_claude_daemon_updates_only_grown_tail_segment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transcript = tmp_path / "claude-daemon.jsonl"
    transcript.write_text("".join(_claude_pair(index) for index in range(50)))
    pushed: list[tuple[str, int]] = []

    def fake_push(session, args):
        pushed.append((session.session_id, len(session.trajectory)))
        args._push_outcome = "uploaded"
        return 0

    monkeypatch.setattr(UPLOADER, "_push", fake_push)
    monkeypatch.setenv("EXP_CLAUDE_SEGMENT_TURNS", "40")
    args = SimpleNamespace(verbose=False, dry_run=False)
    push_args = SimpleNamespace()
    state: dict = {}
    row = {
        "id": transcript.stem,
        "path": str(transcript),
        "mtime": "2026-07-12T02:00:00",
        "size_bytes": transcript.stat().st_size,
    }

    first = UPLOADER._daemon_sync_claude(
        [row],
        state,
        args,
        push_args,
        cap_per_source=10,
        cap_per_session_kb=64,
    )
    assert first == (3, 0, 0)
    assert [turns for _, turns in pushed] == [40, 40, 20]

    with transcript.open("a") as handle:
        handle.write(_claude_pair(50))
    row["size_bytes"] = transcript.stat().st_size
    pushed.clear()
    second = UPLOADER._daemon_sync_claude(
        [row],
        state,
        args,
        push_args,
        cap_per_source=10,
        cap_per_session_kb=64,
    )
    assert second == (1, 2, 0)
    assert pushed == [("claude-daemon:seg-0003", 22)]


def test_codex_backfill_splits_tasks_and_skips_open_tail(tmp_path: Path) -> None:
    rollout = tmp_path / "rollout-backfill.jsonl"
    rollout.write_text(
        _record("session_meta", {"cwd": "/workspace/demo", "cli_version": "0.144.1"}, "2026-07-12T00:00:00Z")
        + "".join(_task_lines("done-task", complete=True))
        + "".join(_task_lines("open-task", complete=False))
    )

    tasks = list(BACKFILL._iter_codex_tasks(rollout))

    assert len(tasks) == 1
    turn_id, trajectory, meta = tasks[0]
    assert turn_id == "done-task"
    assert meta["task_status"] == "complete"
    calls = [call for turn in trajectory for call in turn.get("tool_calls", [])]
    assert calls[0]["kind"] == "custom_tool_call"
    results = [turn for turn in trajectory if turn.get("role") == "tool"]
    assert results[0]["tool_result_for"] == "call-done-task"


def test_codex_tool_output_is_compacted() -> None:
    huge = "BEGIN" + ("x" * 100_000) + "END"
    turn = UPLOADER.Turn(role="tool", content=huge, tool_result_for="call-1")

    turns, stats = UPLOADER._compact_codex_turns([turn])

    assert len(turns[0].content) <= UPLOADER.CODEX_TOOL_OUTPUT_CHARS + 100
    assert turns[0].content.startswith("BEGIN")
    assert turns[0].content.endswith("END")
    assert "truncated" in turns[0].content
    assert stats["truncated_turns"] == 1


def test_runtime_wrappers_do_not_replace_real_codex_task() -> None:
    objective = "优化 Codex 增量召回并验证延迟"
    session = UPLOADER.Session(
        agent_type="codex",
        session_id="rollout:turn",
        started_at="2026-07-12T01:00:00Z",
        ended_at="2026-07-12T01:00:01Z",
        model="gpt-5.6",
        cwd="/workspace/demo",
        agent_version="0.144.1",
        trajectory=[
            UPLOADER.Turn(role="user", content="# AGENTS.md instructions\n<INSTRUCTIONS>noise</INSTRUCTIONS>"),
            UPLOADER.Turn(
                role="user",
                content=f"<goal_context><objective>{objective}</objective></goal_context>",
            ),
            UPLOADER.Turn(role="assistant", content="完成实现并通过测试"),
        ],
    )

    payload = UPLOADER.build_lite_card(
        session,
        task_type="refactor",
        sensitivity="medium",
        acl="private",
        tags=[],
    )

    assert payload["card"]["query"] == objective
    assert "AGENTS.md" not in UPLOADER._pack_transcript(session.trajectory)
    assert UPLOADER._session_has_retrievable_task(session)


def test_trivial_task_is_not_retrievable() -> None:
    trajectory = [
        {"role": "user", "content": "<environment_context>noise</environment_context>"},
        {"role": "user", "content": "继续"},
        {"role": "assistant", "content": "好的"},
    ]
    session = UPLOADER.Session(
        agent_type="codex",
        session_id="rollout:trivial",
        started_at="",
        ended_at="",
        model="",
        cwd="",
        agent_version="",
        trajectory=[UPLOADER.Turn(**turn) for turn in trajectory],
    )

    assert not UPLOADER._session_has_retrievable_task(session)
    assert not BACKFILL._trajectory_has_retrievable_task(trajectory)
