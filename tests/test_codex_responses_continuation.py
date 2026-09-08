"""Sanitized Codex Responses histories with item IDs separate from call IDs."""
from __future__ import annotations

import pytest

from test_studio_chat_planning import (
    RESPONSES_READ_TOOL,
    _responses_body,
    _studio_responses_continuation_app,
)


TASK = "codex-task-sentinel: read /tmp/a.txt"
TOOL_RESULT = "Process exited with code 0\ncodex-tool-result-sentinel"


def _request_body(stream):
    return {
        "model": "Gpt_6_Astra",
        "input": [{
            "type": "message",
            "id": "msg_codex_user",
            "role": "user",
            "content": [{"type": "input_text", "text": TASK}],
        }],
        "tools": [RESPONSES_READ_TOOL],
        "tool_choice": "auto",
        "parallel_tool_calls": True,
        "store": False,
        "stream": stream,
    }


def _continuation_body(first_body, call):
    # Codex resends full history without previous_response_id. Both items carry
    # their own id; only call_id pairs the function with its execution result.
    function_call = {name: call[name] for name in (
        "type", "id", "name", "arguments", "call_id"
    )}
    function_output = {
        "type": "function_call_output",
        "id": "fco_codex_result",
        "call_id": call["call_id"],
        "output": TOOL_RESULT,
    }
    assert len({function_call["id"], function_output["id"], call["call_id"]}) == 3
    return dict(first_body, input=[*first_body["input"], function_call, function_output])


@pytest.mark.parametrize("stream", [False, True], ids=["buffered", "stream"])
@pytest.mark.parametrize("session_header", [
    pytest.param("codex-fixed-session", id="fixed_session_header"),
    pytest.param(None, id="no_session_header"),
])
def test_codex_full_history_without_previous_id_completes_studio_loop(
    stream, session_header, tmp_path,
):
    app, client, key, made = _studio_responses_continuation_app(tmp_path)
    headers = {"Authorization": f"Bearer {key}"}
    if session_header:
        headers["X-M365-Session-Id"] = session_header
    body = _request_body(stream)
    first = client.post("/v1/responses", headers=headers, json=body)
    assert first.status_code == 200, first.text
    call = next(item for item in _responses_body(first, stream)["output"]
                if item["type"] == "function_call")
    follow = _continuation_body(body, call)
    assert "previous_response_id" not in follow

    continued = client.post("/v1/responses", headers=headers, json=follow)

    assert continued.status_code == 200, continued.text
    result = _responses_body(continued, stream)
    assert result["status"] == "completed"
    assert not [item for item in result["output"] if item["type"] == "function_call"]
    assert "studio-final-answer" in continued.text
    assert app.state.call_log[-1]["tool_planning"] == "studio"
    assert "studio_fallback" not in app.state.call_log[-1]
    studio_clients = [item for item in made if item.studio]
    assert len(studio_clients) == 2
    assert [item.tones for item in studio_clients] == [["Gpt_6_Astra"], ["Gpt_6_Astra"]]
    assert all(not item.calls for item in made if not item.studio)
    assert TOOL_RESULT in "\n".join(studio_clients[1].calls[0][2])
    if session_header:
        assert studio_clients[0].calls[0][3] is studio_clients[1].calls[0][3]
        context = "\n".join(studio_clients[1].calls[0][2])
        assert TASK not in context
        assert "Assistant called tool" not in context


@pytest.mark.parametrize("stream", [False, True], ids=["buffered", "stream"])
@pytest.mark.parametrize("invalid_history", ["missing_call", "mismatched_call_id"])
def test_codex_session_header_does_not_authorize_unmatched_tool_results(
    stream, invalid_history, tmp_path,
):
    app, client, key, made = _studio_responses_continuation_app(tmp_path)
    headers = {"Authorization": f"Bearer {key}", "X-M365-Session-Id": "codex-fixed-session"}
    body = _request_body(stream)
    first = client.post("/v1/responses", headers=headers, json=body)
    assert first.status_code == 200, first.text
    call = next(item for item in _responses_body(first, stream)["output"]
                if item["type"] == "function_call")
    follow = _continuation_body(body, call)
    if invalid_history == "missing_call":
        follow["input"].pop(-2)
    else:
        # The output's independent item ID cannot authorize a wrong call_id.
        follow["input"][-1]["id"] = call["call_id"]
        follow["input"][-1]["call_id"] = "call_never_issued"
    before_calls = sum(len(item.calls) for item in made)

    rejected = client.post("/v1/responses", headers=headers, json=follow)

    assert rejected.status_code == 400, rejected.text
    expected = (
        "must resend the matching function_call"
        if invalid_history == "missing_call"
        else "call_id does not match a prior function_call"
    )
    assert expected in rejected.json()["error"]["message"]
    assert sum(len(item.calls) for item in made) == before_calls
    assert len(app.state.call_log) == 1
