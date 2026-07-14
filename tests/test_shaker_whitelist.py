"""Lint test: every effect emitted by sim modules is either whitelisted for
the bass shaker or explicitly allow-listed as non-shaker.

Runs without audio hardware. Invoke with::

    python -m tests.test_shaker_whitelist

Or via unittest::

    python -m unittest tests.test_shaker_whitelist

Background: see docs/SHAKER.md §8.3. The shaker backend silently drops every
effect that is not in ``SHAKER_EFFECT_WHITELIST`` (telemffb/hw/ffb_shaker.py).
That is intentional — force-only effects (springs, dampers) and stick-only
effects (stick shaker) have no useful body-vibration mapping. But it also
means a brand-new effect added to ``aircraft_base.py`` upstream is silent
without any error or warning. This test scans the sim modules for
``effects["..."]`` triggers and fails when one is found that is neither
whitelisted nor allow-listed below — forcing a per-effect decision.

To resolve a failure: pick one for the new effect.

  - Renders usefully as audio on a body shaker → add to
    ``SHAKER_EFFECT_WHITELIST`` in ``telemffb/hw/ffb_shaker.py`` (and
    optionally tune via ``shaker_effects_default.json``).
  - Force-only / stick-only / harness placeholder → add to
    ``KNOWN_NON_SHAKER_EFFECTS`` below with a comment explaining why.

Limitations: variable-keyed lookups (``effects[effect_name]``) are not
checked; the literal name list they iterate over must appear elsewhere
in the same file as a literal trigger to be covered.
"""
from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SIM_DIR = REPO_ROOT / "telemffb" / "sim"
SHAKER_MODULE = REPO_ROOT / "telemffb" / "hw" / "ffb_shaker.py"

SOURCE_FILES = (
    SIM_DIR / "aircraft_base.py",
    SIM_DIR / "aircrafts_dcs.py",
    SIM_DIR / "aircrafts_il2.py",
    SIM_DIR / "aircrafts_msfs_xp.py",
)


def _load_whitelist_from_source() -> frozenset[str]:
    """Parse ``SHAKER_EFFECT_WHITELIST`` out of ``ffb_shaker.py`` via AST.

    Avoids importing ``telemffb.hw.ffb_shaker`` at test time — that module
    transitively imports ``sounddevice``, which requires PortAudio system
    libraries and fails on headless CI without audio support. This test is
    static-analysis, not runtime, so the import is unnecessary.
    """
    tree = ast.parse(SHAKER_MODULE.read_text(encoding="utf-8"),
                     filename=str(SHAKER_MODULE))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not (len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "SHAKER_EFFECT_WHITELIST"):
            continue
        if not isinstance(node.value, ast.Set):
            raise AssertionError(
                f"SHAKER_EFFECT_WHITELIST in {SHAKER_MODULE} is not a set "
                f"literal — adapt this loader."
            )
        names: set[str] = set()
        for elt in node.value.elts:
            if not isinstance(elt, ast.Constant) or not isinstance(elt.value, str):
                raise AssertionError(
                    f"SHAKER_EFFECT_WHITELIST contains a non-string element: "
                    f"{ast.dump(elt)}"
                )
            names.add(elt.value)
        return frozenset(names)
    raise AssertionError(
        f"SHAKER_EFFECT_WHITELIST not found in {SHAKER_MODULE}"
    )


SHAKER_EFFECT_WHITELIST = _load_whitelist_from_source()


# Effects emitted by aircraft code that intentionally do NOT render on the
# shaker. Grouped by reason; add new entries to the matching section with a
# one-line comment. If a new effect doesn't fit any existing category, add
# a new ``# --- ... ---`` group above with a short rationale.
KNOWN_NON_SHAKER_EFFECTS: frozenset = frozenset({
    # --- Force-feedback only (springs / dampers / friction / inertia) ---
    # No useful audio mapping. Dropped with a debug log on the shaker
    # backend (see ``HapticEffect`` stubs in ``ffb_shaker.py``).
    "spring", "damper", "friction", "inertia",
    "pedal_spring", "pedal_damper",
    "trim_spring", "fbw_spring", "pause_spring", "dynamic_spring",
    "spring_adjuster", "offset_adjuster",
    "collective_ap_spring", "collective_damper", "collective_ft",
    "cp_ovd_spring", "control_weight",
    "hyd_loss_damper", "hyd_loss_friction", "hyd_loss_inertia",
    "dcs_spr_override", "elev_droop",
    "TR Damper",

    # --- Constant-force loading (G-force, deceleration, turbulence) ---
    # Sustained force vector, not vibration. ``turbulence`` (MSFS/XP) is
    # ``effects['turbulence'].constant(force, dir).start()`` with a fixed
    # direction — pure constant force, no vibratory component on a body
    # shaker. (Compare to whitelisted ``wnd`` which uses
    # ``RandomDirectionModulator`` and is effectively vibration.)
    "decel", "gforce", "new_gforce",
    "turbulence",

    # --- Stick-only effects ---
    # Stick-shaker is a mechanical stall warning on the control column,
    # physically distinct from a body shake. Out of scope for the bass
    # shaker by design (see plan: claude/review-shaker-tasks-P4M65).
    "stick_shaker", "stick_shaker1", "stick_shaker2",

    # --- Documentation / harness placeholders ---
    # Never triggered in production; appear as examples in ac_handler.py
    # comments or as effect-tester samples.
    "myUniqueName",
    "FI_vibration",
})


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

# ``effects["name"]`` and ``effects['name']``.
_LITERAL_KEY_RE = re.compile(r'effects\[\s*([\'"])([^\'"\n]+)\1\s*\]')

# ``effects[f"prefix_{...}suffix"]`` — captures the literal prefix.
_FSTRING_KEY_RE = re.compile(
    r'effects\[\s*f([\'"])([^\'"{}\n]*)\{[^}\n]+\}([^\'"\n]*)\1\s*\]'
)


def _scan_file(path: Path) -> tuple[set[str], set[str]]:
    """Return ``(literal_names, fstring_prefixes)`` found in ``path``."""
    text = path.read_text(encoding="utf-8")
    literals = {m.group(2) for m in _LITERAL_KEY_RE.finditer(text)}
    prefixes = {m.group(2) for m in _FSTRING_KEY_RE.finditer(text)
                if m.group(2)}
    return literals, prefixes


def _discover_effects() -> tuple[set[str], set[str]]:
    all_literals: set[str] = set()
    all_prefixes: set[str] = set()
    for path in SOURCE_FILES:
        if not path.exists():
            continue
        lit, pre = _scan_file(path)
        all_literals |= lit
        all_prefixes |= pre
    return all_literals, all_prefixes


def _prefix_is_covered(prefix: str, recognized: set[str]) -> bool:
    """A dynamic effect prefix is covered if any recognized name shares
    the prefix in either direction (``recognized`` starts with prefix, or
    prefix starts with ``recognized``). Catches both ``cyl_phys_`` ↔
    ``cyl_phys_1`` and ``il2_gunfire_`` ↔ ``il2_gunfire``.
    """
    return any(name.startswith(prefix) or prefix.startswith(name)
               for name in recognized)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class ShakerWhitelistDriftTest(unittest.TestCase):

    def test_literal_effects_are_whitelisted_or_allowlisted(self) -> None:
        literals, _ = _discover_effects()
        recognized = SHAKER_EFFECT_WHITELIST | KNOWN_NON_SHAKER_EFFECTS
        unknown = sorted(literals - recognized)
        if unknown:
            self.fail(
                "New effect names found in sim modules that are neither in "
                "SHAKER_EFFECT_WHITELIST (telemffb/hw/ffb_shaker.py) nor in "
                "KNOWN_NON_SHAKER_EFFECTS (this test):\n"
                "  - " + "\n  - ".join(unknown) + "\n\n"
                "Decide per effect: whitelist it (audio on shaker) or add "
                "it to KNOWN_NON_SHAKER_EFFECTS with a comment."
            )

    def test_fstring_prefixes_are_covered(self) -> None:
        _, prefixes = _discover_effects()
        recognized = SHAKER_EFFECT_WHITELIST | KNOWN_NON_SHAKER_EFFECTS
        unknown = sorted(p for p in prefixes
                         if not _prefix_is_covered(p, recognized))
        if unknown:
            self.fail(
                "Dynamic ``effects[f\"...\"]`` prefixes in sim modules "
                "without a matching SHAKER_EFFECT_WHITELIST or "
                "KNOWN_NON_SHAKER_EFFECTS sibling:\n"
                "  - " + "\n  - ".join(unknown)
            )

    def test_allowlist_does_not_overlap_whitelist(self) -> None:
        overlap = SHAKER_EFFECT_WHITELIST & KNOWN_NON_SHAKER_EFFECTS
        self.assertFalse(
            overlap,
            f"Effects appear in both SHAKER_EFFECT_WHITELIST and "
            f"KNOWN_NON_SHAKER_EFFECTS: {sorted(overlap)}. Pick one — an "
            f"effect either renders on the shaker or it does not."
        )

    def test_at_least_one_effect_was_discovered(self) -> None:
        # Smoke test: if discovery returns empty, the regex or paths broke
        # silently. A real run should always find dozens of effect names.
        literals, _ = _discover_effects()
        self.assertGreater(
            len(literals), 20,
            f"Effect discovery returned only {len(literals)} literal names. "
            f"Regex or SOURCE_FILES paths likely broken."
        )


if __name__ == "__main__":
    unittest.main()
