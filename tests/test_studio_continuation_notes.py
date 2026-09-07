from __future__ import annotations

import json

import pytest

from test_studio_chat_planning import (
    StudioResponsesContinuationClient,
    _custom_studio_app,
    _tool_request,
)


FINAL_JSON = '{"ok":true,"value":"checked"}'
TASK = "Inspect /tmp/a.txt, then return only a JSON object containing ok and value."


class JsonContinuationClient(StudioResponsesContinuationClient):
    async def chat_stream(self, prompt, context=None, session=None, images=None):
        async for part in super().chat_stream(prompt, context, session, images):
            yield FINAL_JSON if part == "studio-final-answer" else part


def _events(response):
    return [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]


def _answer_text(response, endpoint, stream):
    if stream:
        events = _events(response)
        if endpoint == "chat/completions":
            return "".join(
                str(choice.get("delta", {}).get("content") or "")
                for event in events
                for choice in event.get("choices", [])
            )
        if endpoint == "messages":
            return "".join(
                event["delta"]["text"]
                for event in events
                if event.get("type") == "content_block_delta"
                and event.get("delta", {}).get("type") == "text_delta"
            )
        return "".join(
            event["delta"]
            for event in events
            if event.get("type") == "response.output_text.delta"
        )
    body = response.json()
    if endpoint == "chat/completions":
        return body["choices"][0]["message"]["content"]
    if endpoint == "messages":
        return "".join(
            block["text"] for block in body["content"] if block["type"] == "text"
        )
    return "".join(
        block["text"]
        for item in body["output"] if item["type"] == "message"
        for block in item["content"] if block["type"] == "output_text"
    )


def _continue_tool_loop(tmp_path, endpoint, stream, tool_choice="auto"):
    app, client, key, made = _custom_studio_app(
        tmp_path, lambda: JsonContinuationClient(studio=True)
    )
    user = {"role": "user", "content": TASK}
    first = _tool_request(
        client, key, endpoint,
        **({"input": TASK} if endpoint == "responses" else {"messages": [user]}),
    )
    assert first.status_code == 200
    first_body = first.json()
    if endpoint == "responses":
        call = next(
            item for item in first_body["output"] if item["type"] == "function_call"
        )
        continuation = {
            "previous_response_id": first_body["id"],
            "input": [{
                "type": "function_call_output",
                "call_id": call["call_id"],
                "output": "inspection-complete",
            }],
        }
    elif endpoint == "messages":
        call = next(
            item for item in first_body["content"] if item["type"] == "tool_use"
        )
        continuation = {"messages": [
            user,
            {"role": "assistant", "content": first_body["content"]},
            {"role": "user", "content": [{
                "type": "tool_result",
                "tool_use_id": call["id"],
                "content": "inspection-complete",
            }]},
        ]}
    else:
        assistant = first_body["choices"][0]["message"]
        continuation = {"messages": [
            user,
            assistant,
            {
                "role": "tool",
                "tool_call_id": assistant["tool_calls"][0]["id"],
                "content": "inspection-complete",
            },
        ]}
    if endpoint == "messages" and tool_choice == "auto":
        tool_choice = {"type": "auto"}
    response = _tool_request(
        client, key, endpoint, stream=stream, tool_choice=tool_choice, **continuation
    )
    return app, response, made


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("endpoint", ["chat/completions", "messages", "responses"])
def test_studio_auto_tool_continuation_preserves_final_json(endpoint, stream, tmp_path):
    app, response, made = _continue_tool_loop(tmp_path, endpoint, stream)

    assert response.status_code == 200
    assert app.state.call_log[-1]["tool_planning"] == "studio"
    assert app.state.call_log[-1]["read_only_guard"] is False
    assert not made[0].calls and not made[2].calls
    assert _answer_text(response, endpoint, stream) == FINAL_JSON


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("endpoint", ["chat/completions", "messages", "responses"])
@pytest.mark.parametrize("choice", ["required", "forced"])
def test_studio_tool_continuation_keeps_missing_demanded_tool_error(
    choice, endpoint, stream, tmp_path,
):
    if choice == "required":
        tool_choice = {"type": "any"} if endpoint == "messages" else "required"
    elif endpoint == "chat/completions":
        tool_choice = {"type": "function", "function": {"name": "Read"}}
    elif endpoint == "messages":
        tool_choice = {"type": "tool", "name": "Read"}
    else:
        tool_choice = {"type": "function", "name": "Read"}

    app, response, _made = _continue_tool_loop(
        tmp_path, endpoint, stream, tool_choice=tool_choice
    )

    assert app.state.call_log[-1]["read_only_guard"] is False
    assert response.status_code == (
        200 if stream else 502 if endpoint == "responses" else 400
    )
    if endpoint == "responses":
        if stream:
            events = _events(response)
            assert any(event.get("type") == "error" for event in events)
            assert any(
                event.get("type") == "response.failed"
                and event["response"]["status"] == "failed"
                for event in events
            )
        assert "required tool_choice" in response.text
    else:
        text = (
            _answer_text(response, endpoint, stream)
            if stream else json.dumps(response.json(), ensure_ascii=False)
        )
        assert "tool_choice" in text
        assert ("tool_choice=required" if choice == "required" else "Read") in text
        assert "Studio Agent" in text
