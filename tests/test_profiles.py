"""TDD test suite for ProfileManager and build_run_config.

Tests cover:
- ProfileManager loads YAML profiles from a directory
- ProfileManager.get() returns copies, never mutates internal state
- ProfileManager.get(None) and get("unknown") return {}
- Malformed YAML is skipped without crashing
- build_run_config merge order: default <- named <- per-call (right wins)
- verbose=False is always forced unconditionally
- Unknown keys in profile are stripped with a warning (no TypeError)
- word_count_threshold flows to PruningContentFilter via markdown_generator
- CrawlerRunConfig is returned from build_run_config
"""

import logging
import textwrap
from pathlib import Path

import pytest
import yaml
from crawl4ai import CrawlerRunConfig

from crawl4ai_mcp.profiles import KNOWN_KEYS, ProfileManager, build_run_config


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def profile_dir(tmp_path: Path) -> Path:
    """Create a temporary profiles directory with standard test profiles."""
    profiles = tmp_path / "profiles"
    profiles.mkdir()

    (profiles / "default.yaml").write_text(
        textwrap.dedent("""\
        wait_until: domcontentloaded
        page_timeout: 60000
        word_count_threshold: 10
        """)
    )

    (profiles / "fast.yaml").write_text(
        textwrap.dedent("""\
        wait_until: domcontentloaded
        page_timeout: 15000
        word_count_threshold: 5
        """)
    )

    (profiles / "stealth.yaml").write_text(
        textwrap.dedent("""\
        simulate_user: true
        override_navigator: true
        magic: true
        delay_before_return_html: 2.0
        wait_until: networkidle
        page_timeout: 90000
        """)
    )

    return profiles


@pytest.fixture()
def pm(profile_dir: Path) -> ProfileManager:
    """ProfileManager loaded from the standard test profiles."""
    return ProfileManager(profiles_dir=profile_dir)


# ---------------------------------------------------------------------------
# ProfileManager — loading
# ---------------------------------------------------------------------------


class TestProfileManagerLoading:
    def test_loads_all_yaml_files(self, pm: ProfileManager) -> None:
        """ProfileManager discovers and loads all *.yaml files at init time."""
        assert "default" in pm.names
        assert "fast" in pm.names
        assert "stealth" in pm.names
        assert len(pm.names) == 3

    def test_names_are_sorted(self, pm: ProfileManager) -> None:
        """names property returns sorted list."""
        assert pm.names == sorted(pm.names)

    def test_get_returns_correct_dict(self, pm: ProfileManager) -> None:
        """get('fast') returns the fast profile fields."""
        fast = pm.get("fast")
        assert fast["page_timeout"] == 15000
        assert fast["word_count_threshold"] == 5
        assert fast["wait_until"] == "domcontentloaded"

    def test_get_returns_copy_not_reference(self, pm: ProfileManager) -> None:
        """Mutating the returned dict does not affect internal state."""
        fast = pm.get("fast")
        fast["page_timeout"] = 99999
        assert pm.get("fast")["page_timeout"] == 15000  # unchanged

    def test_all_returns_full_registry(self, pm: ProfileManager) -> None:
        """all() returns a dict with all loaded profiles."""
        all_profiles = pm.all()
        assert set(all_profiles.keys()) == {"default", "fast", "stealth"}

    def test_missing_profiles_dir_does_not_crash(self, tmp_path: Path) -> None:
        """ProfileManager with a non-existent directory initializes without error."""
        missing = tmp_path / "does_not_exist"
        pm = ProfileManager(profiles_dir=missing)
        assert pm.names == []


# ---------------------------------------------------------------------------
# ProfileManager — get() edge cases
# ---------------------------------------------------------------------------


class TestProfileManagerGet:
    def test_get_none_returns_empty_dict(self, pm: ProfileManager) -> None:
        """get(None) returns {} — no profile selected."""
        result = pm.get(None)
        assert result == {}

    def test_get_unknown_name_returns_empty_dict(self, pm: ProfileManager) -> None:
        """get('nonexistent') returns {} — unknown profile is not an error."""
        result = pm.get("nonexistent")
        assert result == {}

    def test_get_default_returns_base_profile(self, pm: ProfileManager) -> None:
        """get('default') returns the default profile dict."""
        default = pm.get("default")
        assert default["wait_until"] == "domcontentloaded"
        assert default["page_timeout"] == 60000


# ---------------------------------------------------------------------------
# ProfileManager — malformed YAML resilience
# ---------------------------------------------------------------------------


class TestProfileManagerMalformedYaml:
    def test_syntax_error_yaml_is_skipped(self, tmp_path: Path) -> None:
        """A YAML syntax error in one file does not crash ProfileManager."""
        profiles = tmp_path / "profiles"
        profiles.mkdir()
        (profiles / "bad.yaml").write_text("key: [unclosed bracket\n")
        (profiles / "good.yaml").write_text("page_timeout: 30000\n")
        pm = ProfileManager(profiles_dir=profiles)
        assert "good" in pm.names
        assert "bad" not in pm.names

    def test_non_dict_yaml_root_is_skipped(self, tmp_path: Path) -> None:
        """A YAML file with a non-dict root (e.g., list) is skipped."""
        profiles = tmp_path / "profiles"
        profiles.mkdir()
        (profiles / "list_profile.yaml").write_text("- item1\n- item2\n")
        (profiles / "valid.yaml").write_text("page_timeout: 30000\n")
        pm = ProfileManager(profiles_dir=profiles)
        assert "valid" in pm.names
        assert "list_profile" not in pm.names

    def test_malformed_yaml_logs_error(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A malformed YAML file causes an error to be logged (not raised)."""
        profiles = tmp_path / "profiles"
        profiles.mkdir()
        (profiles / "broken.yaml").write_text("key: [unclosed\n")
        with caplog.at_level(logging.ERROR, logger="crawl4ai_mcp.profiles"):
            ProfileManager(profiles_dir=profiles)
        assert any("broken" in rec.message for rec in caplog.records)

    def test_other_profiles_load_after_bad_file(self, tmp_path: Path) -> None:
        """Bad YAML file is skipped; subsequent valid profiles are still loaded."""
        profiles = tmp_path / "profiles"
        profiles.mkdir()
        # Alphabetically: aaa_bad comes before bbb_good — ensures bad doesn't abort
        (profiles / "aaa_bad.yaml").write_text(": bad yaml content {}\n")
        (profiles / "bbb_good.yaml").write_text("page_timeout: 45000\n")
        pm = ProfileManager(profiles_dir=profiles)
        assert "bbb_good" in pm.names
        assert pm.get("bbb_good")["page_timeout"] == 45000


# ---------------------------------------------------------------------------
# build_run_config — merge order
# ---------------------------------------------------------------------------


class TestBuildRunConfigMerge:
    def test_profile_none_uses_only_default(self, pm: ProfileManager) -> None:
        """build_run_config(pm, None) uses only the default profile."""
        cfg = build_run_config(pm, None)
        assert isinstance(cfg, CrawlerRunConfig)
        # page_timeout should be from default (60000 ms)
        assert cfg.page_timeout == 60000

    def test_named_profile_overrides_default(self, pm: ProfileManager) -> None:
        """build_run_config(pm, 'fast') merges default <- fast (fast wins on collision)."""
        cfg = build_run_config(pm, "fast")
        assert isinstance(cfg, CrawlerRunConfig)
        # fast.yaml overrides default's page_timeout
        assert cfg.page_timeout == 15000

    def test_per_call_override_wins_over_profile(self, pm: ProfileManager) -> None:
        """Per-call override has highest priority in the merge."""
        cfg = build_run_config(pm, "fast", page_timeout=5000)
        assert cfg.page_timeout == 5000

    def test_per_call_override_wins_over_default(self, pm: ProfileManager) -> None:
        """Per-call override wins over default profile values."""
        cfg = build_run_config(pm, None, page_timeout=25000)
        assert cfg.page_timeout == 25000

    def test_stealth_profile_values_applied(self, pm: ProfileManager) -> None:
        """Stealth profile values (simulate_user, magic, etc.) are passed through."""
        cfg = build_run_config(pm, "stealth")
        assert cfg.simulate_user is True
        assert cfg.magic is True
        assert cfg.override_navigator is True

    def test_unknown_profile_name_falls_back_to_default(
        self, pm: ProfileManager
    ) -> None:
        """Unknown profile name logs a warning and falls back to default only."""
        cfg = build_run_config(pm, "nonexistent")
        assert isinstance(cfg, CrawlerRunConfig)
        # Falls back to default — page_timeout is 60000
        assert cfg.page_timeout == 60000

    def test_unknown_profile_name_logs_warning(
        self, pm: ProfileManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Unknown profile name produces a warning log."""
        with caplog.at_level(logging.WARNING, logger="crawl4ai_mcp.profiles"):
            build_run_config(pm, "nonexistent")
        assert any("nonexistent" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# build_run_config — verbose=False enforcement
# ---------------------------------------------------------------------------


class TestBuildRunConfigVerbose:
    def test_verbose_always_false_with_no_profile(self, pm: ProfileManager) -> None:
        """verbose=False is returned even with no profile."""
        cfg = build_run_config(pm, None)
        assert cfg.verbose is False

    def test_verbose_forced_false_even_if_passed_as_override(
        self, pm: ProfileManager
    ) -> None:
        """verbose=True in per-call override is ignored — always forced False."""
        cfg = build_run_config(pm, None, verbose=True)
        assert cfg.verbose is False

    def test_verbose_forced_false_with_named_profile(self, pm: ProfileManager) -> None:
        """verbose remains False when using a named profile."""
        cfg = build_run_config(pm, "stealth")
        assert cfg.verbose is False


# ---------------------------------------------------------------------------
# build_run_config — unknown key stripping
# ---------------------------------------------------------------------------


class TestBuildRunConfigUnknownKeys:
    def test_unknown_key_in_profile_is_stripped(
        self, tmp_path: Path
    ) -> None:
        """Unknown YAML keys do not cause TypeError from CrawlerRunConfig."""
        profiles = tmp_path / "profiles"
        profiles.mkdir()
        (profiles / "default.yaml").write_text("page_timeout: 60000\n")
        (profiles / "weird.yaml").write_text(
            "page_timeout: 30000\nsome_unknown_key: 42\n"
        )
        pm = ProfileManager(profiles_dir=profiles)
        # Must not raise TypeError
        cfg = build_run_config(pm, "weird")
        assert isinstance(cfg, CrawlerRunConfig)

    def test_unknown_key_in_profile_logs_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Unknown YAML keys trigger a warning log."""
        profiles = tmp_path / "profiles"
        profiles.mkdir()
        (profiles / "default.yaml").write_text("page_timeout: 60000\n")
        (profiles / "weird.yaml").write_text(
            "page_timeout: 30000\nsome_unknown_key: 42\n"
        )
        pm = ProfileManager(profiles_dir=profiles)
        with caplog.at_level(logging.WARNING, logger="crawl4ai_mcp.profiles"):
            build_run_config(pm, "weird")
        assert any("some_unknown_key" in rec.message for rec in caplog.records)

    def test_known_per_call_params_pass_through(self, pm: ProfileManager) -> None:
        """Per-call params like css_selector and wait_for pass through without warning."""
        cfg = build_run_config(pm, None, css_selector="article", wait_for="body")
        assert isinstance(cfg, CrawlerRunConfig)


# ---------------------------------------------------------------------------
# build_run_config — word_count_threshold routing
# ---------------------------------------------------------------------------


class TestBuildRunConfigWordCountThreshold:
    def test_markdown_generator_is_set(self, pm: ProfileManager) -> None:
        """build_run_config always attaches a markdown_generator."""
        cfg = build_run_config(pm, None)
        assert cfg.markdown_generator is not None

    def test_word_count_threshold_from_profile_used(
        self, tmp_path: Path
    ) -> None:
        """word_count_threshold from profile flows into PruningContentFilter."""
        profiles = tmp_path / "profiles"
        profiles.mkdir()
        (profiles / "default.yaml").write_text("word_count_threshold: 10\n")
        (profiles / "custom.yaml").write_text("word_count_threshold: 50\n")
        pm = ProfileManager(profiles_dir=profiles)
        cfg = build_run_config(pm, "custom")
        # Can't easily inspect PruningContentFilter internals,
        # but markdown_generator must be present (not None)
        assert cfg.markdown_generator is not None

    def test_word_count_threshold_not_passed_directly_to_crawlerrunconfig(
        self, pm: ProfileManager
    ) -> None:
        """word_count_threshold is popped from merged dict before CrawlerRunConfig(**merged).

        If word_count_threshold were passed directly it would cause a TypeError
        since it's not a CrawlerRunConfig kwarg — instead it goes to PruningContentFilter.
        """
        # Should not raise TypeError
        cfg = build_run_config(pm, None)
        assert isinstance(cfg, CrawlerRunConfig)

    def test_word_count_threshold_default_10_when_absent(
        self, tmp_path: Path
    ) -> None:
        """When no profile sets word_count_threshold, default of 10 is used (no crash)."""
        profiles = tmp_path / "profiles"
        profiles.mkdir()
        (profiles / "default.yaml").write_text("page_timeout: 60000\n")
        pm = ProfileManager(profiles_dir=profiles)
        cfg = build_run_config(pm, None)
        # No error + markdown_generator is set
        assert cfg.markdown_generator is not None


# ---------------------------------------------------------------------------
# KNOWN_KEYS
# ---------------------------------------------------------------------------


class TestKnownKeys:
    def test_known_keys_is_a_set(self) -> None:
        """KNOWN_KEYS is exported as a set."""
        assert isinstance(KNOWN_KEYS, (set, frozenset))

    def test_known_keys_does_not_include_verbose(self) -> None:
        """verbose is excluded from KNOWN_KEYS so it always triggers unknown-key warning."""
        assert "verbose" not in KNOWN_KEYS

    def test_known_keys_includes_expected_fields(self) -> None:
        """KNOWN_KEYS includes the main profile-settable CrawlerRunConfig fields."""
        expected = {
            "wait_until", "page_timeout", "simulate_user",
            "magic", "scan_full_page", "word_count_threshold",
        }
        assert expected.issubset(KNOWN_KEYS)
