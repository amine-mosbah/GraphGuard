"""Tests for the analyst assistant: gating, tool execution, and the agent loop.

The Anthropic client is replaced by a scripted fake — these tests prove the harness
(auth, feature flag, tool dispatch, SSE framing, usage accounting), not the model.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from graphguard.platform import assistant, auth, security
from graphguard.platform.assistant import ChatMessage, ChatRequest, execute_tool, stream_chat
from graphguard.platform.db import session_scope
from graphguard.platform.models import Role, User
from graphguard.platform.settings import get_settings


@pytest.fixture
def api(platform_env: dict[str, Any]) -> TestClient:
    """A client with the auth and assistant routes mounted and one analyst provisioned."""
    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(assistant.router)
    with session_scope() as session:
        session.add(
            User(
                email="analyst@eniso.tn",
                hashed_password=security.hash_password("analyst-password-01"),
                role=Role.ANALYST,
            )
        )
    return TestClient(app)


def _token(api: TestClient) -> dict[str, str]:
    body = api.post(
        "/api/auth/login",
        json={"email": "analyst@eniso.tn", "password": "analyst-password-01"},
    ).json()
    return {"Authorization": f"Bearer {body['access_token']}"}


def _enable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRAPHGUARD_ASSISTANT_ENABLED", "true")
    get_settings.cache_clear()


def _events(sse_text: str) -> list[dict[str, Any]]:
    return [
        json.loads(line[len("data: ") :])
        for line in sse_text.splitlines()
        if line.startswith("data: ")
    ]


# --------------------------------------------------------------------------- gating


def test_assistant_routes_require_authentication(api: TestClient) -> None:
    assert api.get("/api/v1/assistant/capabilities").status_code == 401
    assert api.post("/api/v1/assistant/chat", json={"messages": []}).status_code == 401


def test_assistant_is_disabled_by_default(api: TestClient) -> None:
    headers = _token(api)
    assert api.get("/api/v1/assistant/capabilities", headers=headers).json() == {
        "enabled": False,
        "model": None,
    }
    response = api.post(
        "/api/v1/assistant/chat",
        json={"messages": [{"role": "user", "content": "hi"}]},
        headers=headers,
    )
    assert response.status_code == 404


def test_capabilities_report_the_model_when_enabled(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(monkeypatch)
    body = api.get("/api/v1/assistant/capabilities", headers=_token(api)).json()
    assert body == {"enabled": True, "model": "claude-opus-5", "provider": "anthropic"}


def test_the_last_message_must_be_from_the_user(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(monkeypatch)
    response = api.post(
        "/api/v1/assistant/chat",
        json={
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "x"},
            ]
        },
        headers=_token(api),
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------- tool execution


def _analyst() -> User:
    with session_scope() as session:
        user = session.query(User).filter_by(email="analyst@eniso.tn").one()
        session.expunge(user)
        return user


def test_tools_answer_from_an_empty_database(api: TestClient) -> None:
    user = _analyst()
    with session_scope() as session:
        stats = execute_tool(session, user, "get_stats", {})
        assert stats["runs"] == 0 and stats["alerts"] == 0
        assert execute_tool(session, user, "list_alerts", {})["alerts"] == []
        assert execute_tool(session, user, "list_runs", {})["runs"] == []


def test_tool_errors_come_back_as_data_not_exceptions(api: TestClient) -> None:
    user = _analyst()
    with session_scope() as session:
        assert "error" in execute_tool(session, user, "get_alert", {"alert_id": "nope"})
        assert "error" in execute_tool(
            session, user, "transition_alert", {"alert_id": "nope", "state": "investigating"}
        )
        assert "error" in execute_tool(
            session, user, "transition_alert", {"alert_id": "nope", "state": "not-a-state"}
        )
        assert "error" in execute_tool(session, user, "no_such_tool", {})


def test_open_page_is_a_client_side_no_op(api: TestClient) -> None:
    user = _analyst()
    with session_scope() as session:
        result = execute_tool(session, user, "open_page", {"path": "/alerts"})
    assert result == {"ok": True, "navigated_to": "/alerts"}


# --------------------------------------------------------------------------- the agent loop


class _FakeStream:
    def __init__(self, final: SimpleNamespace):
        self._final = final

    def __enter__(self) -> _FakeStream:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def __iter__(self) -> Any:
        # One visible text delta per turn, so the streaming path is exercised.
        yield SimpleNamespace(
            type="content_block_delta",
            delta=SimpleNamespace(type="text_delta", text="…"),
        )

    def get_final_message(self) -> SimpleNamespace:
        return self._final


class _FakeClient:
    """Plays back a scripted sequence of final messages, one per model turn."""

    def __init__(self, finals: list[SimpleNamespace]):
        self._finals = list(finals)
        self.requests: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(stream=self._stream)

    def _stream(self, **kwargs: Any) -> _FakeStream:
        # Snapshot the message list: the loop mutates it in place after the call.
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        return _FakeStream(self._finals.pop(0))


def _final(content: list[SimpleNamespace], stop_reason: str) -> SimpleNamespace:
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=100, output_tokens=25),
    )


def test_the_loop_executes_tools_and_reports_usage(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeClient(
        [
            _final(
                [SimpleNamespace(type="tool_use", id="tu_1", name="get_stats", input={})],
                stop_reason="tool_use",
            ),
            _final([SimpleNamespace(type="text", text="All quiet.")], stop_reason="end_turn"),
        ]
    )
    monkeypatch.setattr(assistant, "_make_client", lambda: fake)

    request = ChatRequest(messages=[ChatMessage(role="user", content="how are things?")])
    events = _events("".join(stream_chat(request, _analyst())))

    kinds = [event["type"] for event in events]
    assert kinds.count("tool_use") == 1 and kinds.count("tool_result") == 1
    assert kinds[-2:] == ["usage", "done"]

    tool_result = next(event for event in events if event["type"] == "tool_result")
    assert tool_result["name"] == "get_stats" and tool_result["is_error"] is False
    assert tool_result["result"]["runs"] == 0

    usage = next(event for event in events if event["type"] == "usage")
    assert usage == {"type": "usage", "input_tokens": 200, "output_tokens": 50}

    # The second request must carry the tool result back to the model.
    followup = fake.requests[1]["messages"]
    assert followup[-1]["role"] == "user"
    assert followup[-1]["content"][0]["type"] == "tool_result"


def test_open_page_emits_a_navigate_event(api: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(
        [
            _final(
                [
                    SimpleNamespace(
                        type="tool_use", id="tu_1", name="open_page", input={"path": "/chains"}
                    )
                ],
                stop_reason="tool_use",
            ),
            _final([SimpleNamespace(type="text", text="Here.")], stop_reason="end_turn"),
        ]
    )
    monkeypatch.setattr(assistant, "_make_client", lambda: fake)

    request = ChatRequest(messages=[ChatMessage(role="user", content="show me the chains")])
    events = _events("".join(stream_chat(request, _analyst())))
    assert {"type": "navigate", "path": "/chains"} in events


def test_a_dead_client_yields_an_error_event_not_a_crash(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken() -> Any:
        raise RuntimeError("no credentials")

    monkeypatch.setattr(assistant, "_make_client", broken)
    request = ChatRequest(messages=[ChatMessage(role="user", content="hi")])
    events = _events("".join(stream_chat(request, _analyst())))
    assert events[0]["type"] == "error" and "no credentials" in events[0]["message"]


# --------------------------------------------------------------------------- the openai driver


def _chunk(
    *,
    content: str | None = None,
    tool_calls: list[SimpleNamespace] | None = None,
    finish: str | None = None,
    usage: SimpleNamespace | None = None,
    choices: bool = True,
) -> SimpleNamespace:
    if not choices:
        return SimpleNamespace(choices=[], usage=usage)
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(content=content, tool_calls=tool_calls),
                finish_reason=finish,
            )
        ],
        usage=usage,
    )


class _FakeOpenAIClient:
    """Plays back scripted chat-completion chunk streams, one list per model turn."""

    def __init__(self, turns: list[list[SimpleNamespace]]):
        self._turns = list(turns)
        self.requests: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any) -> Any:
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        return iter(self._turns.pop(0))


def _use_openai(monkeypatch: pytest.MonkeyPatch, fake: _FakeOpenAIClient) -> None:
    monkeypatch.setenv("GRAPHGUARD_ASSISTANT_PROVIDER", "openai")
    get_settings.cache_clear()
    monkeypatch.setattr(assistant, "_make_openai_client", lambda: fake)


def test_the_openai_driver_executes_tools_and_reports_usage(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeOpenAIClient(
        [
            [
                _chunk(
                    tool_calls=[
                        SimpleNamespace(
                            index=0,
                            id="call_1",
                            function=SimpleNamespace(name="get_stats", arguments=""),
                        )
                    ]
                ),
                _chunk(
                    tool_calls=[
                        SimpleNamespace(
                            index=0,
                            id=None,
                            function=SimpleNamespace(name=None, arguments="{}"),
                        )
                    ],
                    finish="tool_calls",
                ),
                _chunk(
                    choices=False,
                    usage=SimpleNamespace(prompt_tokens=80, completion_tokens=20),
                ),
            ],
            [
                _chunk(content="All quiet."),
                _chunk(
                    finish="stop",
                    usage=SimpleNamespace(prompt_tokens=120, completion_tokens=10),
                ),
            ],
        ]
    )
    _use_openai(monkeypatch, fake)

    request = ChatRequest(messages=[ChatMessage(role="user", content="how are things?")])
    events = _events("".join(stream_chat(request, _analyst())))

    kinds = [event["type"] for event in events]
    assert kinds.count("tool_use") == 1 and kinds.count("tool_result") == 1
    assert kinds[-2:] == ["usage", "done"]
    assert {"type": "text", "text": "All quiet."} in events

    usage = next(event for event in events if event["type"] == "usage")
    assert usage == {"type": "usage", "input_tokens": 200, "output_tokens": 30}

    # The replayed conversation must carry the assistant's tool call and the tool
    # result in the chat-completions dialect.
    followup = fake.requests[1]["messages"]
    assert followup[0]["role"] == "system"
    assert followup[-2]["role"] == "assistant"
    assert followup[-2]["tool_calls"][0]["function"]["name"] == "get_stats"
    assert followup[-1]["role"] == "tool" and followup[-1]["tool_call_id"] == "call_1"


def test_the_openai_driver_streams_plain_answers_without_tools(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeOpenAIClient(
        [
            [
                _chunk(content="Hello "),
                _chunk(content="analyst."),
                _chunk(
                    finish="stop",
                    usage=SimpleNamespace(prompt_tokens=50, completion_tokens=5),
                ),
            ]
        ]
    )
    _use_openai(monkeypatch, fake)

    request = ChatRequest(messages=[ChatMessage(role="user", content="hi")])
    events = _events("".join(stream_chat(request, _analyst())))
    text = "".join(event["text"] for event in events if event["type"] == "text")
    assert text == "Hello analyst."
    assert events[-1] == {"type": "done"}
