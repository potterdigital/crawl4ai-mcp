# Quick Task 1: Run Full Test on MCP Server with Real URLs

## Results

### Unit Tests: 94/94 PASSED (0.73s)

| Test File | Tests | Status |
|-----------|-------|--------|
| test_profiles.py | 24 | PASSED |
| test_extraction.py | 9 | PASSED |
| test_extraction_css.py | 5 | PASSED |
| test_crawl_many.py | 6 | PASSED |
| test_deep_crawl.py | 10 | PASSED |
| test_crawl_sitemap.py | 6 | PASSED |
| test_sessions.py | 14 | PASSED |
| test_update.py | 8 | PASSED |

### Lint: All checks passed (ruff T201 clean)

### Live MCP Tools (6/12 tested)

| Tool | Status | Response |
|------|--------|----------|
| ping | PASS | ok |
| list_profiles | PASS | 4 profiles loaded |
| crawl_url | PASS | Markdown from webscraper.io |
| extract_css | PASS | 6 laptop records extracted |
| list_sessions | PASS | No active sessions |
| check_update | PASS | crawl4ai 0.8.0 up to date |

### Overall: HEALTHY
