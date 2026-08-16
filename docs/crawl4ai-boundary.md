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
  the point. Clear by name **and domain and path** — clearing by name alone
  reaches into every context and deletes a live session's identically-named
  cookie, and `session`, `sid` and `auth_token` are exactly what callers reuse.

- **A session is not an isolation boundary, and cannot be made into one here.**
  Playwright stores cookies on the browser CONTEXT. `BrowserManager.get_page`
  caches one context per config signature and hands each crawl a fresh *page*
  inside it, then binds a `session_id` to whichever shared context it landed
  in. The signature is a whitelist — proxy, locale, timezone, geolocation,
  `override_navigator`, `simulate_user`, `magic`, browser version — and nothing
  in it can be varied per session without changing how pages actually render.
  So there is no supported way to give a session its own cookie jar in 0.9.2.

  Consequences, measured against a local echo server: a cookie held by a
  session is sent on other same-domain crawls that pass no cookies and no
  session; two named sessions share one jar; and a concurrent call overlapping
  a cookie-bearing call can see its cookie. Cookies do stay domain-scoped, so
  this is same-domain exposure, not the cross-host leak fixed in 2.1.0.
  Documented on `crawl_url`'s `cookies` parameter and on `create_session`
  rather than worked around, because any workaround here would be a guess about
  crawl4ai internals. Separate identities belong in separate processes.

- **Cookies cannot be injected without a real page load.** The hook fires during
  navigation, and crawl4ai rejects `about:blank` outright ("URL must start with
  http://, https://, file:// or raw:"). `create_session` used to navigate there
  to apply cookies, so the hook never ran and the cookies were dropped while the
  tool reported success. If there is no URL to load, say the cookies were not
  applied; do not pretend.
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

## Projected, not passed through

Three upstream shapes are narrowed on the way out rather than forwarded whole.
Each drop was measured, not assumed.

- **Links** keep `href`, `text` and `title`. crawl4ai attaches seven more per
  link — `base_domain`, `head_data`, the two `head_extraction_*` fields, and
  three scores — all `None` or `0.0` unless link-head extraction or a URL
  scorer ran, and this server enables neither. Measured on one Wikipedia
  article: 317KB of links became 132KB for the same 997 entries. There is
  deliberately **no deduplication** — crawl4ai already returns each href once
  (997 links, 997 distinct hrefs on that page), so deduping would be a lossy
  transform with nothing to gain.
- **Tables** keep `headers`, `rows` and `caption`. The `metadata` block is
  dropped: `row_count` and `column_count` are `len()` of what is already
  returned, and the rest is the table's raw `id` and `class` attributes.
- **Block reasons** in a failure diagnostic are flattened to one line and
  capped. crawl4ai puts raw exception text in them, and a Playwright
  navigation failure carries a full multi-line call log that would bury
  everything around it.

Neither `links` nor `tables` is returned unless asked for, and nothing is
truncated when they are. The size warning lives in the tool docstrings instead,
because a silent cap on a batch crawl is worse than a large result.

## Ordering inside crawl4ai that changes what a parameter means

`deep_crawl`'s `allowed_domains` forces `include_external` on, and that is not
a preference. Reading `BFSDeepCrawlStrategy.link_discovery` and
`can_process_url` in 0.9.2: `include_external` decides which links enter the
candidate set at all, and the filter chain runs *afterwards* on whatever
survived. So under the default same-domain scope an off-domain host named in
`allowed_domains` is already discarded before `DomainFilter` could admit it.
Nothing raises and nothing warns — the parameter is simply inert. Handing the
boundary to the allowlist is the only way it can work, and it is stricter than
the scope it replaces. `blocked_domains` only subtracts, so it needs none of
this.

Two related facts from the same read, both load-bearing for tests:

- **Depth 0 bypasses the filter chain entirely.** An allowlist that omits the
  start URL's host still fetches the start page, then drops every link back
  into its own site. The crawl does *not* stop at one page: links to the
  allowed hosts are still followed. Measured — `docs.astral.sh/uv/` with
  `allowed_domains=["github.com"]` returns 3 pages across both hosts, and 1
  page with the widening removed.
- **External links are appended after internal ones and the frontier is
  truncated to remaining capacity.** On a BFS crawl with a small `max_pages`,
  an external host can therefore never be reached however the allowlist is
  written. `best-first` sorts the frontier by score before truncating, so a
  keyword scorer is what lets an extra host through on a short crawl.

`DomainFilter` matching is domain-level like everything else in crawl4ai:
`_is_subdomain` accepts `domain == parent or domain.endswith("." + parent)`, so
listing `example.com` also allows `api.example.com`. Its `_extract_domain`
regex captures the whole netloc, so a URL with an explicit port does not match
a bare host. Code that needs to predict a filter's verdict should call the
filter's own `apply()` rather than reimplement this, or the two will drift.

## Compensating for upstream defects

Two places where this wrapper deliberately does not pass a value straight
through, because upstream mishandles it. Both were found by driving the real
server against real sites while the unit suite was green.

- **`deep_crawl` asks BFS for `max_pages + 1` and truncates its own results.**
  `BFSDeepCrawlStrategy._arun_stream` increments its page counter and then
  `break`s when it reaches `max_pages` — *before* the `yield`. The last page is
  fetched, counted, and discarded. `deep_crawl` forces `stream=True` so it can
  report progress (a silent tool call gets aborted for idleness), so every BFS
  deep crawl returned one page fewer than asked and `max_pages=1` returned
  nothing at all. Measured against crawl4ai's own strategy:

  | | `max_pages=1` | `3` | `5` |
  | --- | --- | --- | --- |
  | `stream=False` | 1 | 3 | 5 |
  | `stream=True` | 0 | 2 | 4 |

  Asking for one extra *and* truncating locally lands on exactly `max_pages`
  under both states, so this stays correct if upstream is ever fixed. Remove
  both halves together, never just one. `best-first` is unaffected and is not
  padded.

- **Sitemap decompression is decided by the bytes, never the URL.** See
  `_maybe_gunzip`. httpx transparently decodes `Content-Encoding: gzip`, so the
  URL's `.gz` suffix says nothing about what actually arrived, and redirects
  break the correspondence in both directions.

## Defaults that are deliberately not crawl4ai's

- **`cache_mode` defaults to `bypass`, not `enabled`.** crawl4ai's cache stores
  the raw page and does not preserve `fit_markdown`, so a cache hit silently
  discards every content control this server applies — and this server always
  configures a content filter, so essentially every cache hit was wrong.
  Measured on one docs page with a BM25 query: 1,848 characters fresh versus
  21,767 from cache, the latter carrying `status_code=None`. Caching is still
  reachable by name; it is simply not a safe default here.

- **Optional crawl parameters default to `None`, not to their documented
  values.** `cache_mode`, `page_timeout` and `word_count_threshold` are only
  written into the run config when the caller actually passes them. A concrete
  default is indistinguishable from silence at the call site, and writing it
  unconditionally meant a profile's own values could never win — which quietly
  broke the documented merge order `default ← profile ← per-call`. Any new
  parameter that a profile may also set must follow this pattern.

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

Capability crawl4ai ships that this server does not surface, and why.

- **`screenshot`, `pdf`, `mhtml`, `network_requests`, `console_messages`.**
  Opt-in upstream too — `screenshot`, `pdf`, `capture_mhtml`,
  `capture_network_requests` and `capture_console_messages` all default
  `False` on `CrawlerRunConfig` — and each is either binary or very large. An
  MCP result is JSON on a stdio pipe; base64 page images do not belong in one.
- **`media`.** Populated by default alongside `links` and `tables`, and left
  out with them exposed. Images, audio and video carry `src`, `alt`, `score`
  and dimensions, which serves image harvesting rather than the "read this page
  / map this site / pull this table" work these tools exist for. Cheap to add
  the day someone wants it, following the `links` projection exactly.
- **Proxy rotation and `max_retries`.** crawl4ai's anti-bot retry loop is what
  fills the `crawl_stats` this server now reports, but its inputs are not
  exposed as parameters. `max_retries` defaults to 0, so a block is normally
  one attempt.

`crawl_url` deliberately does not take `include_links` or `include_tables`: it
returns a plain markdown string, and a model return type is what the SDK needs
to derive an `outputSchema`. Wrapping one page's markdown in JSON to carry its
links would bury the content in escaping. Use `crawl_many` with a single URL.
