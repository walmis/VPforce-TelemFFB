"""Per-instance settings panels: one device's settings, written under that
device's own registry keys.

This is the plumbing that lets the master configure every instance, so the
child instances can stop carrying a settings page of their own.  What has to
hold: a panel reads and writes only its own role's keys, two panels for
different roles never bleed into each other, and every setting the old
per-instance dialog owned survives the round trip.
"""
import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from telemffb.InstanceSettingsPanel import (
    ALL_FIELDS, STARTUP_FIELDS, SYSTEM_FIELDS, InstanceSettingsPanel,
)

pytestmark = [pytest.mark.unit]


class FakeSettings:
    """Stands in for SystemSettings: role-scoped reads, flat storage."""

    def __init__(self, stored=None):
        self.stored = dict(stored or {})

    def get(self, name, default=None, instance=None):
        return self.stored.get(f"{instance}/{name}", default)

    def setValue(self, key, value):
        self.stored[key] = value


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _panel(app, role="joystick", fields=None):
    return InstanceSettingsPanel(role, fields or ALL_FIELDS)


class TestRoundTrip:
    def test_writes_only_its_own_roles_keys(self, app):
        panel = _panel(app, "pedals")
        settings = FakeSettings()
        panel.save(settings)
        assert settings.stored, "nothing was written"
        assert all(k.startswith("pedals/") for k in settings.stored), settings.stored

    def test_every_instance_setting_round_trips(self, app):
        settings = FakeSettings({
            "collective/logLevel": "DEBUG",
            "collective/telemTimeout": "350",
            "collective/saveWindow": False,
            "collective/saveLastTab": True,
            "collective/enableVPConfStartup": True,
            "collective/pathVPConfStartup": r"C:\profiles\start.vpconf",
            "collective/enableVPConfGlobalDefault": True,
            "collective/enableVPConfExit": True,
            "collective/pathVPConfExit": r"C:\profiles\exit.vpconf",
            "collective/enableResetGainsExit": True,
        })
        panel = _panel(app, "collective")
        panel.load(settings)

        out = FakeSettings()
        panel.save(out)
        for key, expected in settings.stored.items():
            assert out.stored[key] == expected, key

    def test_panels_for_different_roles_do_not_interfere(self, app):
        settings = FakeSettings({
            "joystick/logLevel": "DEBUG",
            "pedals/logLevel": "INFO",
        })
        joystick = _panel(app, "joystick", SYSTEM_FIELDS)
        pedals = _panel(app, "pedals", SYSTEM_FIELDS)
        joystick.load(settings)
        pedals.load(settings)

        assert joystick.widgets["logLevel"].currentText() == "DEBUG"
        assert pedals.widgets["logLevel"].currentText() == "INFO"

        pedals.widgets["logLevel"].setCurrentText("DEBUG")
        pedals.save(settings)
        assert settings.stored["pedals/logLevel"] == "DEBUG"
        assert joystick.widgets["logLevel"].currentText() == "DEBUG"  # untouched

    def test_unset_settings_fall_back_to_defaults(self, app):
        panel = _panel(app, "trimwheel")
        panel.load(FakeSettings())          # nothing stored for this role
        values = panel.values()
        assert values["logLevel"] == "INFO"
        assert values["telemTimeout"] == "200"
        assert values["saveWindow"] is True
        assert values["enableVPConfStartup"] is False

    def test_load_defaults_ignores_stored_values(self, app):
        settings = FakeSettings({"joystick/logLevel": "DEBUG",
                                 "joystick/saveWindow": False})
        panel = _panel(app, "joystick")
        panel.load(settings, defaults_only=True)
        assert panel.values()["logLevel"] == "INFO"
        assert panel.values()["saveWindow"] is True


class TestFieldCoverage:
    def test_covers_exactly_the_per_instance_settings(self):
        """These are the settings the child dialog owned; if one is added or
        dropped, this list and utils.default_inst must move together."""
        from telemffb.utils import SystemSettings
        keys = set()
        for f in ALL_FIELDS:
            keys.add(f.key)
            if f.path_key:
                keys.add(f.path_key)
        # ignoreUpdate becomes global (only the master checks for updates);
        # the teleplot pair is per-instance but belongs to the Teleplot Setup
        # dialog on the debug menu, not to System Settings.
        elsewhere = {"ignoreUpdate", "teleplotPort", "teleplotVars"}
        expected = set(SystemSettings.default_inst) - elsewhere
        assert keys == expected, f"missing={expected - keys} extra={keys - expected}"

    def test_system_and_startup_fields_are_disjoint(self):
        assert not ({f.key for f in SYSTEM_FIELDS}
                    & {f.key for f in STARTUP_FIELDS})


class TestDependentFields:
    def test_path_row_disabled_until_its_toggle_is_on(self, app):
        panel = _panel(app, "joystick", STARTUP_FIELDS)
        panel.load(FakeSettings())
        assert not panel.widgets["pathVPConfStartup"].isEnabled()

        panel.widgets["enableVPConfStartup"].setChecked(True)
        assert panel.widgets["pathVPConfStartup"].isEnabled()
        assert panel.widgets["pathVPConfStartup__browse"].isEnabled()

    def test_global_default_follows_the_startup_profile(self, app):
        """"Make Startup Profile Global Default" is meaningless without a
        startup profile, and must not stay set when one is switched off."""
        panel = _panel(app, "joystick", STARTUP_FIELDS)
        panel.load(FakeSettings({
            "joystick/enableVPConfStartup": True,
            "joystick/enableVPConfGlobalDefault": True,
        }))
        assert panel.widgets["enableVPConfGlobalDefault"].isEnabled()

        panel.widgets["enableVPConfStartup"].setChecked(False)
        assert not panel.widgets["enableVPConfGlobalDefault"].isEnabled()
        assert not panel.widgets["enableVPConfGlobalDefault"].isChecked()


class TestInlineFields:
    """Settings that qualify another one sit beside it, not under it."""

    def _row_of(self, panel, key):
        grid = panel.layout()
        for i in range(grid.count()):
            if grid.itemAt(i).widget() is panel.widgets[key]:
                return grid.getItemPosition(i)[0]
        raise AssertionError(f"{key} is not in the layout")

    def test_restore_last_tab_shares_a_row_with_restore_window(self, app):
        panel = _panel(app, "joystick", STARTUP_FIELDS)
        assert (self._row_of(panel, "saveLastTab")
                == self._row_of(panel, "saveWindow"))

    def test_global_default_shares_a_row_with_the_startup_profile(self, app):
        panel = _panel(app, "joystick", STARTUP_FIELDS)
        assert (self._row_of(panel, "enableVPConfGlobalDefault")
                == self._row_of(panel, "enableVPConfStartup"))

    def test_an_inline_field_still_round_trips(self, app):
        settings = FakeSettings({"joystick/saveWindow": False,
                                 "joystick/saveLastTab": False})
        panel = _panel(app, "joystick", STARTUP_FIELDS)
        panel.load(settings)
        assert panel.values()["saveLastTab"] is False
        panel.widgets["saveLastTab"].setChecked(True)
        panel.save(settings)
        assert settings.stored["joystick/saveLastTab"] is True

    def test_inline_fields_do_not_overlap_their_neighbor(self, app):
        """Sharing a row is only safe if the columns differ."""
        panel = _panel(app, "joystick", STARTUP_FIELDS)
        grid = panel.layout()
        occupied = set()
        for i in range(grid.count()):
            r, c, rs, cs = grid.getItemPosition(i)
            for col in range(c, c + cs):
                assert (r, col) not in occupied, f"two widgets at row {r}, col {col}"
                occupied.add((r, col))


class TestPathDisplay:
    """A profile is identified by its file name; the path is what is stored."""

    def _field(self, panel):
        return panel.widgets["pathVPConfStartup"]

    def test_shows_the_file_name_and_hovers_the_path(self, app):
        path = r"C:\Users\me\Documents\VPforce Configurator\MyConfigs\heli.vpconf"
        panel = _panel(app, "joystick", STARTUP_FIELDS)
        panel.load(FakeSettings({"joystick/enableVPConfStartup": True,
                                 "joystick/pathVPConfStartup": path}))
        field = self._field(panel)
        assert field.text() == "heli.vpconf"
        assert field.toolTip() == path

    def test_the_full_path_is_what_gets_saved(self, app):
        path = r"C:\profiles\heli.vpconf"
        panel = _panel(app, "joystick", STARTUP_FIELDS)
        panel.load(FakeSettings({"joystick/pathVPConfStartup": path}))
        out = FakeSettings()
        panel.save(out)
        assert out.stored["joystick/pathVPConfStartup"] == path

    def test_a_typed_path_is_taken_as_typed(self, app):
        """Shortening the display must not cost the ability to paste a path."""
        panel = _panel(app, "joystick", STARTUP_FIELDS)
        panel.load(FakeSettings())
        field = self._field(panel)
        field.setText(r"D:\other\exit.vpconf")
        field.textEdited.emit(field.text())
        assert field.path() == r"D:\other\exit.vpconf"

    def test_an_unset_profile_is_blank(self, app):
        panel = _panel(app, "joystick", STARTUP_FIELDS)
        panel.load(FakeSettings())
        field = self._field(panel)
        assert field.text() == ""
        assert field.path() == ""
