"""Unit tests for version check logic (check_update tool, helpers, startup check).

Tests cover:
- check_update when crawl4ai is up to date
- check_update when an update is available (with changelog)
- check_update when PyPI is unreachable (ConnectError)
- check_update when PyPI times out (TimeoutException)
- _fetch_changelog_summary success path (parses changelog section)
- _fetch_changelog_summary fallback (HTTP error returns URL)
- _startup_version_check logs warning when outdated
- _startup_version_check no warning when current
- _startup_version_check swallows all exceptions silently
"""

import logging

import httpx
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from crawl4ai_mcp.server import (
    _fetch_changelog_summary,
    _startup_version_check,
    check_update,
)


def _make_pypi_response(version: str) -> dict:
    """Create a minimal PyPI JSON API response dict."""
    return {"info": {"version": version}}


def _make_mock_ctx():
    """Create a mock Context for tool calls."""
    ctx = MagicMock()
    app = MagicMock()
    ctx.request_context.lifespan_context = app
    return ctx


# ---------------------------------------------------------------------------
# check_update — up to date
# ---------------------------------------------------------------------------


class TestCheckUpdateUpToDate:
    @pytest.mark.asyncio
    async def test_check_update_up_to_date(self):
        """When installed == latest, reports up to date."""
        mock_response = MagicMock()
        mock_response.json.return_value = _make_pypi_response("0.8.0")
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("crawl4ai_mcp.server.importlib.metadata.version", return_value="0.8.0"),
            patch("crawl4ai_mcp.server.httpx.AsyncClient", return_value=mock_client),
        ):
            result = await check_update(_make_mock_ctx())

        assert "up to date" in result
        assert "0.8.0" in result


# ---------------------------------------------------------------------------
# check_update — update available
# ---------------------------------------------------------------------------


class TestCheckUpdateAvailable:
    @pytest.mark.asyncio
    async def test_check_update_available(self):
        """When latest > installed, reports update with changelog."""
        mock_response = MagicMock()
        mock_response.json.return_value = _make_pypi_response("0.9.0")
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        fake_changelog = "### Added\n- **New feature** description"

        with (
            patch("crawl4ai_mcp.server.importlib.metadata.version", return_value="0.8.0"),
            patch("crawl4ai_mcp.server.httpx.AsyncClient", return_value=mock_client),
            patch("crawl4ai_mcp.server._fetch_changelog_summary", return_value=fake_changelog),
        ):
            result = await check_update(_make_mock_ctx())

        assert "Update available" in result
        assert "0.8.0" in result
        assert "0.9.0" in result
        assert "scripts/update.sh" in result
        assert "New feature" in result


# ---------------------------------------------------------------------------
# check_update — PyPI unreachable
# ---------------------------------------------------------------------------


class TestCheckUpdatePyPIUnreachable:
    @pytest.mark.asyncio
    async def test_check_update_pypi_unreachable(self):
        """ConnectError returns structured error, does not raise."""
        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.ConnectError("Connection refused")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("crawl4ai_mcp.server.importlib.metadata.version", return_value="0.8.0"),
            patch("crawl4ai_mcp.server.httpx.AsyncClient", return_value=mock_client),
        ):
            result = await check_update(_make_mock_ctx())

        assert "Version check failed" in result
        assert "0.8.0" in result
        assert "Error" in result


# ---------------------------------------------------------------------------
# check_update — PyPI timeout
# ---------------------------------------------------------------------------


class TestCheckUpdatePyPITimeout:
    @pytest.mark.asyncio
    async def test_check_update_pypi_timeout(self):
        """TimeoutException returns structured error, does not raise."""
        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.TimeoutException("Request timed out")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("crawl4ai_mcp.server.importlib.metadata.version", return_value="0.8.0"),
            patch("crawl4ai_mcp.server.httpx.AsyncClient", return_value=mock_client),
        ):
            result = await check_update(_make_mock_ctx())

        assert "Version check failed" in result
        assert "0.8.0" in result


# ---------------------------------------------------------------------------
# _fetch_changelog_summary — success
# ---------------------------------------------------------------------------


class TestFetchChangelogSummarySuccess:
    @pytest.mark.asyncio
    async def test_fetch_changelog_summary_success(self):
        """Extracts headers and bullets from valid changelog section."""
        changelog_text = (
            "# Changelog\n\n"
            "## [0.9.0] - 2025-01-15\n\n"
            "### Added\n"
            "- **Streaming support** for large pages\n"
            "- **Better proxy handling** with rotation\n\n"
            "### Fixed\n"
            "- **Memory leak** in browser sessions\n\n"
            "## [0.8.0] - 2025-01-01\n\n"
            "### Added\n"
            "- Initial release\n"
        )

        mock_response = MagicMock()
        mock_response.text = changelog_text
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("crawl4ai_mcp.server.httpx.AsyncClient", return_value=mock_client):
            result = await _fetch_changelog_summary("0.9.0")

        assert "### Added" in result
        assert "Streaming support" in result
        assert "### Fixed" in result
        assert "Memory leak" in result


# ---------------------------------------------------------------------------
# _fetch_changelog_summary — fallback
# ---------------------------------------------------------------------------


class TestFetchChangelogSummaryFallback:
    @pytest.mark.asyncio
    async def test_fetch_changelog_summary_fallback(self):
        """Returns fallback URL when HTTP request fails."""
        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.ConnectError("fail")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("crawl4ai_mcp.server.httpx.AsyncClient", return_value=mock_client):
            result = await _fetch_changelog_summary("0.9.0")

        assert "Changelog:" in result
        assert "github.com" in result


# ---------------------------------------------------------------------------
# _startup_version_check — logs warning when outdated
# ---------------------------------------------------------------------------


class TestStartupVersionCheckWarning:
    @pytest.mark.asyncio
    async def test_startup_version_check_logs_warning(self):
        """Logs warning when a newer version is available."""
        mock_response = MagicMock()
        mock_response.json.return_value = _make_pypi_response("0.9.0")
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("crawl4ai_mcp.server.importlib.metadata.version", return_value="0.8.0"),
            patch("crawl4ai_mcp.server.httpx.AsyncClient", return_value=mock_client),
            patch("crawl4ai_mcp.server.logger") as mock_logger,
        ):
            await _startup_version_check()

        mock_logger.warning.assert_called_once()
        warning_msg = mock_logger.warning.call_args[0][0]
        assert "newer" in warning_msg
        assert "update" in warning_msg.lower() or "upgrade" in warning_msg.lower()


# ---------------------------------------------------------------------------
# _startup_version_check — no warning when current
# ---------------------------------------------------------------------------


class TestStartupVersionCheckNoWarning:
    @pytest.mark.asyncio
    async def test_startup_version_check_no_warning_when_current(self):
        """Does not log warning when installed == latest."""
        mock_response = MagicMock()
        mock_response.json.return_value = _make_pypi_response("0.8.0")
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("crawl4ai_mcp.server.importlib.metadata.version", return_value="0.8.0"),
            patch("crawl4ai_mcp.server.httpx.AsyncClient", return_value=mock_client),
            patch("crawl4ai_mcp.server.logger") as mock_logger,
        ):
            await _startup_version_check()

        mock_logger.warning.assert_not_called()


# ---------------------------------------------------------------------------
# _startup_version_check — swallows exceptions
# ---------------------------------------------------------------------------


class TestStartupVersionCheckSwallowsExceptions:
    @pytest.mark.asyncio
    async def test_startup_version_check_swallows_exceptions(self):
        """Any exception is silently swallowed — never propagates."""
        mock_client = AsyncMock()
        mock_client.get.side_effect = RuntimeError("catastrophic failure")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("crawl4ai_mcp.server.importlib.metadata.version", return_value="0.8.0"),
            patch("crawl4ai_mcp.server.httpx.AsyncClient", return_value=mock_client),
        ):
            # Must not raise
            await _startup_version_check()
