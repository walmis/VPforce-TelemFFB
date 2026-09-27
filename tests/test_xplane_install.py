"""X-Plane installs and TelemFFB's two plugins in them: which installs are
known, what state each plugin is in, and what startup offers."""
import json
import os

import pytest

from telemffb.tap import xplane_install as xi

pytestmark = [pytest.mark.unit, pytest.mark.xplane]


def make_install(root, **plugins):
    """An X-Plane folder, with ``plugins`` mapping a plugin to its contents."""
    os.makedirs(os.path.join(root, "Resources", "plugins"), exist_ok=True)
    for plugin, data in plugins.items():
        target = os.path.join(root, "Resources", "plugins", plugin, "64", "win.xpl")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as f:
            f.write(data)
    return str(root)


@pytest.fixture
def bundled(tmp_path, monkeypatch):
    """This TelemFFB's copies of both plugins."""
    copies = {}
    for plugin in xi.PLUGINS:
        path = tmp_path / "bundled" / plugin / "win.xpl"
        path.parent.mkdir(parents=True)
        path.write_bytes(f"{plugin} new".encode())
        copies[plugin] = str(path)
    monkeypatch.setattr(xi, "bundled_plugin", lambda plugin: copies.get(plugin))
    return {plugin: f"{plugin} new".encode() for plugin in xi.PLUGINS}


class TestWhichInstalls:
    def test_x_planes_own_records_are_read_skipping_folders_gone(self, tmp_path, monkeypatch):
        xp11 = make_install(tmp_path / "X-Plane 11")
        xp12 = make_install(tmp_path / "X-Plane 12")
        (tmp_path / "x-plane_install_11.txt").write_text(f"{xp11}/\n")
        (tmp_path / "x-plane_install_12.txt").write_text(f"{tmp_path / 'gone'}/\n{xp12}/\n{xp12}\\\n")
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        assert xi.detected_installs() == [os.path.normpath(xp11), os.path.normpath(xp12)]

    def test_the_old_single_folder_is_the_first_added_one(self):
        assert xi.added_installs({"pathXPLANE": r"D:\XP12"}) == [os.path.normpath(r"D:\XP12")]

    def test_once_the_list_is_saved_the_old_folder_is_not_brought_back(self):
        settings = {"pathXPLANE": r"D:\XP12", "xplaneInstalls": json.dumps([])}
        assert xi.added_installs(settings) == []


class TestPluginState:
    def test_absent_current_and_outdated(self, tmp_path, bundled):
        root = make_install(tmp_path / "xp", **{xi.TELEMETRY: bundled[xi.TELEMETRY]})
        assert xi.plugin_state(root, xi.TELEMETRY) == xi.CURRENT
        assert xi.plugin_state(root, xi.PANEL) == xi.ABSENT
        make_install(tmp_path / "xp", **{xi.TELEMETRY: b"old"})
        assert xi.plugin_state(root, xi.TELEMETRY) == xi.OUTDATED

    def test_installing_puts_this_telemffbs_copy_in_place(self, tmp_path, bundled):
        root = make_install(tmp_path / "xp")
        xi.install_plugin(root, xi.PANEL)
        assert xi.plugin_state(root, xi.PANEL) == xi.CURRENT


class TestStartupOffers:
    def test_only_what_is_installed_and_out_of_date(self, tmp_path, bundled):
        stale = make_install(tmp_path / "a", **{xi.TELEMETRY: b"old", xi.PANEL: b"old"})
        current = make_install(tmp_path / "b", **{xi.TELEMETRY: bundled[xi.TELEMETRY]})
        offers = xi.startup_offers([stale, current], telemetry=True, panel=True)
        assert {(o.root, o.plugin, o.missing) for o in offers} == {
            (stale, xi.TELEMETRY, False), (stale, xi.PANEL, False)}

    def test_a_plugin_nobody_looks_after_is_not_offered(self, tmp_path, bundled):
        stale = make_install(tmp_path / "a", **{xi.TELEMETRY: b"old", xi.PANEL: b"old"})
        offers = xi.startup_offers([stale], telemetry=False, panel=True)
        assert [o.plugin for o in offers] == [xi.PANEL]

    def test_with_no_telemetry_plugin_anywhere_every_install_is_offered_it(self, tmp_path, bundled):
        a = make_install(tmp_path / "a")
        b = make_install(tmp_path / "b")
        offers = xi.startup_offers([a, b], telemetry=True, panel=True)
        assert {(o.root, o.plugin, o.missing) for o in offers} == {
            (a, xi.TELEMETRY, True), (b, xi.TELEMETRY, True)}

    def test_once_one_install_has_it_the_others_are_left_alone(self, tmp_path, bundled):
        has = make_install(tmp_path / "a", **{xi.TELEMETRY: bundled[xi.TELEMETRY]})
        without = make_install(tmp_path / "b")
        assert xi.startup_offers([has, without], telemetry=True, panel=True) == []
