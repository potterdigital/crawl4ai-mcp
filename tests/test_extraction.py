"""Unit tests for extract_structured tool, _check_api_key helper, and PROVIDER_ENV_VARS.

Tests cover:
- _check_api_key returns None when env var is set
- _check_api_key returns structured error when env var is missing
- _check_api_key allows ollama (local, no key needed)
- _check_api_key allows unknown providers (let litellm handle them)
- PROVIDER_ENV_VARS contains all expected providers
- extract_structured is registered as an MCP tool
- extract_structured docstring contains cost warning
"""

import pytest

from crawl4ai_mcp.server import (
    PROVIDER_ENV_VARS,
    _check_api_key,
    extract_structured,
    mcp,
)


# ---------------------------------------------------------------------------
# _check_api_key — key present / missing
# ---------------------------------------------------------------------------


class TestCheckApiKey:
    def test_openai_key_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Returns None when OPENAI_API_KEY is set."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
        result = _check_api_key("openai/gpt-4o-mini")
        assert result is None

    def test_openai_key_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Returns structured error mentioning OPENAI_API_KEY when not set."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        result = _check_api_key("openai/gpt-4o-mini")
        assert result is not None
        assert "OPENAI_API_KEY" in result
        assert "not set" in result

    def test_anthropic_key_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Returns structured error mentioning ANTHROPIC_API_KEY when not set."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        result = _check_api_key("anthropic/claude-sonnet-4-20250514")
        assert result is not None
        assert "ANTHROPIC_API_KEY" in result
        assert "not set" in result

    def test_ollama_no_key_needed(self) -> None:
        """Returns None for ollama — local provider, no API key required."""
        result = _check_api_key("ollama/llama3")
        assert result is None

    def test_unknown_provider_passes(self) -> None:
        """Returns None for unknown providers — let litellm handle them."""
        result = _check_api_key("some-unknown/model")
        assert result is None


# ---------------------------------------------------------------------------
# PROVIDER_ENV_VARS mapping
# ---------------------------------------------------------------------------


class TestProviderEnvVars:
    def test_known_providers_mapped(self) -> None:
        """All major cloud LLM providers are present in the mapping."""
        expected = {"openai", "anthropic", "gemini", "deepseek", "groq"}
        assert expected.issubset(set(PROVIDER_ENV_VARS.keys()))

    def test_ollama_maps_to_none(self) -> None:
        """Ollama maps to None (local provider, no key)."""
        assert PROVIDER_ENV_VARS["ollama"] is None


# ---------------------------------------------------------------------------
# extract_structured — tool registration and docstring
# ---------------------------------------------------------------------------


class TestExtractStructuredRegistration:
    def test_tool_registered(self) -> None:
        """extract_structured is registered in the FastMCP tool manager."""
        tool_names = list(mcp._tool_manager._tools.keys())
        assert "extract_structured" in tool_names

    def test_docstring_has_cost_warning(self) -> None:
        """Docstring contains prominent cost warning and mentions extract_css."""
        doc = extract_structured.__doc__
        assert doc is not None
        assert "WARNING" in doc
        assert "cost" in doc
        assert "extract_css" in doc
