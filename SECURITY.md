# Security Policy

## Supported Versions

Only the latest release is actively maintained.

## Reporting a Vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Email: brian@potterdigital.com

Include:

- Description of the vulnerability
- Steps to reproduce
- Potential impact

You will receive a response within 7 days. If confirmed, a fix will be released as soon as possible and you will be credited in the release notes (unless you prefer to remain anonymous).

## Scope

This is a local stdio MCP server — it does not listen on network ports or run as a service. The primary security surfaces are:

- **Credential leakage**: API keys or tokens passed via headers/cookies. Two real leaks were found and fixed in 2.1.0 — injected cookies outlived their call and authenticated later crawls of the same domain, and headers written to crawl4ai's single per-type hook slot crossed between concurrent calls. Per-call data is now scoped to the running task and injected cookies are cleared when the call ends. `tests/test_credential_isolation.py` guards both.

  **Known limitation, cookies are not fully isolated.** Playwright stores cookies on the browser _context_, and crawl4ai keeps one shared context per browser configuration with no per-call or per-session cookie storage available in 0.9.2. Verified against a local echo server in 2.4.0:

  - a cookie passed with `session_id` stays in the shared jar for the life of the session (30-minute idle TTL) and **is sent on other crawls of the same domain**, including crawls passing no cookies and no session;
  - **two named sessions share that jar**, so a credential in one is sent on the other's requests to that domain;
  - a call running _concurrently_ with a cookie-bearing call can see its cookie, because cleanup cannot run until the owning call ends.

  Cookies do stay scoped to their own domain, so this is same-domain exposure rather than one host's credential reaching another host — narrower than the 2.1.0 leaks. `destroy_session` clears a session's cookies immediately. **Treat a session as a convenience, not a security boundary: to keep two identities against one site genuinely apart, run them in separate server processes.** This is documented on `crawl_url`'s `cookies` parameter, on `create_session`, and in `docs/crawl4ai-boundary.md`, which records why it is not fixable within this wrapper.
- **Profile injection**: Malicious YAML profiles could inject unexpected browser config values
- **Network access**: The crawler runs with full network access to whatever URLs are requested
- **LLM extraction**: The `extract_structured` tool passes content to an LLM provider (API key required)
