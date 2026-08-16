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

import inspect
import logging
from pathlib import Path

import yaml
from crawl4ai import CrawlerRunConfig
from crawl4ai.content_filter_strategy import BM25ContentFilter, PruningContentFilter
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

logger = logging.getLogger(__name__)

# Default location: src/crawl4ai_mcp/profiles/
PROFILES_DIR = Path(__file__).parent / "profiles"


def _valid_config_keys() -> frozenset[str]:
    """Every parameter CrawlerRunConfig actually accepts, read from the class.

    Derived from the live signature rather than a hand-written list. The list
    this replaced named 20 keys against a class that accepts 99, so 77 upstream
    parameters could not be reached from a profile OR a per-call override --
    `excluded_tags`, `exclude_external_links`, `check_robots_txt`,
    `remove_consent_popups`, `target_elements`, `only_text` among them. Setting
    one in a profile did nothing, silently: the stripped-key warning goes to
    stderr, which an MCP client never displays.

    For a server whose whole purpose is exposing crawl4ai, a frozen allowlist
    is the wrong shape -- it can only ever fall further behind upstream. Reading
    the signature cannot drift.
    """
    return frozenset(inspect.signature(CrawlerRunConfig.__init__).parameters) - {
        "self",
        "kwargs",
    }


# Params this module sets itself after the merge; a profile must not override them.
_RESERVED_KEYS: frozenset[str] = frozenset({"verbose", "markdown_generator"})

# Consumed by build_run_config and routed to the content filter, not passed
# through to CrawlerRunConfig.
_FILTER_KEYS: frozenset[str] = frozenset({"word_count_threshold", "query"})


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

    # Strip keys CrawlerRunConfig does not accept, checked against the live
    # signature so this cannot fall behind upstream. See _valid_config_keys.
    unknown = set(merged) - _valid_config_keys() - _FILTER_KEYS
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
    #
    # preserve_tags keeps <pre> and <code> out of the pruner's reach. Without
    # it the filter treats code blocks as low-density noise and either drops
    # them or reassembles them without their whitespace, so a syntax-
    # highlighted docs page (mkdocs-material, Docusaurus and friends wrap every
    # token in its own <span>) turns "uvx pycowsay hello from uv" into
    # "uvxpycowsayhellofromuv". Feeding that to a model is worse than dropping
    # it, because it reads like a real command.
    #
    # Measured across three real docs sites (uv, Pydantic, FastAPI): code lines
    # surviving intact went 1 -> 10 of 24, mangled lines 1 -> 0, and retained
    # content nearly doubled (26.9k -> 48.6k chars). Nothing got smaller.
    # crawl4ai's mark_code and handle_code_in_pre options were measured too and
    # made no difference, so they are deliberately not set.
    #
    # This is an improvement, not a cure: fit_markdown still drops newlines
    # between statements inside a multi-line block. Callers who need verbatim
    # code should scope with css_selector and lower word_count_threshold.
    wct = merged.pop("word_count_threshold", 10)
    query = merged.pop("query", None)

    if query:
        # A query swaps the filter entirely. BM25 scores each block against the
        # query and keeps the relevant ones, so an agent asking "what does this
        # page say about X" gets the answer filtered BEFORE the tokens are
        # spent, instead of pulling the whole page into context to read past
        # the irrelevant parts. Pruning is density-based and cannot do that.
        content_filter = BM25ContentFilter(user_query=query)
    else:
        content_filter = PruningContentFilter(
            threshold=0.48,
            threshold_type="fixed",
            min_word_threshold=wct,
            preserve_tags=["pre", "code"],
        )

    merged["markdown_generator"] = DefaultMarkdownGenerator(
        content_filter=content_filter
    )

    return CrawlerRunConfig(**merged)


def effective_profile_keys(settings: dict) -> tuple[dict, dict]:
    """Split a raw profile dict into (applied, ignored).

    list_profiles used to print the raw YAML, so a key that build_run_config
    strips on the very next line was shown to the agent as an active setting.
    The tool that exists to say what a profile does was reporting settings that
    do not apply.
    """
    valid = _valid_config_keys() | _FILTER_KEYS
    applied = {k: v for k, v in settings.items() if k in valid}
    ignored = {k: v for k, v in settings.items() if k not in valid}
    return applied, ignored
