"""IL-2 Korea install-layout resolution.

The standalone release nests the game one level down
(<root>/game/data/startup.cfg); the Steam release ("IL2Series") drops that
level (<root>/data/startup.cfg). The auto-setup and device-ordinal lookups
must find startup.cfg under either layout.
"""
import os

from telemffb.utils import il2_korea_game_root


def _mk(tmp_path, *parts):
    p = tmp_path.joinpath(*parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("[KEY = value]")
    return p


def test_standalone_layout_resolves_game_subdir(tmp_path):
    _mk(tmp_path, "game", "data", "startup.cfg")
    assert il2_korea_game_root(str(tmp_path)) == os.path.join(str(tmp_path), "game")


def test_steam_layout_resolves_root(tmp_path):
    _mk(tmp_path, "data", "startup.cfg")
    assert il2_korea_game_root(str(tmp_path)) == str(tmp_path)


def test_standalone_wins_when_both_exist(tmp_path):
    # Pathological both-present case: prefer the historical standalone
    # layout deterministically.
    _mk(tmp_path, "game", "data", "startup.cfg")
    _mk(tmp_path, "data", "startup.cfg")
    assert il2_korea_game_root(str(tmp_path)) == os.path.join(str(tmp_path), "game")


def test_pointing_at_the_game_dir_itself_resolves(tmp_path):
    # A standalone user who browsed one level too deep still resolves.
    _mk(tmp_path, "data", "startup.cfg")
    assert il2_korea_game_root(str(tmp_path)) == str(tmp_path)


def test_missing_install_falls_back_to_standalone_shape(tmp_path):
    # Nothing found: return the historical default so error messages name
    # the expected location.
    assert il2_korea_game_root(str(tmp_path)) == os.path.join(str(tmp_path), "game")


def test_empty_path_does_not_raise():
    assert il2_korea_game_root(None) == os.path.join("", "game")
