"""
ai_client
=========
QForge's only bridge to an LLM. Deliberately not an API-key/HTTP client —
it shells out to the user's own locally-installed `claude` CLI (Claude
Code) in non-interactive mode, so auth is whatever the user already has
configured there (subscription login or ANTHROPIC_API_KEY) and QForge
itself never stores or asks for a key. See ai/ (repo-root design notes,
not app code) issue #341 for the scoping rationale.

Every public function here resolves to a plain dataclass result and never
raises — a missing/unauthenticated/misbehaving CLI is a normal, expected
outcome (most users won't have this configured), not an exceptional one.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass

from utils.logger import get_logger

logger = get_logger()

# Fixed, short-ish timeout for the cheap local-only auth probe — this never
# hits the network, so a slow response means something is actually wrong.
_AUTH_STATUS_TIMEOUT = 8.0

# Soft app-wide cap on concurrent `claude` subprocesses, so a pathological
# case (every open tab firing an AI request at once) can't fork unbounded
# processes. Each capability's own UI additionally guards against a second
# concurrent call from the *same* widget (see ui/ai_async.py) — this is
# the cross-widget backstop.
_MAX_CONCURRENT_CALLS = 3
_call_semaphore = threading.Semaphore(_MAX_CONCURRENT_CALLS)

# Applied to every call by default (see run_prompt's system_prompt param) —
# QForge is a SQL/database tool, not a general chat client, so every AI
# Assistance capability is scoped to that domain regardless of what a user
# types into the one genuinely free-text surface (schema chat). This is
# the primary guard against domain mismatch/off-topic token spend — not a
# client-side keyword filter, which would be fragile (easy to bypass by
# rephrasing, and prone to false-positives on legitimate SQL questions that
# happen to use unusual wording) and wouldn't reliably save tokens anyway,
# since the question text still has to be sent to be classified. A full
# --system-prompt override (not --append-system-prompt) is used so this is
# the *entire* operating context — nothing of "Claude Code" the coding
# assistant carries over, on top of --safe-mode/--tools "" already
# stripping tool use, MCP, and the user's own CLAUDE.md/skills/plugins.
DEFAULT_SYSTEM_PROMPT = (
    "You are a SQL and database assistant embedded in QForge, a desktop "
    "SQL client for MySQL and PostgreSQL. You help with SQL queries, "
    "database schema design, data modeling, and query optimization for the "
    "user's connected database — nothing else. If asked about anything "
    "outside that scope (general knowledge, unrelated programming "
    "languages/topics, personal advice, or anything not about SQL/"
    "databases), politely decline and say you can only help with SQL and "
    "database questions here. Do not attempt the off-topic request."
)


@dataclass
class AiAvailability:
    installed: bool
    authenticated: bool
    auth_email: str | None = None
    detail: str = ""


@dataclass
class AiResult:
    ok: bool
    text: str = ""
    data: dict | None = None
    error: str | None = None
    # One of: not_installed | not_authenticated | timeout | parse_error |
    # cli_error | cancelled
    error_kind: str | None = None


def _claude_path() -> str | None:
    return shutil.which("claude")


def is_enabled() -> bool:
    """Whether the user has opted in via Preferences. This is independent
    of check_availability() — a user can enable AI while the CLI is
    momentarily unauthenticated; each entry point shows the gated state
    until they log in and hit Recheck, rather than silently re-disabling
    the preference itself."""
    from services import preferences
    return bool(preferences.get("ai.enabled", False))


def check_availability(timeout: float = _AUTH_STATUS_TIMEOUT) -> AiAvailability:
    """Best-effort probe of whether AI features can work right now. Never
    raises — any failure to determine status collapses to "not available"
    with a human-readable `detail`, never a fabricated "yes"."""
    claude_bin = _claude_path()
    if not claude_bin:
        return AiAvailability(
            installed=False, authenticated=False,
            detail="Claude Code CLI not found on PATH.",
        )
    try:
        proc = subprocess.run(
            [claude_bin, "auth", "status"],
            capture_output=True, text=True, timeout=timeout,
        )
        status = json.loads(proc.stdout)
        logged_in = bool(status.get("loggedIn"))
        return AiAvailability(
            installed=True, authenticated=logged_in,
            auth_email=status.get("email") if logged_in else None,
            detail="" if logged_in else "Claude Code is installed but not logged in.",
        )
    except subprocess.TimeoutExpired:
        return AiAvailability(installed=True, authenticated=False,
                               detail="Timed out checking Claude Code auth status.")
    except Exception as ex:
        logger.debug(f"ai_client: check_availability failed: {ex}")
        return AiAvailability(installed=True, authenticated=False,
                               detail="Could not determine Claude Code auth status.")


def run_prompt(
    prompt: str,
    *,
    system_prompt: str | None = None,
    json_schema: dict | None = None,
    timeout: float = 30.0,
    cancel_event: "threading.Event | None" = None,
) -> AiResult:
    """Run *prompt* through the local `claude` CLI, non-interactively, and
    return a structured AiResult — success or failure, never an exception.

    *system_prompt*: omit (or pass None) to get DEFAULT_SYSTEM_PROMPT's
    SQL-only domain guard — every real call site should do this. Pass an
    explicit string to override it, or "" to send no system prompt at all
    (only meaningful for tests).

    Call this off the Qt main thread (see ui/ai_async.py's start_ai_call);
    a real call takes several seconds (CLI startup + model latency).
    """
    claude_bin = _claude_path()
    if not claude_bin:
        return AiResult(ok=False, error="Claude Code CLI not found on PATH.",
                         error_kind="not_installed")

    effective_system_prompt = (
        DEFAULT_SYSTEM_PROMPT if system_prompt is None else system_prompt)

    cmd = [
        claude_bin, "-p", prompt,
        "--output-format", "json",
        "--safe-mode",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--tools", "",
    ]
    if effective_system_prompt:
        cmd += ["--system-prompt", effective_system_prompt]
    if json_schema is not None:
        cmd += ["--json-schema", json.dumps(json_schema)]

    if not _call_semaphore.acquire(timeout=timeout):
        return AiResult(ok=False, error="Too many AI requests in flight.",
                         error_kind="cli_error")
    try:
        return _run_subprocess(cmd, timeout=timeout, cancel_event=cancel_event,
                                want_schema=json_schema is not None)
    finally:
        _call_semaphore.release()


def _run_subprocess(cmd: list, *, timeout: float,
                     cancel_event: "threading.Event | None",
                     want_schema: bool) -> AiResult:
    # Popen (not subprocess.run(timeout=...)) so a timeout can explicitly
    # kill the child rather than relying on subprocess.run's own cleanup,
    # which doesn't reliably reach process trees `claude` may spawn.
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    start = time.monotonic()
    poll_interval = 0.1
    while True:
        if proc.poll() is not None:
            break
        if cancel_event is not None and cancel_event.is_set():
            _kill(proc)
            return AiResult(ok=False, error="Cancelled.", error_kind="cancelled")
        if time.monotonic() - start > timeout:
            _kill(proc)
            return AiResult(ok=False, error=f"Timed out after {timeout:.0f}s.",
                             error_kind="timeout")
        time.sleep(poll_interval)

    stdout, stderr = proc.communicate()

    try:
        envelope = json.loads(stdout)
    except Exception:
        logger.debug(f"ai_client: non-JSON stdout (exit={proc.returncode}): "
                      f"stdout={stdout!r} stderr={stderr!r}")
        return AiResult(ok=False, error="Claude CLI returned an unexpected response.",
                         error_kind="cli_error")

    if envelope.get("is_error"):
        return AiResult(ok=False, error=str(envelope.get("result") or "Unknown error."),
                         error_kind="cli_error")

    text = str(envelope.get("result") or "")
    if not want_schema:
        return AiResult(ok=True, text=text)

    data = envelope.get("structured_output")
    if data is None:
        try:
            data = json.loads(text)
        except Exception:
            return AiResult(ok=True, text=text, error="Couldn't parse a structured response.",
                             error_kind="parse_error")
    return AiResult(ok=True, text=text, data=data)


def _kill(proc: subprocess.Popen) -> None:
    try:
        proc.kill()
        proc.communicate(timeout=5)
    except Exception as ex:
        logger.debug(f"ai_client: failed to kill subprocess: {ex}")
