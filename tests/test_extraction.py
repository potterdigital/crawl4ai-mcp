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

import json

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


# ---------------------------------------------------------------------------
# _extraction_error — LLM failures reported in-band
# ---------------------------------------------------------------------------


class TestExtractionErrorDetection:
    """crawl4ai does not raise when an LLM call fails. It reports success and
    puts the failure INSIDE extracted_content as blocks flagged
    {"error": true}. Passed through verbatim that reads like a normal
    extraction with a quiet "Total tokens: 0" underneath.

    Observed live: a retired model name came back as
    {"index": 0, "error": true, "tags": ["error"],
     "content": "litellm.NotFoundError: ... no longer available"}
    and the tool reported isError=false.
    """

    def test_detects_a_provider_failure_block(self) -> None:
        from crawl4ai_mcp.server import _extraction_error

        payload = json.dumps(
            [
                {
                    "index": 0,
                    "error": True,
                    "tags": ["error"],
                    "content": "litellm.NotFoundError: model is no longer available",
                }
            ]
        )

        assert "NotFoundError" in _extraction_error(payload)

    def test_real_data_is_not_mistaken_for_an_error(self) -> None:
        """The flag is the signal, never the word 'error' in the content:
        legitimate extracted data mentions errors all the time."""
        from crawl4ai_mcp.server import _extraction_error

        payload = json.dumps(
            [
                {
                    "title": "How to handle an error in Python",
                    "tags": ["error", "howto"],
                },
                {"title": "Error budgets for SRE teams"},
            ]
        )

        assert _extraction_error(payload) is None

    def test_error_false_is_not_an_error(self) -> None:
        from crawl4ai_mcp.server import _extraction_error

        assert (
            _extraction_error(json.dumps([{"error": False, "content": "fine"}])) is None
        )

    def test_non_json_and_empty_are_passed_through(self) -> None:
        from crawl4ai_mcp.server import _extraction_error

        assert _extraction_error(None) is None
        assert _extraction_error("") is None
        assert _extraction_error("not json at all") is None

    def test_a_flagged_block_with_no_message_still_reports(self) -> None:
        from crawl4ai_mcp.server import _extraction_error

        assert _extraction_error(json.dumps([{"error": True}])) is not None
