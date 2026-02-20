"""Profile system for crawl4ai_mcp.

Provides:
  - ProfileManager: loads *.yaml profile files from a directory at startup.
  - build_run_config: merges default profile <- named profile <- per-call overrides
    and constructs a CrawlerRunConfig instance.

Design constraints:
  - verbose=False is ALWAYS forced after merge — profiles must never be able to
    set verbose=True, which would corrupt the MCP stdio transport.
  - word_count_threshold is NOT passed to CrawlerRunConfig directly; it is popped
    from the merged dict and routed to PruningContentFilter instead.
  - Unknown keys (not in KNOWN_KEYS union per-call-only keys) are stripped with
    a warning log — they never reach CrawlerRunConfig(**merged).
"""

import logging
from pathlib import Path

import yaml
from crawl4ai import CrawlerRunConfig
from crawl4ai.content_filter_strategy import PruningContentFilter
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

logger = logging.getLogger(__name__)

# Default location: src/crawl4ai_mcp/profiles/
PROFILES_DIR = Path(__file__).parent / "profiles"

# Keys that are valid for YAML profile files.
# verbose is intentionally excluded — it is handled unconditionally after merge.
# word_count_threshold is included here but will be popped before CrawlerRunConfig(**).
KNOWN_KEYS: frozenset[str] = frozenset(
    {
        "wait_until",
        "page_timeout",
        "delay_before_return_html",
        "simulate_user",
        "override_navigator",
        "magic",
        "scan_full_page",
        "scroll_delay",
        "remove_overlay_elements",
        "word_count_threshold",
        "cache_mode",
        "mean_delay",
        "max_range",
    }
)

# Per-call-only params: valid CrawlerRunConfig kwargs but not in YAML profiles.
# These are merged in via **per_call_overrides and must not trigger unknown-key warnings.
_PER_CALL_KEYS: frozenset[str] = frozenset(
    {
        "css_selector",
        "excluded_selector",
        "wait_for",
        "js_code",
        "user_agent",
    }
)

# Full set of valid keys for unknown-key detection.
# Anything not in this set will be stripped with a warning.
_ALL_VALID_KEYS: frozenset[str] = KNOWN_KEYS | _PER_CALL_KEYS


class ProfileManager:
    """Loads and manages YAML crawl profiles.

    Each *.yaml file in the profiles directory becomes a named profile.
    The stem of the filename is the profile name (e.g., "fast.yaml" -> "fast").

    Malformed or unreadable files are logged and skipped — ProfileManager.__init__
    never raises.
    """

    def __init__(self, profiles_dir: Path = PROFILES_DIR) -> None:
        self._profiles: dict[str, dict] = {}
        self._load_all(profiles_dir)

    def _load_all(self, profiles_dir: Path) -> None:
        """Load all *.yaml files from profiles_dir into _profiles."""
        if not profiles_dir.exists():
            logger.warning("profiles/ directory not found at %s", profiles_dir)
            return

        for path in sorted(profiles_dir.glob("*.yaml")):
            name = path.stem
            try:
                raw = path.read_text(encoding="utf-8")
                data = yaml.safe_load(raw)
                if not isinstance(data, dict):
                    logger.error(
                        "Profile %s is not a YAML dict (got %s) — skipped",
                        name,
                        type(data).__name__,
                    )
                    continue
                self._profiles[name] = data
                logger.info("Loaded profile: %s", name)
            except Exception as exc:
                logger.error("Failed to load profile %s: %s — skipped", name, exc)

    def get(self, name: str | None) -> dict:
        """Return a copy of the named profile dict.

        Returns {} if name is None or if the name is not found.
        """
        if not name:
            return {}
        return dict(self._profiles.get(name, {}))

    def all(self) -> dict[str, dict]:
        """Return a copy of the full profile registry."""
        return dict(self._profiles)

    @property
    def names(self) -> list[str]:
        """Sorted list of loaded profile names."""
        return sorted(self._profiles.keys())


def build_run_config(
    profile_manager: ProfileManager,
    profile: str | None,
    **per_call_overrides,
) -> CrawlerRunConfig:
    """Build a CrawlerRunConfig by merging profiles and per-call overrides.

    Merge order (right side wins):
        default profile <- named profile <- per_call_overrides

    Guarantees:
    - verbose=False is always forced regardless of profile or override content.
    - word_count_threshold is popped and routed to PruningContentFilter.
    - Unknown keys are stripped with a warning — no TypeError from CrawlerRunConfig.

    Args:
        profile_manager: The ProfileManager instance holding loaded profiles.
        profile: Named profile to use (e.g., "fast", "stealth"), or None for
            default-only.
        **per_call_overrides: Additional kwargs that override profile values.

    Returns:
        A fully configured CrawlerRunConfig instance.
    """
    default = profile_manager.get("default")

    if profile is not None and profile not in profile_manager.names:
        logger.warning(
            "Profile %r not found — falling back to default profile only", profile
        )
        named: dict = {}
    else:
        named = profile_manager.get(profile) if profile else {}

    # Three-layer merge: default <- named <- per-call (right wins)
    merged = {**default, **named, **per_call_overrides}

    # Strip unknown keys (not valid for CrawlerRunConfig)
    unknown = set(merged.keys()) - _ALL_VALID_KEYS - {"verbose"}
    if unknown:
        logger.warning(
            "Stripping unknown profile keys %s — not valid CrawlerRunConfig kwargs",
            sorted(unknown),
        )
        for key in unknown:
            del merged[key]

    # CRITICAL: force verbose=False unconditionally after merge.
    # CrawlerRunConfig defaults verbose=True which causes Rich Console to write
    # to stdout, immediately corrupting the MCP stdio JSON-RPC transport.
    merged["verbose"] = False

    # word_count_threshold goes to PruningContentFilter, not CrawlerRunConfig.
    wct = merged.pop("word_count_threshold", 10)
    merged["markdown_generator"] = DefaultMarkdownGenerator(
        content_filter=PruningContentFilter(
            threshold=0.48,
            threshold_type="fixed",
            min_word_threshold=wct,
        )
    )

    return CrawlerRunConfig(**merged)
