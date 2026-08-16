# Where this server stops and crawl4ai starts

This server is a **wrapper**. Its job is to make crawl4ai pleasant to use from
an MCP client, not to reimplement it. Anything crawl4ai already does well
should be exposed, not rebuilt.

That principle raises an obvious question for anyone reading the code: *why is
there hand-rolled sitemap parsing / filename logic / browser health checking in
here at all?* This document answers it, so the next person does not "simplify"
something into a regression.

All findings below were verified against **crawl4ai 0.9.2** (2026-08-16) by
reading the installed package, not the docs. Re-verify on a major upgrade.

## Hand-rolled on purpose

| Ours | Why crawl4ai's version does not fit |
| --- | --- |
| `_fetch_sitemap_urls` / `crawl_sitemap` | `AsyncUrlSeeder`, `aseed_urls`, and `amap_domain` all take a **domain** and guess paths from `/sitemap.xml`, `/sitemap_index.xml`, or `robots.txt`. None accepts an explicit sitemap URL, so a WordPress `/wp-sitemap.xml` or a CDN-hosted sitemap at an arbitrary path is unreachable through them. crawl4ai's better parser (`_iter_sitemap`) is private and unexported; the pattern was ported rather than called. |
| `_chromium_status` | `crawl4ai-doctor` → `install.doctor()` calls **`sys.exit(0)` unconditionally**, even on failure, and performs a live network crawl rather than a local presence check. `utils.get_chromium_path()` caches its result to disk and does not re-validate, so it reports "ready" after an uninstall. |
| `_install_browser` shelling out | `install.post_install()` calls `subprocess.check_call` **without capturing output**, so calling it in-process would write Playwright's install progress to our stdout and corrupt the MCP transport. Shelling out to the console script and capturing is the only stdout-safe route. |
| `create_session` | crawl4ai's own `AsyncPlaywrightCrawlerStrategy.create_session` raises `AttributeError` on its own missing `self.user_agent` in 0.9.2. It is broken; do not migrate to it. |
| `_persist_results` | No native "write N pages as individual files plus a manifest" exists. The CLI's `--output-file` writes a single file, and `model_dump()` serializes the whole `CrawlResult` including raw HTML and binary PDF bytes. |
| `_crawl_with_overrides` | `CrawlerRunConfig` has no `headers` or `cookies` parameters — they exist only on the global `BrowserConfig`. Per-request injection has to go through Playwright hooks. |

## Deliberately NOT hand-rolled

- **Valid config keys** are read from `CrawlerRunConfig`'s live signature
  (`profiles._valid_config_keys`), never a hand-written list. The list this
  replaced named 20 keys against a class accepting 99, silently making 77
  upstream parameters unreachable.
- **Browser launch flags.** crawl4ai already sets `--disable-gpu`,
  `--disable-dev-shm-usage` and `--no-sandbox` and dedupes. Passing them again
  was inert, and re-adding `--disable-gpu` would undo crawl4ai's deliberate
  omission of it under stealth (it kills WebGL, which anti-bot sensors read as
  headless).

## Invariants that are easy to break

- **Never put per-call data on the shared crawler or its strategy.** The
  crawler is a process-wide singleton and crawl4ai keeps exactly one hook slot
  per hook type, so anything written there is visible to every concurrent tool
  call. This caused a real leak: a request to one host carried another host's
  bearer token. Per-call state belongs in the `_call_overrides` ContextVar.
- **Injected cookies must be cleared after the call.** `add_cookies` writes into
  a context crawl4ai caches and reuses; closing the page does not close the
  context. Skip the cleanup only when `session_id` is set, where persistence is
  the point.
- **`CrawlResult.success` means "we got a response", not "HTTP 200."** A real
  404 arrives with `success=True` and the error page as content. Always carry
  `status_code`.
- **`fit_markdown` / `fit_html` are the content-FILTERED forms.** A config with
  no content filter has a nearly empty `fit_html`: `extract_patterns` measured
  3 matches on it versus 83 on `html` for the same page.
- **LLM extraction failures are in-band.** `LLMExtractionStrategy` reports
  success and puts the failure inside `extracted_content` as blocks flagged
  `{"error": true}`. Match the flag, never the word "error", which appears in
  legitimate extracted data.

## Known upstream behaviour we cannot fix here

- **`user_agent` is not reliably per-request.** crawl4ai applies it by mutating
  the shared browser config, and contexts are cached, so the first agent used
  for a context wins for its lifetime. Documented on the parameter.
- **`scope="same-origin"` cannot be stricter than `same-domain`.** crawl4ai has
  no origin concept; its internal/external split compares registrable domains,
  so subdomains are followed and scheme and port are ignored.
- **A deep crawl always uses `MemoryAdaptiveDispatcher`.**
  `DeepCrawlStrategy.arun()` takes no dispatcher and BFS calls `arun_many()`
  without one, so crawl4ai builds its own from `mean_delay`, `max_range` and
  `semaphore_count` on the run config. Those three are the only pacing controls
  that reach a deep crawl — which is why `deep_crawl`'s `delay` sets
  `mean_delay` and not `delay_before_return_html` (a post-fetch pause that does
  nothing for politeness).

## Still unexposed

Capability crawl4ai ships that this server does not surface yet, if someone
wants it: `links` and `tables` on `CrawlResult` (both populated by default and
currently discarded), `crawl_stats` and `redirected_url` for richer failure
diagnostics, `JsonXPathExtractionStrategy`, and `DomainFilter` for allowlisting
extra hosts alongside `scope`.
