"""Variables added to the SimConnect subscription at runtime.

Override rows, a custom force trim switch, the controls lock and the FFB API
variables are all added after the manager starts, each by a caller that only
knows its own.  Any of them can ask for a rebuild at any time, so what one
caller added has to survive a rebuild another caller asked for.
"""
import pytest

from telemffb.telem.SimConnectManager import (RUNTIME_SOURCE_OVERRIDE, RUNTIME_SOURCE_SETTING, SimConnectManager, SimVar,
                                              SimVarArray)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning"),
]


class FakeSDK:
    def __getattr__(self, name):
        return lambda *args, **kwargs: 0


def make_manager():
    m = SimConnectManager()
    m.sc = FakeSDK()
    m.sim_vars = [SimVar("T", "ABSOLUTE TIME", "Seconds"),
                  SimVar("ForceTrimSW", "L:TelemFFBHeliFT", "bool"),
                  SimVarArray("PropRPM", "PROP RPM", "rpm", min=1, max=2)]
    return m


def rebuild(m):
    m._subscribe()
    return dict(m.sv_dict)


def test_a_variable_survives_a_rebuild_another_caller_asked_for():
    m = make_manager()
    m.add_simvar("hpgAfcsMaster", "L:EFB_AFCS_MASTER", "number", source=RUNTIME_SOURCE_OVERRIDE)
    assert rebuild(m)["hpgAfcsMaster"] == "L:EFB_AFCS_MASTER"

    m.add_simvar("ControlsLock", "L:ControlLock", "enum")
    subscribed = rebuild(m)
    assert subscribed["ControlsLock"] == "L:ControlLock"
    assert subscribed["hpgAfcsMaster"] == "L:EFB_AFCS_MASTER"

    assert rebuild(m) == subscribed


def test_a_later_add_of_the_same_name_replaces_the_earlier_one():
    m = make_manager()
    m.add_simvar("ForceTrimSW", "L:FIRST", "enum")
    m.add_simvar("ForceTrimSW", "L:SECOND", "enum")
    assert rebuild(m)["ForceTrimSW"] == "L:SECOND"


def test_removing_a_variable_hands_the_name_back_to_the_predefined_one():
    m = make_manager()
    m.add_simvar("ForceTrimSW", "L:CUSTOM", "enum")
    assert rebuild(m)["ForceTrimSW"] == "L:CUSTOM"

    m.remove_simvar("ForceTrimSW")
    assert rebuild(m)["ForceTrimSW"] == "L:TelemFFBHeliFT"


def test_an_override_row_beats_a_modules_variable_of_the_same_name_in_either_order():
    m = make_manager()
    m.add_simvar("ForceTrimSW", "L:USER_ROW", "bool", source=RUNTIME_SOURCE_OVERRIDE)
    m.add_simvar("ForceTrimSW", "L:MODULE", "enum")
    assert rebuild(m)["ForceTrimSW"] == "L:USER_ROW"


def test_a_settings_variable_beats_an_override_row_in_either_order_and_hands_back_on_release():
    m = make_manager()
    m.add_simvar("APMaster", "L:SETTING", "number", source=RUNTIME_SOURCE_SETTING)
    m.add_simvar("APMaster", "L:USER_ROW", "enum", source=RUNTIME_SOURCE_OVERRIDE)
    assert rebuild(m)["APMaster"] == "L:SETTING"
    assert m.has_override("APMaster") and not m.has_override("ForceTrimSW")

    m.remove_simvar("APMaster", source=RUNTIME_SOURCE_SETTING)
    assert rebuild(m)["APMaster"] == "L:USER_ROW"


def test_one_source_is_replaced_without_touching_another():
    m = make_manager()
    m.add_simvar("ControlsLock", "L:ControlLock", "enum")
    m.add_simvar("rowA", "L:A", "number", source=RUNTIME_SOURCE_OVERRIDE)
    m.add_simvar("rowB", "L:B", "number", source=RUNTIME_SOURCE_OVERRIDE)

    m.clear_runtime_simvars(RUNTIME_SOURCE_OVERRIDE)
    m.add_simvar("rowA", "L:A", "number", source=RUNTIME_SOURCE_OVERRIDE)

    subscribed = rebuild(m)
    assert "rowB" not in subscribed
    assert subscribed["rowA"] == "L:A" and subscribed["ControlsLock"] == "L:ControlLock"


def test_clearing_everything_leaves_only_the_predefined_list():
    m = make_manager()
    baseline = rebuild(m)
    m.add_simvar("ControlsLock", "L:ControlLock", "enum")
    m.add_simvar("rowA", "L:A", "number", source=RUNTIME_SOURCE_OVERRIDE)
    assert rebuild(m) != baseline

    m.clear_runtime_simvars()
    assert rebuild(m) == baseline


def test_an_array_element_override_survives_later_rebuilds_and_leaves_the_original_intact():
    m = make_manager()
    original = [sv.var for sv in m.sim_vars[2].vars]
    m.add_simvar("PropRPM:1", "L:CUSTOM_RPM_2", "rpm", source=RUNTIME_SOURCE_OVERRIDE)
    rebuild(m)
    m.add_simvar("ControlsLock", "L:ControlLock", "enum")

    vars_now = [sv for sv in m.substitute_simvars() if sv.name == "PropRPM"][0].vars
    assert vars_now[1].var == "L:CUSTOM_RPM_2"
    assert vars_now[0].var == original[0]
    assert [sv.var for sv in m.sim_vars[2].vars] == original


class TestTelemManagerOwnership:
    """Who clears what: a handler's subscriptions end with it, and the override
    rows are replaced as a set on every read of the config."""

    @pytest.fixture
    def mgr(self, monkeypatch):
        import types
        import telemffb.globals as G
        from telemffb.telem.TelemManager import TelemManager
        monkeypatch.setattr(G, "settings_mgr",
                            types.SimpleNamespace(timed_out=False, active_profile=None),
                            raising=False)
        monkeypatch.setattr(G, "ipc_instance", None, raising=False)
        mgr = TelemManager()
        mgr.set_simconnect(make_manager())
        return mgr

    def rows(self, monkeypatch, *rows):
        from telemffb import xmlutils
        monkeypatch.setattr(xmlutils, "read_sc_overrides", lambda *a, **k: [
            {"name": n, "var": v, "sc_unit": "number", "scale": None} for n, v in rows])

    def test_retiring_the_handler_drops_every_runtime_variable(self, mgr):
        sc = mgr.simconnect
        baseline = rebuild(sc)
        sc.add_simvar("ControlsLock", "L:ControlLock", "enum")
        sc.add_simvar("rowA", "L:A", "number", source=RUNTIME_SOURCE_OVERRIDE)

        mgr._retire_current_aircraft()

        assert rebuild(sc) == baseline

    def test_the_override_setup_keeps_a_modules_variable(self, mgr, monkeypatch):
        sc = mgr.simconnect
        sc.add_simvar("ControlsLock", "L:ControlLock", "enum")
        self.rows(monkeypatch, ("rowA", "L:A"))

        mgr._setup_simconnect_overrides("Some Heli", "MSFS")

        subscribed = rebuild(sc)
        assert subscribed["rowA"] == "L:A" and subscribed["ControlsLock"] == "L:ControlLock"

    def test_a_deleted_override_row_is_unsubscribed_on_the_next_config_read(self, mgr, monkeypatch):
        sc = mgr.simconnect
        self.rows(monkeypatch, ("rowA", "L:A"), ("rowB", "L:B"))
        mgr._setup_simconnect_overrides("Some Heli", "MSFS")
        assert "rowB" in rebuild(sc)

        self.rows(monkeypatch, ("rowA", "L:A"))
        mgr._setup_simconnect_overrides("Some Heli", "MSFS")

        subscribed = rebuild(sc)
        assert "rowB" not in subscribed and subscribed["rowA"] == "L:A"
