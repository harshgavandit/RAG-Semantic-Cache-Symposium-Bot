from pathlib import Path
import requests
from streamlit.testing.v1 import AppTest

FRONTEND = Path(__file__).resolve().parents[1] / "phase2_retrieval" / "frontend.py"


def test_frontend_renders_rate_limit_without_crashing(monkeypatch):
    def post(*args, **kwargs):
        response = requests.Response()
        response.status_code = 429
        response.headers["Retry-After"] = "12"
        response._content = b'{"detail":"limited"}'
        return response
    monkeypatch.setattr(requests, "post", post)
    app = AppTest.from_file(str(FRONTEND)).run()
    app.chat_input[0].set_value("Date?").run()
    assert not app.exception
    assert "12 seconds" in app.warning[0].value
    assert len(app.session_state["messages"]) == 1


def test_frontend_renders_connection_failure(monkeypatch):
    def post(*args, **kwargs):
        raise requests.ConnectionError("private connection detail")
    monkeypatch.setattr(requests, "post", post)
    app = AppTest.from_file(str(FRONTEND)).run()
    app.chat_input[0].set_value("Date?").run()
    assert not app.exception
    assert "Cannot reach" in app.error[0].value
    assert "private" not in app.error[0].value
