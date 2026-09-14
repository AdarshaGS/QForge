"""Tests for services/ai_client.py — mocks subprocess/shutil.which so no
real `claude` CLI is required in CI."""
import json
import subprocess
from unittest.mock import MagicMock, patch

from services import ai_client


def _fake_completed(stdout: str, returncode: int = 0):
    proc = MagicMock()
    proc.stdout = stdout
    proc.returncode = returncode
    return proc


class _FakePopen:
    def __init__(self, stdout="", stderr="", returncode=0, poll_calls_before_done=0):
        self._stdout = stdout
        self._stderr = stderr
        self._returncode = returncode
        self._poll_calls_before_done = poll_calls_before_done
        self._poll_count = 0
        self.killed = False

    def poll(self):
        self._poll_count += 1
        if self._poll_count > self._poll_calls_before_done:
            return self._returncode
        return None

    def communicate(self, timeout=None):
        return self._stdout, self._stderr

    def kill(self):
        self.killed = True

    @property
    def returncode(self):
        return self._returncode


# ===========================================================================
# check_availability
# ===========================================================================

def test_check_availability_not_installed():
    with patch("services.ai_client.shutil.which", return_value=None):
        result = ai_client.check_availability()
    assert result.installed is False
    assert result.authenticated is False


def test_check_availability_logged_in():
    envelope = json.dumps({"loggedIn": True, "email": "test@example.com"})
    with patch("services.ai_client.shutil.which", return_value="/usr/bin/claude"), \
         patch("services.ai_client.subprocess.run", return_value=_fake_completed(envelope)):
        result = ai_client.check_availability()
    assert result.installed is True
    assert result.authenticated is True
    assert result.auth_email == "test@example.com"


def test_check_availability_not_logged_in():
    envelope = json.dumps({"loggedIn": False})
    with patch("services.ai_client.shutil.which", return_value="/usr/bin/claude"), \
         patch("services.ai_client.subprocess.run", return_value=_fake_completed(envelope)):
        result = ai_client.check_availability()
    assert result.installed is True
    assert result.authenticated is False
    assert result.detail


def test_check_availability_timeout():
    with patch("services.ai_client.shutil.which", return_value="/usr/bin/claude"), \
         patch("services.ai_client.subprocess.run",
               side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=8)):
        result = ai_client.check_availability()
    assert result.installed is True
    assert result.authenticated is False


def test_check_availability_malformed_json_never_raises():
    with patch("services.ai_client.shutil.which", return_value="/usr/bin/claude"), \
         patch("services.ai_client.subprocess.run", return_value=_fake_completed("not json")):
        result = ai_client.check_availability()
    assert result.authenticated is False


# ===========================================================================
# run_prompt
# ===========================================================================

def test_run_prompt_not_installed():
    with patch("services.ai_client.shutil.which", return_value=None):
        result = ai_client.run_prompt("hello")
    assert result.ok is False
    assert result.error_kind == "not_installed"


def test_run_prompt_happy_path_no_schema():
    envelope = json.dumps({"is_error": False, "result": "hello world"})
    fake_proc = _FakePopen(stdout=envelope, returncode=0)
    with patch("services.ai_client.shutil.which", return_value="/usr/bin/claude"), \
         patch("services.ai_client.subprocess.Popen", return_value=fake_proc):
        result = ai_client.run_prompt("say hello")
    assert result.ok is True
    assert result.text == "hello world"


def test_run_prompt_happy_path_with_structured_output():
    envelope = json.dumps({
        "is_error": False, "result": '{"name":"Bob","age":30}',
        "structured_output": {"name": "Bob", "age": 30},
    })
    fake_proc = _FakePopen(stdout=envelope, returncode=0)
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    with patch("services.ai_client.shutil.which", return_value="/usr/bin/claude"), \
         patch("services.ai_client.subprocess.Popen", return_value=fake_proc):
        result = ai_client.run_prompt("extract", json_schema=schema)
    assert result.ok is True
    assert result.data == {"name": "Bob", "age": 30}


def test_run_prompt_schema_requested_but_missing_falls_back_to_text_parse():
    envelope = json.dumps({"is_error": False, "result": '{"name":"Bob"}'})
    fake_proc = _FakePopen(stdout=envelope, returncode=0)
    schema = {"type": "object"}
    with patch("services.ai_client.shutil.which", return_value="/usr/bin/claude"), \
         patch("services.ai_client.subprocess.Popen", return_value=fake_proc):
        result = ai_client.run_prompt("extract", json_schema=schema)
    assert result.ok is True
    assert result.data == {"name": "Bob"}


def test_run_prompt_schema_requested_unparseable_result_is_parse_error():
    envelope = json.dumps({"is_error": False, "result": "not json at all"})
    fake_proc = _FakePopen(stdout=envelope, returncode=0)
    schema = {"type": "object"}
    with patch("services.ai_client.shutil.which", return_value="/usr/bin/claude"), \
         patch("services.ai_client.subprocess.Popen", return_value=fake_proc):
        result = ai_client.run_prompt("extract", json_schema=schema)
    assert result.ok is True
    assert result.error_kind == "parse_error"
    assert result.text == "not json at all"


def test_run_prompt_cli_error_envelope():
    envelope = json.dumps({"is_error": True, "result": "Not logged in"})
    fake_proc = _FakePopen(stdout=envelope, returncode=1)
    with patch("services.ai_client.shutil.which", return_value="/usr/bin/claude"), \
         patch("services.ai_client.subprocess.Popen", return_value=fake_proc):
        result = ai_client.run_prompt("hello")
    assert result.ok is False
    assert result.error_kind == "cli_error"
    assert "Not logged in" in result.error


def test_run_prompt_non_json_stdout_is_cli_error():
    fake_proc = _FakePopen(stdout="totally broken output", returncode=1)
    with patch("services.ai_client.shutil.which", return_value="/usr/bin/claude"), \
         patch("services.ai_client.subprocess.Popen", return_value=fake_proc):
        result = ai_client.run_prompt("hello")
    assert result.ok is False
    assert result.error_kind == "cli_error"


def test_run_prompt_timeout_kills_process():
    fake_proc = _FakePopen(stdout="", returncode=0, poll_calls_before_done=10_000)
    with patch("services.ai_client.shutil.which", return_value="/usr/bin/claude"), \
         patch("services.ai_client.subprocess.Popen", return_value=fake_proc), \
         patch("services.ai_client.time.sleep"):
        result = ai_client.run_prompt("hello", timeout=0.01)
    assert result.ok is False
    assert result.error_kind == "timeout"
    assert fake_proc.killed is True


def test_run_prompt_cancelled():
    import threading
    cancel_event = threading.Event()
    cancel_event.set()
    fake_proc = _FakePopen(stdout="", returncode=0, poll_calls_before_done=10_000)
    with patch("services.ai_client.shutil.which", return_value="/usr/bin/claude"), \
         patch("services.ai_client.subprocess.Popen", return_value=fake_proc):
        result = ai_client.run_prompt("hello", cancel_event=cancel_event)
    assert result.ok is False
    assert result.error_kind == "cancelled"
    assert fake_proc.killed is True


# ===========================================================================
# is_enabled / preferences integration
# ===========================================================================

# ===========================================================================
# Domain-restriction system prompt
# ===========================================================================

def test_run_prompt_includes_default_system_prompt_by_default():
    envelope = json.dumps({"is_error": False, "result": "hi"})
    fake_proc = _FakePopen(stdout=envelope, returncode=0)
    with patch("services.ai_client.shutil.which", return_value="/usr/bin/claude"), \
         patch("services.ai_client.subprocess.Popen", return_value=fake_proc) as mock_popen:
        ai_client.run_prompt("hello")
    cmd = mock_popen.call_args[0][0]
    assert "--system-prompt" in cmd
    idx = cmd.index("--system-prompt")
    assert cmd[idx + 1] == ai_client.DEFAULT_SYSTEM_PROMPT
    assert "SQL" in ai_client.DEFAULT_SYSTEM_PROMPT


def test_run_prompt_system_prompt_override():
    envelope = json.dumps({"is_error": False, "result": "hi"})
    fake_proc = _FakePopen(stdout=envelope, returncode=0)
    with patch("services.ai_client.shutil.which", return_value="/usr/bin/claude"), \
         patch("services.ai_client.subprocess.Popen", return_value=fake_proc) as mock_popen:
        ai_client.run_prompt("hello", system_prompt="custom prompt")
    cmd = mock_popen.call_args[0][0]
    idx = cmd.index("--system-prompt")
    assert cmd[idx + 1] == "custom prompt"


def test_run_prompt_empty_system_prompt_omits_flag():
    envelope = json.dumps({"is_error": False, "result": "hi"})
    fake_proc = _FakePopen(stdout=envelope, returncode=0)
    with patch("services.ai_client.shutil.which", return_value="/usr/bin/claude"), \
         patch("services.ai_client.subprocess.Popen", return_value=fake_proc) as mock_popen:
        ai_client.run_prompt("hello", system_prompt="")
    cmd = mock_popen.call_args[0][0]
    assert "--system-prompt" not in cmd


def test_is_enabled_reads_preference(tmp_path, monkeypatch):
    from services import preferences
    monkeypatch.setattr(preferences, "_FILE", str(tmp_path / "preferences.json"))
    assert ai_client.is_enabled() is False
    preferences.set("ai.enabled", True)
    assert ai_client.is_enabled() is True
