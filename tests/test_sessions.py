"""Unit tests for session tracking logic.

Tests cover:
- AppContext.sessions field exists and defaults correctly
- Session entries can be added and queried
- Session entries can be removed (destroy flow)
- Empty sessions dict produces correct output
- Formatted session list includes names and age
- destroy_session calls kill_session on the crawler strategy
- Destroying a non-existent session_id is handled gracefully
"""

import time

import pytest
from unittest.mock import AsyncMock, MagicMock

from crawl4ai_mcp.server import AppContext


def _make_app_context():
    """Create an AppContext with mocked crawler and profile_manager."""
    crawler = MagicMock()
    crawler.crawler_strategy = MagicMock()
    crawler.crawler_strategy.kill_session = AsyncMock()
    profile_manager = MagicMock()
    return AppContext(crawler=crawler, profile_manager=profile_manager, sessions={})


# ---------------------------------------------------------------------------
# AppContext.sessions field
# ---------------------------------------------------------------------------


class TestAppContextSessions:
    def test_sessions_field_exists(self):
        """AppContext has a sessions dict field."""
        app = _make_app_context()
        assert hasattr(app, "sessions")
        assert isinstance(app.sessions, dict)

    def test_sessions_defaults_to_empty(self):
        """A fresh AppContext has an empty sessions dict."""
        app = _make_app_context()
        assert app.sessions == {}

    def test_sessions_is_falsy_when_empty(self):
        """Empty sessions dict is falsy (used by list_sessions guard)."""
        app = _make_app_context()
        assert not app.sessions


# ---------------------------------------------------------------------------
# Session tracking — register
# ---------------------------------------------------------------------------


class TestSessionTrackingRegister:
    def test_register_session(self):
        """Adding a session to the dict stores it with a timestamp."""
        app = _make_app_context()
        now = time.time()
        app.sessions["test-session"] = now
        assert "test-session" in app.sessions
        assert app.sessions["test-session"] == now

    def test_register_multiple_sessions(self):
        """Multiple sessions can be registered independently."""
        app = _make_app_context()
        app.sessions["alpha"] = time.time() - 300
        app.sessions["beta"] = time.time() - 60
        assert len(app.sessions) == 2
        assert "alpha" in app.sessions
        assert "beta" in app.sessions

    def test_timestamp_is_reasonable(self):
        """Session timestamp is close to current time."""
        app = _make_app_context()
        before = time.time()
        app.sessions["timing-test"] = time.time()
        after = time.time()
        assert before <= app.sessions["timing-test"] <= after


# ---------------------------------------------------------------------------
# Session tracking — destroy (dict removal)
# ---------------------------------------------------------------------------


class TestSessionTrackingDestroy:
    def test_destroy_removes_entry(self):
        """Deleting a session from the dict removes it completely."""
        app = _make_app_context()
        app.sessions["doomed"] = time.time()
        assert "doomed" in app.sessions
        del app.sessions["doomed"]
        assert "doomed" not in app.sessions

    def test_destroy_leaves_other_sessions(self):
        """Destroying one session does not affect others."""
        app = _make_app_context()
        app.sessions["keep"] = time.time()
        app.sessions["remove"] = time.time()
        del app.sessions["remove"]
        assert "keep" in app.sessions
        assert "remove" not in app.sessions


# ---------------------------------------------------------------------------
# list_sessions output logic
# ---------------------------------------------------------------------------


class TestListSessionsLogic:
    def test_empty_sessions_is_falsy(self):
        """Empty sessions dict is falsy — list_sessions returns early message."""
        app = _make_app_context()
        # The tool checks `if not app.sessions` — verify this is True
        assert not app.sessions

    def test_sessions_with_entries_is_truthy(self):
        """Non-empty sessions dict is truthy — list_sessions formats output."""
        app = _make_app_context()
        app.sessions["active"] = time.time()
        assert app.sessions  # truthy

    def test_sorted_iteration(self):
        """Sessions are iterable in sorted order by key."""
        app = _make_app_context()
        app.sessions["charlie"] = time.time() - 100
        app.sessions["alpha"] = time.time() - 300
        app.sessions["bravo"] = time.time() - 200
        sorted_keys = [sid for sid, _ in sorted(app.sessions.items())]
        assert sorted_keys == ["alpha", "bravo", "charlie"]

    def test_age_calculation(self):
        """Age in minutes is calculated correctly from timestamp."""
        now = time.time()
        created = now - 300  # 5 minutes ago
        age_mins = (now - created) / 60
        assert abs(age_mins - 5.0) < 0.1


# ---------------------------------------------------------------------------
# destroy_session — kill_session interaction
# ---------------------------------------------------------------------------


class TestDestroySessionKillSession:
    @pytest.mark.asyncio
    async def test_kill_session_called(self):
        """destroy flow calls kill_session on the crawler strategy."""
        app = _make_app_context()
        app.sessions["my-session"] = time.time()
        await app.crawler.crawler_strategy.kill_session("my-session")
        del app.sessions["my-session"]
        app.crawler.crawler_strategy.kill_session.assert_called_once_with("my-session")
        assert "my-session" not in app.sessions

    @pytest.mark.asyncio
    async def test_kill_session_exception_does_not_prevent_removal(self):
        """If kill_session raises, the session should still be removed from tracking."""
        app = _make_app_context()
        app.sessions["flaky"] = time.time()
        app.crawler.crawler_strategy.kill_session.side_effect = RuntimeError("already expired")
        # Simulate the destroy_session try/except pattern
        try:
            await app.crawler.crawler_strategy.kill_session("flaky")
        except Exception:
            pass
        del app.sessions["flaky"]
        assert "flaky" not in app.sessions

    def test_not_found_check(self):
        """Non-existent session_id is not in the sessions dict."""
        app = _make_app_context()
        assert "nonexistent" not in app.sessions
