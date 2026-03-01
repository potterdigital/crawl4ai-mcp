"""Unit tests for output_dir disk persistence feature.

Tests cover:
- _sanitize_filename edge cases (special chars, long URLs, empty)
- _persist_results creates .md files and manifest.json
- _persist_results handles mixed success/failure results
- crawl_many, deep_crawl, crawl_sitemap accept output_dir parameter
"""

import inspect
import json
import os
from unittest.mock import MagicMock

from crawl4ai_mcp.server import (
    _persist_results,
    _sanitize_filename,
    crawl_many,
    crawl_sitemap,
    deep_crawl,
)


# ---------------------------------------------------------------------------
# _sanitize_filename
# ---------------------------------------------------------------------------


class TestSanitizeFilename:
    def test_simple_url(self) -> None:
        """Strips scheme and converts slashes to underscores."""
        result = _sanitize_filename("https://example.com/page")
        assert result == "example_com_page"

    def test_special_characters(self) -> None:
        """Replaces query params, fragments, and special chars."""
        result = _sanitize_filename("https://example.com/path?q=hello&x=1#top")
        assert "?" not in result
        assert "&" not in result
        assert "#" not in result
        assert result == "example_com_path_q_hello_x_1_top"

    def test_long_url_truncated(self) -> None:
        """URLs longer than 200 chars are truncated."""
        long_path = "a" * 300
        result = _sanitize_filename(f"https://example.com/{long_path}")
        assert len(result) <= 200

    def test_empty_path(self) -> None:
        """URL with only scheme returns 'page' fallback."""
        result = _sanitize_filename("https://")
        assert result == "page"

    def test_trailing_slash(self) -> None:
        """Trailing underscores are stripped."""
        result = _sanitize_filename("https://example.com/")
        assert not result.endswith("_")

    def test_http_scheme_stripped(self) -> None:
        """HTTP scheme is stripped too."""
        result = _sanitize_filename("http://example.com/test")
        assert result == "example_com_test"


# ---------------------------------------------------------------------------
# _persist_results
# ---------------------------------------------------------------------------


def _make_result(url: str, success: bool = True, content: str = "page content",
                 error_message: str = "", metadata: dict | None = None):
    """Create a mock CrawlResult for testing."""
    result = MagicMock()
    result.url = url
    result.success = success
    result.error_message = error_message
    result.metadata = metadata or {}

    if success:
        result.markdown.fit_markdown = content
        result.markdown.raw_markdown = content
    else:
        result.markdown = None

    return result


class TestPersistResults:
    def test_creates_directory_and_files(self, tmp_path) -> None:
        """Creates output_dir, .md files, and manifest.json."""
        results = [
            _make_result("https://example.com/page1", content="Page 1"),
            _make_result("https://example.com/page2", content="Page 2"),
        ]
        out = str(tmp_path / "output")
        summary = _persist_results(results, out)

        assert os.path.isdir(out)
        assert os.path.isfile(os.path.join(out, "manifest.json"))

        # Check .md files exist
        files = [f for f in os.listdir(out) if f.endswith(".md")]
        assert len(files) == 2

        # Check manifest content
        with open(os.path.join(out, "manifest.json")) as f:
            manifest = json.load(f)
        assert len(manifest) == 2
        assert all(e["success"] for e in manifest)

        # Summary mentions the output dir
        assert out in summary

    def test_md_file_content(self, tmp_path) -> None:
        """Written .md file contains the crawl result content."""
        results = [_make_result("https://example.com/test", content="Hello World")]
        out = str(tmp_path / "output")
        _persist_results(results, out)

        md_files = [f for f in os.listdir(out) if f.endswith(".md")]
        assert len(md_files) == 1
        with open(os.path.join(out, md_files[0])) as f:
            assert f.read() == "Hello World"

    def test_mixed_success_failure(self, tmp_path) -> None:
        """Failures appear in manifest with error, no .md file created."""
        results = [
            _make_result("https://example.com/good", content="OK"),
            _make_result("https://example.com/bad", success=False,
                         error_message="timeout"),
        ]
        out = str(tmp_path / "output")
        summary = _persist_results(results, out)

        with open(os.path.join(out, "manifest.json")) as f:
            manifest = json.load(f)
        assert len(manifest) == 2
        assert manifest[0]["success"] is True
        assert manifest[1]["success"] is False
        assert manifest[1]["error"] == "timeout"

        # Only 1 .md file
        md_files = [f for f in os.listdir(out) if f.endswith(".md")]
        assert len(md_files) == 1

        assert "FAILED" in summary

    def test_depth_metadata_in_manifest(self, tmp_path) -> None:
        """Depth and parent_url metadata propagate to manifest entries."""
        results = [
            _make_result("https://example.com/root", content="Root",
                         metadata={"depth": 0}),
            _make_result("https://example.com/child", content="Child",
                         metadata={"depth": 1, "parent_url": "https://example.com/root"}),
        ]
        out = str(tmp_path / "output")
        _persist_results(results, out)

        with open(os.path.join(out, "manifest.json")) as f:
            manifest = json.load(f)
        assert manifest[0]["depth"] == 0
        assert manifest[1]["depth"] == 1
        assert manifest[1]["parent_url"] == "https://example.com/root"

    def test_existing_directory_ok(self, tmp_path) -> None:
        """Writing to an existing directory succeeds (no error)."""
        out = str(tmp_path / "existing")
        os.makedirs(out)
        results = [_make_result("https://example.com/page", content="Content")]
        _persist_results(results, out)
        assert os.path.isfile(os.path.join(out, "manifest.json"))


# ---------------------------------------------------------------------------
# output_dir parameter acceptance
# ---------------------------------------------------------------------------


class TestOutputDirParameterAccepted:
    def test_crawl_many_accepts_output_dir(self) -> None:
        """crawl_many has an `output_dir` parameter with default None."""
        sig = inspect.signature(crawl_many)
        assert "output_dir" in sig.parameters
        assert sig.parameters["output_dir"].default is None

    def test_deep_crawl_accepts_output_dir(self) -> None:
        """deep_crawl has an `output_dir` parameter with default None."""
        sig = inspect.signature(deep_crawl)
        assert "output_dir" in sig.parameters
        assert sig.parameters["output_dir"].default is None

    def test_crawl_sitemap_accepts_output_dir(self) -> None:
        """crawl_sitemap has an `output_dir` parameter with default None."""
        sig = inspect.signature(crawl_sitemap)
        assert "output_dir" in sig.parameters
        assert sig.parameters["output_dir"].default is None
