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

- **Credential leakage**: API keys or tokens passed via headers/cookies. Two real leaks were found and fixed in 2.1.0 — injected cookies outlived their call and authenticated later crawls of the same domain, and headers written to crawl4ai's single per-type hook slot crossed between concurrent calls. Per-call data is now scoped to the running task and injected cookies are cleared when the call ends (except within a named session, where persistence is intended). `tests/test_credential_isolation.py` guards both
- **Profile injection**: Malicious YAML profiles could inject unexpected browser config values
- **Network access**: The crawler runs with full network access to whatever URLs are requested
- **LLM extraction**: The `extract_structured` tool passes content to an LLM provider (API key required)
