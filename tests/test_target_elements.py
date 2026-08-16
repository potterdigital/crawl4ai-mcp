"""Tests for target_elements, the non-destructive alternative to css_selector.

`css_selector` narrows the DOCUMENT crawl4ai works from, not just the output.
Everything read from outside the selector goes with it: `<title>` and the meta
description live in `<head>`, so they come back None, and links are restricted
to whatever sits inside the scope. Even `css_selector="body"` loses the title.

Measured on webscraper.io/test-sites/tables with the same selector:

    css_selector="table.table"        title=None  links 0/0   markdown 369 chars
    target_elements=["table.table"]   title set   links 20/5  markdown 369 chars

Identical markdown, and one of them throws the rest of the page away. That is
worth a parameter rather than a footnote, because the loss is silent: a caller
who scoped a crawl and then turned on include_links gets empty lists and no
reason for them.
"""

import inspect

from crawl4ai_mcp.server import (
    crawl_many,
    crawl_sitemap,
    crawl_url,
    deep_crawl,
    extract_css,
)

CRAWL_TOOLS = (crawl_url, crawl_many, crawl_sitemap, deep_crawl)


class TestParameterIsWired:
    def test_every_crawl_tool_accepts_it(self) -> None:
        for tool in CRAWL_TOOLS:
            params = inspect.signature(tool).parameters
            assert "target_elements" in params, tool.__name__
            assert params["target_elements"].default is None, tool.__name__

    def test_it_reaches_the_run_config(self) -> None:
        """The regression this guards: parameter accepted, then dropped.

        Adding it to the signature and the docstring without adding it to
        per_call_kwargs would type-check, pass every registration test, and
        silently do nothing on every call.
        """
        for tool in CRAWL_TOOLS:
            src = inspect.getsource(tool)
            assert '"target_elements"] = target_elements' in src, tool.__name__

    def test_it_is_a_list_not_a_string(self) -> None:
        """crawl4ai's parameter takes a list; a bare string would silently
        iterate character by character into meaningless selectors."""
        for tool in CRAWL_TOOLS:
            annotation = (
                inspect.signature(tool).parameters["target_elements"].annotation
            )
            assert "list[str]" in str(annotation), tool.__name__

    def test_crawl4ai_still_accepts_the_key(self) -> None:
        """Config keys are validated against CrawlerRunConfig's live signature,
        so an upstream rename would strand this parameter rather than error."""
        from crawl4ai import CrawlerRunConfig

        assert (
            "target_elements" in inspect.signature(CrawlerRunConfig.__init__).parameters
        )


class TestNotOnExtractionTools:
    def test_extract_css_does_not_take_it(self) -> None:
        """target_elements scopes MARKDOWN generation, and the extraction tools
        generate none. Offering it there would imply it narrows the schema's
        search, which it does not."""
        assert "target_elements" not in inspect.signature(extract_css).parameters


class TestDocumentedTradeoff:
    def test_css_selector_docs_warn_about_the_loss(self) -> None:
        """The loss is invisible at the call site, so it has to be in the doc
        the model actually reads before choosing between the two."""
        for tool in CRAWL_TOOLS:
            doc = tool.__doc__ or ""
            assert "target_elements" in doc, tool.__name__
            lowered = doc.lower()
            assert "title" in lowered and "description" in lowered, tool.__name__
