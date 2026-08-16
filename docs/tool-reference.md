# Tool reference

Detail for the crawl and extraction tools: what they return, the parameters worth knowing about, and the upstream behaviour that shapes them. `README.md` covers installation and getting started.

Every tool's own docstring is the authoritative, always-current version of this; your MCP client shows it at call time.

## Result shapes

`crawl_many`, `crawl_sitemap`, and `deep_crawl` return structured results with a
declared output schema, so you get real data in `structuredContent` rather than
prose to parse:

```jsonc
{
  "crawled": 2,          // pages that succeeded
  "total": 3,            // pages attempted
  "pages": [
    { "url": "https://example.com/a", "success": true, "markdown": "# Title\n\n..." },
    { "url": "https://example.com/b", "success": true, "markdown": "...", "depth": 1,
      "parent_url": "https://example.com/a" },        // depth/parent: deep_crawl only
    { "url": "https://example.com/c", "success": false, "error": "Connection timeout" }
  ],
  "output_dir": null,    // set when you passed output_dir
  "manifest": null,      // path to manifest.json, same condition
  "note": null           // e.g. that a sitemap was truncated at max_urls
}
```

Successes sort ahead of failures, and a failing URL never discards the pages that
worked. With `output_dir` set, each page carries `file` instead of `markdown` —
the content is on disk. That is the better mode for large content-heavy crawls,
since inline markdown comes back JSON-escaped.

`extract_css` returns `{ "url", "count", "items": [...], "error" }` with `items`
already parsed, not as a JSON string you decode twice.

If the whole operation fails before any page is attempted (an unreachable or
non-XML sitemap, say), you get `crawled: 0` and an `error` explaining why,
rather than an exception.

Note that `success` means the page was **retrieved**, not that the server was
happy: crawl4ai reports success for anything it managed to fetch, so an HTTP
404 arrives with `success: true` and the error page's body. Check `status_code`
before treating content as real.

`crawl_url` is unchanged and still returns plain markdown. A single page has no
tabular structure worth exposing, and wrapping it would only bury the content in
escaping.

## Links and tables

`crawl_many`, `crawl_sitemap`, and `deep_crawl` take `include_links` and
`include_tables`. crawl4ai collects both on every crawl; they are returned only
when asked for, because they routinely outweigh the page content — one Wikipedia
article carries 997 internal links, about 130KB of JSON.

```jsonc
{
  "url": "https://en.wikipedia.org/wiki/...",
  "success": true,
  "markdown": "...",
  "links": {
    "internal": [{ "href": "...", "text": "Jump to content", "title": null }],
    "external": [{ "href": "...", "text": "Donate", "title": null }]
  },
  "tables": [
    { "headers": ["Country", "Population"],
      "rows": [["India", "1,428,627,663"]],
      "caption": "List of countries by population" }
  ]
}
```

Only tables crawl4ai scored as real tabular data are included, so page-layout
tables are already filtered out. Nothing is truncated when the flags are on, and
requested links and tables still come back inline when `output_dir` is set —
only markdown goes to disk.

## Caching is off by default

crawl4ai's cache stores the raw page and does not preserve the filtered
markdown, so reading from it discards the content controls these tools exist to
apply. Measured on one docs page with a `query`: a fresh crawl returned 1,848
characters of the relevant sections; the same crawl served from cache returned
21,767 characters of the whole page, labelled `status_code: null`.

So `cache_mode` defaults to `bypass` — every crawl fetches fresh and you always
get filtered content and a real HTTP status. Pass `cache_mode="enabled"` if you
want the speed and can live with unfiltered results on repeat crawls.

## Sessions are not a security boundary

Playwright stores cookies on the browser *context*, and crawl4ai keeps one
shared context per browser configuration. crawl4ai 0.9.2 offers no per-call or
per-session cookie storage, so:

- Cookies passed **without** `session_id` are cleared when the call ends. A
  crawl running concurrently can still see them.
- Cookies passed **with** `session_id` are deliberately kept, and stay in the
  shared jar for the life of the session (30-minute idle TTL). They are sent on
  other crawls of the same domain, including crawls that pass no cookies and no
  session at all.
- Two named sessions share that jar, so a credential in one is sent on the
  other's requests to that domain.

Cookies stay scoped to their own domain, so this is same-domain exposure rather
than one host's credential reaching another host. `destroy_session` clears them
immediately. **If you need two identities against one site kept genuinely
apart, run them in separate server processes, not separate sessions.**

## When a crawl fails

Errors carry the diagnostics crawl4ai already collected, not just a status code.
crawl4ai retries behind its own anti-bot detection, so "blocked" can mean one
refusal or several across a proxy and an HTTP fallback:

```
Crawl failed
URL: https://httpbin.org/status/429
HTTP status: 429
Error: Blocked by anti-bot protection: HTTP 429 Too Many Requests
Attempts: 1, 1 blocked
Blocked by: HTTP 429 Too Many Requests
```

You also get `Redirected to:` when the crawl ended up somewhere other than where
you pointed it (a login wall is the usual reason), and `Retry-After:` when the
server sent one. These lines are omitted when there is nothing non-obvious to
report, so an ordinary timeout stays a one-line error. The same detail appears in
each failed page's `error` in a batch crawl.

## Extracting with XPath

`extract_css` takes `selector_type="xpath"` and the identical schema shape, so
you can reach what CSS cannot — matching on text content, or walking to a parent
or preceding sibling:

```
extract_css(url="https://www.python.org/about/",
            selector_type="xpath",
            schema={"name": "PyLinks",
                    "baseSelector": "//a[contains(text(), 'Python')]",
                    "fields": [{"name": "text", "selector": ".", "type": "text"}]})
```

Prefer CSS otherwise; it is shorter and more people can read it. With
`selector_type="xpath"`, write relative field selectors as `./` or `.//`. A
leading `//` does not escape to the whole document: crawl4ai evaluates field
selectors context-sensitively and silently re-roots `//foo` to `.//foo`, so
`//title` inside an item matches nothing rather than the page title.

## Crawling more than one host

`deep_crawl`'s `scope` cannot express "this docs site *and* its separate API
host". `allowed_domains` can:

```
deep_crawl(url="https://docs.example.com",
           allowed_domains=["docs.example.com", "api.example.com"])
```

Setting `allowed_domains` **replaces** `scope` as the boundary of the crawl, and
is stricter than it: nothing outside the listed hosts is followed. That is
forced by crawl4ai's ordering — it discards off-domain links before any filter
runs, so an allowlist can only work once external links are let through.

List every host you want crawled, **including the start URL's own**. Leaving it
out is legal and does something surprising: the start page is still fetched, but
every link back into its own site is dropped and only the listed hosts are
traversed. The result carries a `note` when that happens.

`blocked_domains` only subtracts, so it composes with any `scope` without
widening anything. Subdomains of a listed domain are included in both lists,
matching crawl4ai's rule everywhere else.

## Filtering a page to what you asked for

Every crawl tool takes an optional `query`. It swaps the default density-based
filter for BM25 relevance scoring, so the page comes back reduced to the parts
matching your question — before the tokens are spent, with no LLM involved:

```
crawl_url(url="https://docs.astral.sh/uv/concepts/cache/",
          query="how do I clear or prune the cache")
```

On that page this returns roughly 23% of the full text, and it is the right 23%.

`deep_crawl` additionally takes `strategy="best-first"` with
`relevance_keywords=[...]`, which spends the `max_pages` budget on the pages
most likely to matter rather than whatever is shallowest.

## Progress on long crawls

Long-running tools (`crawl_many`, `crawl_sitemap`, `deep_crawl`, `repair_browser`)
report progress while they work. Clients abort a tool call that goes silent for too
long — Claude Code's default is 30 minutes on stdio — so a large crawl that emitted
nothing until it finished could be killed mid-flight. `deep_crawl` reports each page
as it completes; the others heartbeat every 15 seconds.
