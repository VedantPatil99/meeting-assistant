"""First-run checks against a fake Ollama: is it running, is the model there, downloading it."""

import json

import httpx
import pytest

from meeting_assistant import setup_check
from meeting_assistant.automations import Store
from meeting_assistant.app.first_run import with_saved_model
from meeting_assistant.config import Settings

HOST = "http://localhost:11434"


def fake_ollama(models=("qwen2.5:7b",), pull_lines=()):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": m} for m in models]})
        if request.url.path == "/api/pull":
            assert json.loads(request.content) == {"model": "qwen2.5:3b", "stream": True}
            return httpx.Response(200, content="\n".join(json.dumps(x) for x in pull_lines).encode())
        return httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_running_with_model():
    state = setup_check.ollama_state(HOST, fake_ollama())
    assert state.running and state.has("qwen2.5:7b")
    assert not state.has("qwen2.5:3b")


def test_not_running(monkeypatch):
    def refuse(request):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(setup_check, "find_ollama", lambda: None)
    state = setup_check.ollama_state(HOST, httpx.Client(transport=httpx.MockTransport(refuse)))
    assert not state.running and not state.installed


def test_pull_reports_progress():
    lines = [{"status": "pulling manifest"},
             {"status": "pulling abc", "completed": 500, "total": 1000},
             {"status": "pulling abc", "completed": 1000, "total": 1000},
             {"status": "success"}]
    seen = []
    setup_check.pull_model(HOST, "qwen2.5:3b", seen.append, fake_ollama(pull_lines=lines))
    assert seen[1] == ("pulling abc", 500, 1000)
    assert seen[-1] == ("success", 0, 0)


def test_pull_error_is_raised():
    with pytest.raises(RuntimeError, match="disk full"):
        setup_check.pull_model(HOST, "qwen2.5:3b", lambda e: None,
                               fake_ollama(pull_lines=[{"error": "disk full"}]))


def test_model_picked_in_setup_wins(tmp_path):
    store = Store(tmp_path / "a.db")
    settings = Settings(meetings_dir=tmp_path)
    assert with_saved_model(settings, store).ollama_model == "qwen2.5:7b"
    store.set_pref("ollama_model", "qwen2.5:3b")
    assert with_saved_model(settings, store).ollama_model == "qwen2.5:3b"
