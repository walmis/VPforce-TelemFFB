"""Schema validation tests for defaults.xml.

Verifies structural integrity, cross-references, and data consistency
of the shipped defaults.xml configuration file.
"""
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def defaults_root():
    """Load defaults.xml once for all tests in this module."""
    defaults_path = str(Path(__file__).parents[1] / "defaults.xml")
    tree = ET.parse(defaults_path)
    return tree.getroot()


# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────

VALID_SIMS = {"DCS", "BMS", "IL2", "MSFS", "XPLANE"}
VALID_DEVICES = {"joystick", "pedals", "collective", "trimwheel", "any"}
VALID_DATATYPES = {
    "group", "list", "enumlist", "bool",
    "float", "d_float", "n_float", "negfloat", "pct_float", "anyfloat",
    "int", "d_int",
    "anylist", "text", "path", "button", "convert",
    "configurator", "advspr", "advgs", "trimcal",
}

# Internal prereq markers used to hide UI elements (may have leading whitespace/underscores)
INTERNAL_PREREQS = {"do_not_show", "do not show"}


def _is_placeholder_default(elem):
    """Return True if elem is an order-only placeholder <defaults> entry."""
    children = list(elem)
    return len(children) == 1 and children[0].tag == "order" and elem.findtext("name") is None


def _is_dummy_setting(elem):
    """Return True if this is a dummy/prereq-anchor setting with no sim/device flags."""
    prereq = elem.findtext("prereq", "").strip()
    # Dummy anchors use do_not_show prereq and have no sim/device boolean flags
    if prereq in INTERNAL_PREREQS:
        has_sim = any(elem.findtext(s) == "true" for s in VALID_SIMS)
        has_dev = any(elem.findtext(d) == "true" for d in VALID_DEVICES)
        if not has_sim and not has_dev:
            return True
    return False


# ─────────────────────────────────────────────────────────────
# Sims & Classes registry
# ─────────────────────────────────────────────────────────────

class TestSimsRegistry:
    def test_all_sims_present(self, defaults_root):
        found = {e.findtext("sim") for e in defaults_root.findall(".//sims")}
        assert found == VALID_SIMS, f"Expected {VALID_SIMS}, got {found}"

    def test_no_duplicate_sims(self, defaults_root):
        sims = [e.findtext("sim") for e in defaults_root.findall(".//sims")]
        assert len(sims) == len(set(sims)), f"Duplicate sims: {sims}"


class TestClassesRegistry:
    def test_each_sim_has_classes(self, defaults_root):
        for sim in VALID_SIMS:
            classes = [
                e.findtext("class_name")
                for e in defaults_root.findall(f'.//classes[sim="{sim}"]')
            ]
            assert classes, f"No classes registered for {sim}"

    def test_class_names_non_empty(self, defaults_root):
        for cls_elem in defaults_root.findall(".//classes"):
            name = cls_elem.findtext("class_name")
            assert name and name.strip(), f"Empty class_name"

    def test_sim_attribute_matches(self, defaults_root):
        for cls_elem in defaults_root.findall(".//classes"):
            sim = cls_elem.findtext("sim")
            assert sim in VALID_SIMS, f"Invalid sim in <classes>: {sim}"


# ─────────────────────────────────────────────────────────────
# <defaults> element validation
# ─────────────────────────────────────────────────────────────

class TestDefaultsRequiredFields:
    def test_real_defaults_have_datatype(self, defaults_root):
        for elem in defaults_root.findall(".//defaults"):
            if _is_placeholder_default(elem) or _is_dummy_setting(elem):
                continue
            dt = elem.findtext("datatype")
            assert dt, f"<defaults> missing <datatype>: name={elem.findtext('name')}"

    def test_real_defaults_have_grouping(self, defaults_root):
        for elem in defaults_root.findall(".//defaults"):
            if _is_placeholder_default(elem) or _is_dummy_setting(elem):
                continue
            g = elem.findtext("grouping")
            assert g, f"<defaults> missing <grouping>: name={elem.findtext('name')}"

    def test_all_defaults_have_order(self, defaults_root):
        for elem in defaults_root.findall(".//defaults"):
            o = elem.findtext("order")
            assert o is not None, "<defaults> missing <order>"
            float(o)  # verifies numeric

    def test_real_defaults_have_name(self, defaults_root):
        for elem in defaults_root.findall(".//defaults"):
            if _is_placeholder_default(elem):
                continue
            n = elem.findtext("name")
            assert n and n.strip(), "<defaults> with empty/missing <name>"

    def test_real_defaults_have_displayname(self, defaults_root):
        for elem in defaults_root.findall(".//defaults"):
            if _is_placeholder_default(elem) or _is_dummy_setting(elem):
                continue
            dn = elem.findtext("displayname")
            assert dn and dn.strip(), f"<defaults> missing <displayname>: name={elem.findtext('name')}"

    def test_placeholder_defaults_exist_only_as_order_anchors(self, defaults_root):
        """Placeholder entries should only contain <order> and nothing else."""
        for elem in defaults_root.findall(".//defaults"):
            if _is_placeholder_default(elem):
                children = list(elem)
                assert len(children) == 1 and children[0].tag == "order", (
                    f"Placeholder <defaults> has unexpected children: {[c.tag for c in children]}"
                )


class TestDefaultsDatatypes:
    def test_valid_datatype_values(self, defaults_root):
        for elem in defaults_root.findall(".//defaults"):
            if _is_placeholder_default(elem) or _is_dummy_setting(elem):
                continue
            dt = elem.findtext("datatype")
            assert dt in VALID_DATATYPES, (
                f"Invalid datatype '{dt}' for setting '{elem.findtext('name')}'"
            )

    def test_bool_values_are_boolean(self, defaults_root):
        for elem in defaults_root.findall(".//defaults"):
            if _is_placeholder_default(elem) or _is_dummy_setting(elem):
                continue
            if elem.findtext("datatype") == "bool":
                val = elem.findtext("value", "").strip().lower()
                assert val in ("true", "false", "0", "1", ""), (
                    f"Bool setting '{elem.findtext('name')}' has non-boolean value: {val}"
                )


def _is_device_embedded_in_name(elem):
    """Return True if the setting name implies its device (e.g. 'pedal_xxx')."""
    name = elem.findtext("name", "").lower()
    return any(name.startswith(d) for d in ("joystick_", "pedal_", "collective_", "trimwheel_"))


def _has_any_flag(elem):
    """Return True if elem has at least one sim or device boolean flag."""
    has_sim = any(elem.findtext(s) is not None for s in VALID_SIMS)
    has_dev = any(elem.findtext(d) is not None for d in {"joystick", "pedals", "collective", "trimwheel"})
    has_any = elem.findtext("any") is not None
    return has_sim or has_dev or has_any


class TestDefaultsSimDeviceFlags:
    def test_real_defaults_have_at_least_one_sim_flag_or_are_global(self, defaults_root):
        for elem in defaults_root.findall(".//defaults"):
            if _is_placeholder_default(elem) or _is_dummy_setting(elem):
                continue
            # Settings with no sim/device flags are global and apply everywhere
            if not _has_any_flag(elem):
                continue
            has_sim = any(elem.findtext(s) == "true" for s in VALID_SIMS)
            assert has_sim, (
                f"<defaults> '{elem.findtext('name')}' has no sim flag set to 'true'"
            )

    def test_real_defaults_have_at_least_one_device_flag_or_are_global(self, defaults_root):
        for elem in defaults_root.findall(".//defaults"):
            if _is_placeholder_default(elem) or _is_dummy_setting(elem):
                continue
            # Settings with no sim/device flags are global and apply everywhere
            if not _has_any_flag(elem):
                continue
            # Some settings embed their device in the name (e.g. pedal_force_trim_enabled)
            if _is_device_embedded_in_name(elem):
                continue
            has_device = any(elem.findtext(d) == "true" for d in VALID_DEVICES)
            assert has_device, (
                f"<defaults> '{elem.findtext('name')}' has no device flag set to 'true'"
            )

    def test_sim_flags_are_boolean_strings(self, defaults_root):
        for elem in defaults_root.findall(".//defaults"):
            if _is_placeholder_default(elem) or _is_dummy_setting(elem):
                continue
            for sim in VALID_SIMS:
                val = elem.findtext(sim)
                if val is not None:
                    assert val.lower() in ("true", "false"), (
                        f"Setting '{elem.findtext('name')}' has invalid {sim} flag: {val}"
                    )

    def test_device_flags_are_boolean_strings(self, defaults_root):
        for elem in defaults_root.findall(".//defaults"):
            if _is_placeholder_default(elem) or _is_dummy_setting(elem):
                continue
            for dev in {"joystick", "pedals", "collective", "trimwheel"}:
                val = elem.findtext(dev)
                if val is not None:
                    assert val.lower() in ("true", "false"), (
                        f"Setting '{elem.findtext('name')}' has invalid {dev} flag: {val}"
                    )


# ─────────────────────────────────────────────────────────────
# <models> element validation
# ─────────────────────────────────────────────────────────────

class TestModelsRequiredFields:
    def test_has_name(self, defaults_root):
        for elem in defaults_root.findall(".//models"):
            n = elem.findtext("name")
            assert n and n.strip(), f"<models> with empty/missing <name>"

    def test_has_model_pattern(self, defaults_root):
        for elem in defaults_root.findall(".//models"):
            m = elem.findtext("model")
            assert m and m.strip(), f"<models> with empty/missing <model>"

    def test_has_value(self, defaults_root):
        for elem in defaults_root.findall(".//models"):
            v = elem.findtext("value")
            assert v is not None, f"<models> missing <value>: name={elem.findtext('name')}"


class TestModelsSimDevice:
    def test_sim_values_valid(self, defaults_root):
        for elem in defaults_root.findall(".//models"):
            sim = elem.findtext("sim")
            if sim:
                assert sim in VALID_SIMS or sim == "any", (
                    f"<models> has invalid sim='{sim}' for pattern '{elem.findtext('model')}'"
                )

    def test_device_values_valid(self, defaults_root):
        for elem in defaults_root.findall(".//models"):
            dev = elem.findtext("device")
            if dev:
                assert dev in VALID_DEVICES, (
                    f"<models> has invalid device='{dev}' for pattern '{elem.findtext('model')}'"
                )


class TestModelsRegexPatterns:
    def test_all_patterns_are_valid_regex(self, defaults_root):
        for elem in defaults_root.findall(".//models"):
            pattern = elem.findtext("model", "")
            try:
                re.compile(pattern)
            except re.error as e:
                pytest.fail(
                    f"Invalid regex in <models><model>: '{pattern}' — {e}"
                )


# ─────────────────────────────────────────────────────────────
# <classdefaults_*> element validation
# ─────────────────────────────────────────────────────────────

def _all_classdefault_tags(root):
    """Return all unique classdefaults_* tags found in the XML."""
    tags = set()
    for elem in root.iter():
        if elem.tag.startswith("classdefaults_"):
            tags.add(elem.tag)
    return sorted(tags)


class TestClassDefaultsRequiredFields:
    def test_has_name(self, defaults_root):
        for tag in _all_classdefault_tags(defaults_root):
            for elem in defaults_root.findall(f".//{tag}"):
                n = elem.findtext("name")
                assert n and n.strip(), f"<{tag}> missing <name>"

    def test_has_type(self, defaults_root):
        for tag in _all_classdefault_tags(defaults_root):
            for elem in defaults_root.findall(f".//{tag}"):
                t = elem.findtext("type")
                assert t and t.strip(), f"<{tag}> missing <type>"

    def test_has_value_or_is_exclusion_or_override(self, defaults_root):
        """Non-exclusion classdefaults must have <value>, unless it's a known override-only setting."""
        # These settings are intentionally value-less placeholders that get overridden per-model
        OVERRIDE_ONLY_SETTINGS = {"vne_override", "aircraft_vne_speed", "aircraft_vs_speed"}
        for tag in _all_classdefault_tags(defaults_root):
            for elem in defaults_root.findall(f".//{tag}"):
                typ = elem.findtext("type", "")
                val = elem.findtext("value")
                name = elem.findtext("name", "")
                if not typ.startswith("!") and name not in OVERRIDE_ONLY_SETTINGS:
                    assert val is not None, (
                        f"<{tag}> name='{name}' type='{typ}' missing <value>"
                    )

    def test_has_sim(self, defaults_root):
        for tag in _all_classdefault_tags(defaults_root):
            for elem in defaults_root.findall(f".//{tag}"):
                s = elem.findtext("sim")
                assert s and s.strip(), f"<{tag}> missing <sim>"

    def test_has_device(self, defaults_root):
        for tag in _all_classdefault_tags(defaults_root):
            for elem in defaults_root.findall(f".//{tag}"):
                d = elem.findtext("device")
                assert d and d.strip(), f"<{tag}> missing <device>"


class TestClassDefaultsConsistency:
    def test_sim_matches_parent_tag(self, defaults_root):
        for tag in _all_classdefault_tags(defaults_root):
            expected_sim = tag.replace("classdefaults_", "")
            for elem in defaults_root.findall(f".//{tag}"):
                sim = elem.findtext("sim", "")
                assert sim == expected_sim, (
                    f"<{tag}> has <sim>{sim}</sim>, expected '{expected_sim}'"
                )

    def test_device_values_valid(self, defaults_root):
        for tag in _all_classdefault_tags(defaults_root):
            for elem in defaults_root.findall(f".//{tag}"):
                dev = elem.findtext("device", "")
                assert dev in VALID_DEVICES, (
                    f"<{tag}> has invalid device='{dev}'"
                )

    def test_type_refs_registered_class_or_negation_or_allsettings(self, defaults_root):
        """Build sim -> {classes} map and verify each classdefault references a valid class."""
        sim_classes: dict[str, set[str]] = {}
        for cls_elem in defaults_root.findall(".//classes"):
            sim = cls_elem.findtext("sim", "")
            cn = cls_elem.findtext("class_name", "")
            sim_classes.setdefault(sim, set()).add(cn)

        for tag in _all_classdefault_tags(defaults_root):
            parent_sim = tag.replace("classdefaults_", "")
            # classdefaults_any can reference classes from *any* sim
            if parent_sim == "any":
                allowed: set[str] = {"AllSettings"}
                for cs in sim_classes.values():
                    allowed |= cs
            else:
                allowed = sim_classes.get(parent_sim, set()) | {"AllSettings"}
            for elem in defaults_root.findall(f".//{tag}"):
                typ = elem.findtext("type", "")
                if typ.startswith("!"):
                    base_type = typ[1:]
                    assert base_type in allowed, (
                        f"<{tag}> exclusion type='!{base_type}' not registered for sim '{parent_sim}'. "
                        f"Allowed: {allowed}"
                    )
                else:
                    assert typ in allowed, (
                        f"<{tag}> type='{typ}' not registered for sim '{parent_sim}'. "
                        f"Allowed: {allowed}"
                    )


# ─────────────────────────────────────────────────────────────
# <validvalues_overrides> validation
# ─────────────────────────────────────────────────────────────

class TestValidvaluesOverrides:
    def test_has_required_fields(self, defaults_root):
        for elem in defaults_root.findall(".//validvalues_overrides"):
            assert elem.findtext("name"), "<validvalues_overrides> missing <name>"
            assert elem.findtext("sim"), "<validvalues_overrides> missing <sim>"
            assert elem.findtext("class"), "<validvalues_overrides> missing <class>"
            assert elem.findtext("validvalues"), "<validvalues_overrides> missing <validvalues>"
            assert elem.findtext("device"), "<validvalues_overrides> missing <device>"

    def test_sim_valid(self, defaults_root):
        for elem in defaults_root.findall(".//validvalues_overrides"):
            sim = elem.findtext("sim", "")
            assert sim in VALID_SIMS, f"Invalid sim in validvalues_overrides: {sim}"

    def test_device_valid(self, defaults_root):
        for elem in defaults_root.findall(".//validvalues_overrides"):
            dev = elem.findtext("device", "")
            assert dev in {"joystick", "pedals", "collective", "trimwheel"}, (
                f"Invalid device in validvalues_overrides: {dev}"
            )


# ─────────────────────────────────────────────────────────────
# <sc_overrides> validation
# ─────────────────────────────────────────────────────────────

class TestScOverrides:
    def test_has_required_fields(self, defaults_root):
        for elem in defaults_root.findall(".//sc_overrides"):
            assert elem.findtext("name"), "<sc_overrides> missing <name>"
            assert elem.findtext("model"), "<sc_overrides> missing <model>"
            assert elem.findtext("var"), "<sc_overrides> missing <var>"

    def test_model_patterns_valid_regex(self, defaults_root):
        for elem in defaults_root.findall(".//sc_overrides"):
            pattern = elem.findtext("model", "")
            try:
                re.compile(pattern)
            except re.error as e:
                pytest.fail(f"Invalid regex in <sc_overrides><model>: '{pattern}' — {e}")


# ─────────────────────────────────────────────────────────────
# Cross-reference integrity
# ─────────────────────────────────────────────────────────────

class TestPrerequisiteReferences:
    def test_prereq_names_exist_in_defaults(self, defaults_root):
        """Every <prereq> should reference an existing <defaults><name> (ignoring dot-notation suffixes)."""
        all_names = {
            e.findtext("name", "")
            for e in defaults_root.findall(".//defaults")
            if not _is_placeholder_default(e)
        }
        for elem in defaults_root.findall(".//defaults"):
            if _is_placeholder_default(elem) or _is_dummy_setting(elem):
                continue
            prereq = elem.findtext("prereq", "").strip()
            if not prereq:
                continue
            # Skip internal/special prereqs
            if prereq in INTERNAL_PREREQS:
                continue
            # Dot-notation prereqs: spring_mode.BASIC.CENTER.CNTR_FT -> root is "spring_mode"
            root_name = prereq.split(".")[0]
            assert root_name in all_names, (
                f"<defaults> '{elem.findtext('name')}' has prereq='{prereq}', "
                f"but root setting '{root_name}' not found in <defaults>"
            )


class TestExclusivityReferences:
    def test_exclusive_with_refs_existing_name(self, defaults_root):
        all_names = {
            e.findtext("name", "")
            for e in defaults_root.findall(".//defaults")
            if not _is_placeholder_default(e)
        }
        for elem in defaults_root.findall(".//defaults"):
            if _is_placeholder_default(elem) or _is_dummy_setting(elem):
                continue
            exc = elem.findtext("exclusive_with", "")
            if exc:
                assert exc in all_names, (
                    f"<defaults> '{elem.findtext('name')}' exclusive_with='{exc}' "
                    f"not found in <defaults>"
                )