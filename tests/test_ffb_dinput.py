"""DirectInput backend contract tests.

Runs DInputFFBDevice/DInputEffectHandle against FakeDIBridge, a pure-Python
stand-in for the bridge DLL, verifying: effect lifecycle, unit conversions at
the handle boundary, write coalescing, override-start downgrade, priority
slot budgeting (tier-1 LRU eviction with invalidate-for-recreate), CP
emulation, and button/hat signal emission.
"""
import pytest

from telemffb.hw.ffb_dinput import (
    DIB_ERR_ACQUISITION, DIB_ERR_DEVICE_FULL, DIB_ERR_GENERAL, DIB_OK,
    DINPUT_CAPABILITIES,
    DibDeviceState, DibEffectParams,
    DIDeviceInfo, DInputEffectHandle, DInputFFBDevice,
)
from telemffb.hw.ffb_rhino import (
    EFFECT_CONSTANT, EFFECT_DETENT, EFFECT_SINE, EFFECT_SPRING,
    EFFECT_SPRING_ADJUSTER, EFFECT_SQUARE,
    FFBReport_SetCondition, FFBReport_SetEnvelope, HapticEffect,
)

pytestmark = [pytest.mark.unit]


class FakeDIBridge:
    """Pure-Python bridge double with a bounded effect pool."""

    def __init__(self, capacity=10):
        self.capacity = capacity
        self.create_error = None   # simulate e.g. DIB_ERR_ACQUISITION
        self.effects = {}          # id -> {"type": int, "params": DibEffectParams}
        self.started = set()
        self.update_calls = []     # (effect_id, params snapshot bytes)
        self.state = DibDeviceState()
        self.state.hats = 0xFFFF
        self.poll_error = None
        self.open_error = None
        self._next_id = 1
        self.devices = [{
            "guid": "{FAKE-GUID}", "name": "Fake FFB Stick", "vid": 0x1234,
            "pid": 0x5678, "ff_axes": 2, "buttons": 12, "povs": 1,
            "sample_period_us": 1000,
            "effects": ["constant", "sine", "spring", "damper"],
        }]

    def enumerate(self):
        return self.devices

    def open(self, guid):
        if self.open_error:
            raise self.open_error
        return 1

    def release(self, device):
        self.effects.clear()
        self.started.clear()

    def device_reset(self, device):
        self.reset_calls = getattr(self, 'reset_calls', 0) + 1
        self.effects.clear()
        self.started.clear()
        return DIB_OK

    def poll(self, device):
        if self.poll_error:
            raise self.poll_error
        return self.state

    def _copy_params(self, params):
        return DibEffectParams.from_buffer_copy(bytes(params))

    def last_error(self):
        return "fake bridge error detail"

    def effect_create(self, device, effect_type, params):
        if self.create_error is not None:
            return self.create_error
        if len(self.effects) >= self.capacity:
            return DIB_ERR_DEVICE_FULL
        effect_id = self._next_id
        self._next_id += 1
        self.effects[effect_id] = {"type": effect_type, "params": self._copy_params(params)}
        return effect_id

    def effect_update(self, effect, params):
        if effect not in self.effects:
            return -2
        self.effects[effect]["params"] = self._copy_params(params)
        self.update_calls.append((effect, bytes(params)))
        return DIB_OK

    def effect_start(self, effect, iterations=1):
        if effect not in self.effects:
            return -2
        self.started.add(effect)
        return DIB_OK

    def effect_stop(self, effect):
        self.started.discard(effect)
        return DIB_OK

    def effect_destroy(self, effect):
        self.started.discard(effect)
        self.effects.pop(effect, None)
        return DIB_OK


@pytest.fixture
def bridge():
    return FakeDIBridge()

@pytest.fixture
def device(bridge):
    return DInputFFBDevice("{FAKE-GUID}", bridge=bridge, poll_interval_ms=0)


def make_condition(axis=0, **kw):
    defaults = dict(parameterBlockOffset=axis, cpOffset=0,
                    positiveCoefficient=0, negativeCoefficient=0,
                    positiveSaturation=4096, negativeSaturation=4096)
    defaults.update(kw)
    return FFBReport_SetCondition(**defaults)


class TestDeviceBasics:
    def test_capabilities(self, device):
        caps = device.caps
        assert caps is DINPUT_CAPABILITIES
        assert caps.has_cp_telemetry           # emulated, same contract
        assert not caps.has_gains
        assert not caps.has_spring_adjuster
        assert not caps.has_axis_override
        assert not caps.has_force_telemetry
        assert device.supports_axis_override() is False

    def test_info_from_enumeration(self, device):
        assert device.info.product_string == "Fake FFB Stick"
        assert device.info.vidpid() == "1234:5678"

    def test_enumerate_maps_devices(self, bridge):
        devs = DInputFFBDevice.enumerate(bridge=bridge)
        assert len(devs) == 1
        assert isinstance(devs[0], DIDeviceInfo)
        assert devs[0].guid == "{FAKE-GUID}"
        assert devs[0].ff_axes == 2

    def test_vpforce_only_surface_is_safe(self, device):
        device.set_deadzone(100)
        device.set_gain(1, 50)
        device.send_axis_override(1, 100)
        device.clear_axis_override()
        assert device.get_gains() is None
        assert device.get_firmware_version() is None
        assert device.serial is None


class TestEffectLifecycle:
    def test_unsupported_types_return_none(self, device):
        assert device.create_effect(EFFECT_DETENT) is None
        assert device.create_effect(EFFECT_SPRING_ADJUSTER) is None

    def test_create_configure_start_stop_destroy(self, device, bridge):
        handle = device.create_effect(EFFECT_SPRING)
        assert isinstance(handle, DInputEffectHandle)
        assert bool(handle)
        assert bridge.effects[handle.effect_id]["type"] == EFFECT_SPRING

        handle.setCondition(make_condition(axis=0, positiveCoefficient=2048))
        params = bridge.effects[handle.effect_id]["params"]
        assert params.condition_x.positive_coefficient == 2048
        assert params.condition_x.active == 1

        handle.start()
        assert handle.started
        assert handle.effect_id in bridge.started

        handle.stop()
        assert not handle.started
        assert handle.effect_id not in bridge.started

        eid = handle.effect_id
        handle.destroy()
        assert not bool(handle)
        assert eid not in bridge.effects

    def test_condition_values_are_clamped(self, device, bridge):
        handle = device.create_effect(EFFECT_SPRING)
        handle.setCondition(make_condition(axis=1, positiveCoefficient=4096,
                                           cpOffset=-4096))
        params = bridge.effects[handle.effect_id]["params"]
        assert params.condition_y.positive_coefficient == 4096
        assert params.condition_y.cp_offset == -4096

    def test_zero_saturation_means_unlimited(self, device, bridge):
        """The app's condition objects default saturation to 0, which Rhino
        firmware reads as unlimited but DirectInput reads as a zero-force
        cap - the killer of all spring force in the first flight test."""
        handle = device.create_effect(EFFECT_SPRING)
        handle.setCondition(make_condition(axis=0, positiveCoefficient=4096,
                                           positiveSaturation=0,
                                           negativeSaturation=0))
        params = bridge.effects[handle.effect_id]["params"]
        assert params.condition_x.positive_saturation == 4096
        assert params.condition_x.negative_saturation == 4096

    def test_updates_are_coalesced(self, device, bridge):
        handle = device.create_effect(EFFECT_SPRING)
        cond = make_condition(axis=0, positiveCoefficient=1000)
        handle.setCondition(cond)
        n = len(bridge.update_calls)
        handle.setCondition(make_condition(axis=0, positiveCoefficient=1000))
        assert len(bridge.update_calls) == n  # identical params: no transport
        handle.setCondition(make_condition(axis=0, positiveCoefficient=2000))
        assert len(bridge.update_calls) == n + 1

    def test_constant_force_conversion(self, device, bridge):
        handle = device.create_effect(EFFECT_CONSTANT)
        handle.setConstantForce(0.5, 90)
        params = bridge.effects[handle.effect_id]["params"]
        assert params.constant_magnitude == 2048
        assert params.direction_deg == 90
        handle.setConstantForce(-1.0, 450)  # direction wraps
        params = bridge.effects[handle.effect_id]["params"]
        assert params.constant_magnitude == -4096
        assert params.direction_deg == 90

    def test_periodic_conversion(self, device, bridge):
        handle = device.create_effect(EFFECT_SINE)
        handle.setPeriodic(freq=20, magnitude=0.25, direction=180)
        params = bridge.effects[handle.effect_id]["params"]
        assert params.periodic_period_ms == 50   # 1000/20
        assert params.periodic_magnitude == 1024
        assert params.direction_deg == 180

    def test_envelope_mapping(self, device, bridge):
        handle = device.create_effect(EFFECT_SINE)
        env = FFBReport_SetEnvelope(attackFromForce=1024, decayToForce=512,
                                    attackTime=100, decayTime=200)
        handle.setEnvelope(env)
        params = bridge.effects[handle.effect_id]["params"]
        assert params.envelope_active == 1
        assert params.attack_level == 1024
        assert params.fade_level == 512
        assert params.attack_time_ms == 100
        assert params.fade_time_ms == 200

    def test_override_start_downgrades_to_normal_start(self, device, bridge):
        handle = device.create_effect(EFFECT_SPRING)
        handle.setCondition(make_condition(axis=0, positiveCoefficient=2048))
        handle.start(override=True)
        assert handle.started
        assert handle.effect_id in bridge.started

    def test_spring_offset_passes_through_natively(self, device, bridge):
        """Offsets render natively (the bridge uses DIEFF_POLAR condition
        coordinates, the one formation whose lOffset works on SideWinder-class
        drivers AND the PID driver) - and CP telemetry reports the same."""
        handle = device.create_effect(EFFECT_SPRING)
        handle.setCondition(make_condition(axis=0, positiveCoefficient=4096,
                                           negativeCoefficient=4096,
                                           cpOffset=2048))
        handle.start()
        params = bridge.effects[handle.effect_id]["params"]
        assert params.condition_x.cp_offset == 2048
        device._poll_once()
        assert device.get_input().CP_XY()[0] == 0.5

    def test_zero_coefficient_condition_is_muted_device_side(self, device, bridge):
        """A playing zero-coefficient spring changes the back-drive feel on
        consumer sticks (energized motors cog); zero force must render as no
        effect at all, resuming transparently when force returns."""
        handle = device.create_effect(EFFECT_SPRING)
        handle.setCondition(make_condition(axis=0, positiveCoefficient=0,
                                           negativeCoefficient=0))
        handle.start()
        assert handle.started                      # logically started
        assert handle.effect_id not in bridge.started  # muted on the device

        # force returns -> device effect resumes without an app-level start
        handle.setCondition(make_condition(axis=0, positiveCoefficient=2048))
        assert handle.effect_id in bridge.started

        # back to zero (force trim held) -> muted again
        handle.setCondition(make_condition(axis=0, positiveCoefficient=0))
        assert handle.effect_id not in bridge.started
        assert handle.started

        handle.stop()
        assert not handle.started

    def test_center_updates_flow_while_muted(self, device, bridge):
        """Force-trim flow: while the spring is muted at 0%, center updates
        must keep landing on the (stopped, not destroyed) device effect and
        on the CP telemetry, so restoring the coefficient re-engages the
        spring at the latest center.  Verified against real hardware:
        SetParameters on a stopped effect renders on the next Start."""
        handle = device.create_effect(EFFECT_SPRING)
        handle.setCondition(make_condition(axis=0, positiveCoefficient=0,
                                           cpOffset=0))
        handle.start()
        assert handle.effect_id not in bridge.started

        # center moves while muted (button held, stick moving)
        handle.setCondition(make_condition(axis=0, positiveCoefficient=0,
                                           cpOffset=1500))
        assert handle.effect_id in bridge.effects        # still downloaded
        assert bridge.effects[handle.effect_id]["params"].condition_x.cp_offset == 1500
        device._poll_once()
        assert device.get_input().CP_XY()[0] is None     # coef 0: no center reported

        # button released: coefficient restored -> plays at the muted-state center
        handle.setCondition(make_condition(axis=0, positiveCoefficient=2048,
                                           cpOffset=1500))
        assert handle.effect_id in bridge.started
        assert bridge.effects[handle.effect_id]["params"].condition_x.cp_offset == 1500
        device._poll_once()
        assert device.get_input().CP_XY()[0] == pytest.approx(1500 / 4096)

    def test_nonzero_condition_plays_normally(self, device, bridge):
        handle = device.create_effect(EFFECT_SPRING)
        handle.setCondition(make_condition(axis=0, positiveCoefficient=1000))
        handle.start()
        assert handle.effect_id in bridge.started

    def test_ffb_priority_loss_raises_error_and_recovers(self, device, bridge, caplog):
        """When a foreground app (the sim's own FFB) holds priority, effect
        creation fails continuously via the per-frame lazy re-create: the
        condition is logged at ERROR level (captured and de-duplicated by
        the exception tracker) with actionable advice and a stable message,
        effects return None cleanly, and recovery is announced when creation
        succeeds again."""
        import logging as _logging
        from telemffb.hw.ffb_dinput import DIB_ERR_ACQUISITION

        bridge.create_error = DIB_ERR_ACQUISITION
        with caplog.at_level(_logging.ERROR):
            for _ in range(5):   # five telemetry frames of retries
                assert device.create_effect(EFFECT_SPRING) is None
        blocked = [r for r in caplog.records if "FFB effects blocked" in r.message]
        assert blocked and all(r.levelno == _logging.ERROR for r in blocked)
        # both remedies must be named: turning the sim's own force feedback
        # off, and - for a tap user, where nothing will release the device
        # mid-session - starting TelemFFB first and restarting the sim
        assert "force feedback" in blocked[0].message
        assert "start TelemFFB before the sim" in blocked[0].message
        # stable message text so the exception tracker's dedup groups them
        assert len({r.message for r in blocked}) == 1

        # priority released: creation succeeds and recovery is announced
        bridge.create_error = None
        with caplog.at_level(_logging.INFO):
            handle = device.create_effect(EFFECT_SPRING)
        assert handle is not None
        assert any("FFB priority restored" in r.message for r in caplog.records)

    def test_reset_effects_invalidates_handles(self, device, bridge):
        h1 = device.create_effect(EFFECT_SPRING)
        h2 = device.create_effect(EFFECT_SINE)
        device.reset_effects()
        assert not bool(h1) and not bool(h2)
        assert not bridge.effects


class TestSlotBudgeting:
    def test_tier0_evicts_lru_periodic(self, bridge):
        bridge.capacity = 3
        device = DInputFFBDevice("{FAKE-GUID}", bridge=bridge, poll_interval_ms=0)

        spring = device.create_effect(EFFECT_SPRING)
        sine = device.create_effect(EFFECT_SINE)
        square = device.create_effect(EFFECT_SQUARE)
        sine.start()
        square.start()  # sine is now least-recently-started

        constant = device.create_effect(EFFECT_CONSTANT)  # pool full: evict
        assert constant is not None and bool(constant)
        assert not bool(sine)          # LRU periodic evicted + invalidated
        assert bool(square)            # newer cue survives
        assert bool(spring)            # force model never evicted

    def test_no_periodic_to_evict_fails_cleanly(self, bridge):
        bridge.capacity = 2
        device = DInputFFBDevice("{FAKE-GUID}", bridge=bridge, poll_interval_ms=0)
        spring = device.create_effect(EFFECT_SPRING)
        constant = device.create_effect(EFFECT_CONSTANT)
        assert spring is not None and constant is not None
        assert device.create_effect(EFFECT_SPRING) is None  # only tier 0 present

    def test_evicted_handle_recreates_via_haptic_effect(self, bridge):
        """The owning HapticEffect lazily re-creates an evicted cue."""
        bridge.capacity = 2
        device = DInputFFBDevice("{FAKE-GUID}", bridge=bridge, poll_interval_ms=0)
        saved = HapticEffect.device
        HapticEffect.device = device
        try:
            cue = HapticEffect().periodic(frequency=10, magnitude=0.1, direction=0)
            cue.start()
            assert cue.started

            spring = device.create_effect(EFFECT_SPRING)   # fills the pool
            constant = device.create_effect(EFFECT_CONSTANT)  # evicts the cue
            assert constant is not None
            assert not cue.started

            cue.start()  # lazy re-create needs a free slot again
            # pool is full with tier-0 effects; periodic cannot evict tier 0
            assert not cue.started

            spring.destroy()
            cue.start()
            assert cue.started
        finally:
            HapticEffect.device = saved


@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
class TestIL2KoreaForces:
    """IL-2 Korea Const/Damper record rendering (il2_ffb_forces).

    Rendered ONLY on generic DirectInput devices: on VPforce hardware the
    game renders these itself through its native DirectInput channel, and
    re-rendering would double the forces."""

    ORDINAL = 2

    def _make_instance(self, records):
        import json as _json
        import telemffb.globals as G
        from tests.framework.base import BaseTelemetryEffectTestCase
        from tests.framework.utils import TelemetryDataBuilder
        from telemffb.sim.aircrafts_il2 import Aircraft as IL2Aircraft

        case = BaseTelemetryEffectTestCase()
        case.setup_method()
        inst = case.create_aircraft_instance(IL2Aircraft, name="TestIL2")
        inst._telem_data = (TelemetryDataBuilder()
                            .with_field("FFBRecords", _json.dumps(records))
                            .build())
        G.il2_ffb_device_ordinal = self.ORDINAL
        return case, inst

    def _run(self, records, di_guid):
        import telemffb.globals as G
        saved = (G.device_di_guid, G.il2_ffb_device_ordinal)
        case, inst = self._make_instance(records)
        G.device_di_guid = di_guid
        try:
            inst.il2_ffb_forces()
            return case.mock_effects
        finally:
            G.device_di_guid, G.il2_ffb_device_ordinal = saved

    def test_const_and_damper_rendered_on_di_device(self):
        effects = self._run([
            {"axis": 0, "dev": self.ORDINAL, "type": 1, "pos": 0, "force": 0.5, "amp": 0, "freq": 0},
            {"axis": 0, "dev": self.ORDINAL, "type": 2, "pos": 0, "force": 0.25, "amp": 0, "freq": 0},
            {"axis": 1, "dev": self.ORDINAL, "type": 2, "pos": 0, "force": 0.5, "amp": 0, "freq": 0},
            {"axis": 0, "dev": 9, "type": 1, "pos": 0, "force": 1.0, "amp": 0, "freq": 0},  # other device: ignored
        ], di_guid='{GUID}')

        const = effects.dict['il2_ffb_const']
        assert const.started
        assert const._magnitude == pytest.approx(0.5)
        assert const._direction == pytest.approx(270)   # +X push (measured convention)

        damper = effects.dict['il2_ffb_damper']
        assert damper.started
        assert damper._x_coefficient == 1024            # 0.25 * 4096
        assert damper._y_coefficient == 2048            # 0.50 * 4096

    def test_not_rendered_on_vpforce_device(self):
        effects = self._run([
            {"axis": 0, "dev": self.ORDINAL, "type": 1, "pos": 0, "force": 0.5, "amp": 0, "freq": 0},
        ], di_guid=None)
        assert 'il2_ffb_const' not in effects.dict
        assert 'il2_ffb_damper' not in effects.dict

    def test_transient_zero_stops_effects(self):
        """No caching: absent/zero records mean the game stopped commanding
        the force - the effects must stop rather than repeat stale kicks."""
        effects = self._run([], di_guid='{GUID}')
        assert not effects.dict['il2_ffb_const'].started
        assert not effects.dict['il2_ffb_damper'].started


# the simconnect package (pulled in via TelemManager) leaks a file handle at
# import time; keep its ResourceWarning from failing these tests
@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
class TestCapabilityGating:
    """App-side gates read HapticEffect.device.caps: VPforce-only features
    must switch off cleanly when a DirectInput device is attached."""

    def _swap_device(self, device):
        saved = HapticEffect.device
        HapticEffect.device = device
        return saved

    def test_vpconf_and_gains_gate(self, device):
        from telemffb.telem.TelemManager import TelemManager
        saved = self._swap_device(device)
        try:
            assert TelemManager._device_has_gains() is False
        finally:
            HapticEffect.device = saved

    def test_vpforce_mock_keeps_gains(self):
        from telemffb.telem.TelemManager import TelemManager
        from tests.framework.base import MockFFBDevice
        saved = self._swap_device(MockFFBDevice())
        try:
            assert TelemManager._device_has_gains() is True
        finally:
            HapticEffect.device = saved

    def test_gforce_offset_mode_flags_error_on_di_device(self):
        """The Advanced G-Force gate is per OUTPUT MODE: 'offset' (spring
        adjuster) flags an error on a DI device, 'constant' passes - the
        Advanced curve itself is device-neutral.  (Advanced SPRING mode needs
        no gate on MSFS/XP: its implementation there is plain-spring gain
        shaping; the adjuster-based ac_modify_game_spring is DCS/IL-2 only.)"""
        from tests.framework.base import BaseTelemetryEffectTestCase
        from tests.framework.utils import TelemetryDataBuilder
        from telemffb.sim.msfs_xp.Aircraft import Aircraft
        from telemffb.SettingsManager import GEffectModeEnum

        case = BaseTelemetryEffectTestCase()
        case.setup_method()
        try:
            inst = case.create_aircraft_instance(
                Aircraft, name="TestPlane", _test_sim_is_msfs=True,
                _test_device_type="joystick")
            inst.gforce_effect_mode = GEffectModeEnum.ADVANCED
            inst._telem_data = (TelemetryDataBuilder().on_ground(False)
                                .with_airspeed(50.0)
                                .with_field("FFBType", "joystick").build())

            class _DIDevice:
                from telemffb.hw.ffb_dinput import DINPUT_CAPABILITIES as caps

            saved = HapticEffect.device
            HapticEffect.device = _DIDevice()
            try:
                inst.gforce_effect_adv_curve = {"mode": "offset", "scale": 1}
                assert inst._GForceEffectMixIn__check_firmware_support() is False
                assert "offset" in inst.telem_data.error

                # end-to-end through the MRO: AdvancedSpringMixIn swallows
                # the ADVANCED+offset call expecting the (DCS-only) adjuster
                # path to run it; the MsfsXp mixin must bypass that so the
                # effect - and this gate - actually execute on MSFS/X-Plane
                inst._telem_data.error = None
                inst.ac_update_gforce_effect(inst.telem_data)
                assert inst.telem_data.error and "offset" in inst.telem_data.error

                inst._telem_data.error = None
                inst.gforce_effect_adv_curve = {"mode": "constant", "scale": 1}
                assert inst._GForceEffectMixIn__check_firmware_support() is True
                assert not inst.telem_data.error
            finally:
                HapticEffect.device = saved
        finally:
            teardown = getattr(case, 'teardown_method', None)
            if teardown:
                teardown()

    def test_native_ffb_sims_gated_on_di_device(self):
        """DCS/IL-2/BMS render native DirectInput FFB and cannot coexist
        with the bridge's exclusive acquisition: their listeners must report
        disabled while a DI device is selected, WITHOUT touching the stored
        enable settings (soft gate - reselecting VPforce restores them)."""
        from types import SimpleNamespace
        import telemffb.globals as G
        from telemffb.telem.SimTelemListener import SimTelemListener

        saved = (getattr(G, 'system_settings', None), getattr(G, 'args', None),
                 G.device_di_guid)
        G.system_settings = {'enableDCS': True, 'enableIL2': True,
                             'enableBMS': True, 'enableMSFS': True}
        G.args = SimpleNamespace(sim=None)
        try:
            listeners = {name: SimTelemListener(name)
                         for name in ('DCS', 'IL2', 'BMS', 'MSFS')}

            G.device_di_guid = None                # VPforce device
            assert all(sim.is_enabled for sim in listeners.values())

            # No sim is gated for DirectInput devices: native-FFB sims
            # (DCS/IL-2/BMS) work on a DI device with the tap/sink dinput8
            # wrapper in the game folder; without it, FFB priority loss
            # surfaces as an actionable exception-tracker error
            G.device_di_guid = '{SOME-GUID}'       # DirectInput device
            assert all(sim.is_enabled for sim in listeners.values())
        finally:
            G.system_settings, G.args, G.device_di_guid = saved

    def test_di_device_lacks_vpforce_features(self, device):
        caps = device.caps
        assert not caps.has_spring_adjuster   # Advanced Spring Override gate
        assert not caps.has_detents           # controls-lock detent gate
        assert not caps.has_firmware_version  # status bar label gate
        assert not caps.has_gains             # vpconf/configurator gate

    def test_configurator_dialog_constructs_without_gains(self, device):
        """Startup regression: MainWindow builds a ConfiguratorDialog
        unconditionally; get_gains() returning None must not crash it."""
        from PyQt6 import QtWidgets
        try:
            app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        except Exception as e:
            pytest.skip(f"cannot create QApplication: {e}")

        from telemffb.ConfiguratorDialog import ConfiguratorDialog
        saved = self._swap_device(device)
        try:
            dialog = ConfiguratorDialog(None)
            assert dialog.at_show_state is None
            dialog.cb_MasterGain.setChecked(True)   # cb_toggle path
            assert dialog.sl_MasterGain.value() == 0
            dialog.set_gains_from_object(None)      # exit reset-gains path
        finally:
            HapticEffect.device = saved

    def test_get_device_forces_none_safe(self, device, bridge):
        """Telemetry-frame regression: forceXY() is None on this backend and
        must degrade to (0, 0) instead of failing the unpack in
        aircraft_base.on_telemetry (which killed all effects in flight)."""
        from telemffb.sim.base.AircraftEffectUtilsBase import AircraftEffectUtilsBase
        device._poll_once()
        saved = self._swap_device(device)
        try:
            assert AircraftEffectUtilsBase._get_device_forces() == (0.0, 0.0)
        finally:
            HapticEffect.device = saved

    def test_vpconf_upload_gated_by_capabilities(self, device):
        """upload_vpconf_profile must no-op (no Configurator spawn) when the
        connected device has no gains."""
        import telemffb.globals as G
        from telemffb.utils import upload_vpconf_profile
        saved_caps = G.device_capabilities
        G.device_capabilities = device.caps
        try:
            # would raise/spawn on the normal path; the gate returns first
            assert upload_vpconf_profile("nonexistent.vpconf", None) is None
        finally:
            G.device_capabilities = saved_caps


class TestInputAndCPEmulation:
    def test_no_input_before_first_poll(self, device):
        assert device.get_input() is None

    def test_axis_scaling(self, device, bridge):
        bridge.state.x = 2048
        bridge.state.y = -4096
        device._poll_once()
        snapshot = device.get_input()
        assert snapshot.axisXY() == (0.5, -1.0)
        assert snapshot.rawAxisXY() == snapshot.axisXY()
        assert snapshot.axisOverrideActive() is False
        assert snapshot.forceXY() is None

    def test_cp_follows_last_spring_condition(self, device, bridge):
        handle = device.create_effect(EFFECT_SPRING)
        handle.setCondition(make_condition(axis=0, cpOffset=1024,
                                           positiveCoefficient=4096,
                                           negativeCoefficient=4096))
        device._poll_once()
        cp_x, cp_y = device.get_input().CP_XY()
        assert cp_x == 0.25
        assert cp_y is None  # no spring written on Y yet

    def test_cp_none_when_coefficient_zero(self, device, bridge):
        handle = device.create_effect(EFFECT_SPRING)
        handle.setCondition(make_condition(axis=0, cpOffset=1024,
                                           positiveCoefficient=0))
        device._poll_once()
        assert device.get_input().CP_XY()[0] is None

    def test_cp_scaled_axis(self, device, bridge):
        handle = device.create_effect(EFFECT_SPRING)
        handle.setCondition(make_condition(axis=0, cpOffset=2048,
                                           positiveCoefficient=4096,
                                           negativeCoefficient=4096))
        bridge.state.x = 4096  # full deflection, center at +0.5
        device._poll_once()
        scaled_x, _ = device.get_input().CP_scaled_axisXY()
        assert scaled_x == pytest.approx(1.0)

    def test_button_signals(self, device, bridge):
        pressed, released = [], []
        device.buttonPressed.connect(pressed.append)
        device.buttonReleased.connect(released.append)

        bridge.state.buttons = 0b101
        device._poll_once()
        bridge.state.buttons = 0b001
        device._poll_once()

        assert 0 in pressed and 2 in pressed
        assert released == [2]

    def test_hat_signals_use_rhino_encoding(self, device, bridge):
        pressed, released = [], []
        device.buttonPressed.connect(pressed.append)
        device.buttonReleased.connect(released.append)

        bridge.state.hats = 0xFFF2  # hat 0 -> position 2
        device._poll_once()
        assert pressed == [0x80 | 2]

        bridge.state.hats = 0xFFFF  # centered
        device._poll_once()
        assert released == [0x80 | 2]

    def test_snapshot_button_reads(self, device, bridge):
        bridge.state.buttons = 0b10
        bridge.state.hats = 0xFFF3
        device._poll_once()
        snapshot = device.get_input()
        assert snapshot.isButtonPressed(2)
        assert not snapshot.isButtonPressed(1)
        assert snapshot.isButtonPressed(0x80 | 3)
        assert set(snapshot.getPressedButtons()) == {2, 0x80 | 3}


class TestShutdown:
    """Deliberate release for the live device switch: the poll and
    reconnect machinery must stop, and a queued reconnect retry must not
    re-acquire the hardware behind a new device."""

    def test_shutdown_releases_the_bridge_handle(self, bridge):
        device = DInputFFBDevice("{FAKE-GUID}", bridge=bridge, poll_interval_ms=0)
        device.shutdown()
        assert device._handle is None

    def test_reconnect_retry_after_shutdown_is_inert(self, bridge):
        device = DInputFFBDevice("{FAKE-GUID}", bridge=bridge, poll_interval_ms=0)
        device.shutdown()
        device._try_reconnect()          # a queued retry firing late
        assert device._handle is None    # did not re-open

    def test_poll_after_shutdown_is_inert(self, bridge):
        device = DInputFFBDevice("{FAKE-GUID}", bridge=bridge, poll_interval_ms=0)
        device.shutdown()
        bridge.poll_error = OSError("polled a released handle")
        device.timerEvent(None)          # a queued tick firing late: no poll,
                                         # no exception, no reconnect kickoff
        assert not device._reconnecting

    def test_shutdown_is_idempotent(self, bridge):
        device = DInputFFBDevice("{FAKE-GUID}", bridge=bridge, poll_interval_ms=0)
        device.shutdown()
        device.shutdown()
        assert device._handle is None


class TestUpdateFailureSelfHeal:
    """A vendor driver can silently destroy downloaded effects (observed on
    SideWinder when an enumeration created a second device object for held
    hardware): every update then fails forever with a generic error.  The
    handle must presume the device-side effect dead after a few consecutive
    failures and invalidate itself, so the owning HapticEffect lazily
    re-creates the effect on the next frame."""

    def _failing_handle(self, bridge):
        device = DInputFFBDevice("{FAKE-GUID}", bridge=bridge, poll_interval_ms=0)
        handle = device.create_effect(EFFECT_CONSTANT)
        bridge.effect_update = lambda effect, params: DIB_ERR_GENERAL
        return handle

    def _fail_push(self, handle, magnitude):
        handle.params.constant_magnitude = magnitude   # defeat the hash skip
        handle._push()

    def test_persistent_update_failure_triggers_device_recovery(self, bridge):
        handle = self._failing_handle(bridge)
        for magnitude in (1, 2):
            self._fail_push(handle, magnitude)
            assert handle.effect_id                    # not yet
        self._fail_push(handle, 3)
        assert handle.effect_id == 0                   # presumed dead
        assert not handle._started
        assert getattr(bridge, 'reset_calls', 0) == 1  # device-level reset

    def test_success_resets_the_failure_count(self, bridge):
        device = DInputFFBDevice("{FAKE-GUID}", bridge=bridge, poll_interval_ms=0)
        handle = device.create_effect(EFFECT_CONSTANT)
        good_update = bridge.effect_update
        bridge.effect_update = lambda effect, params: DIB_ERR_GENERAL
        self._fail_push(handle, 1)
        self._fail_push(handle, 2)
        bridge.effect_update = good_update
        self._fail_push(handle, 3)                     # succeeds: counter resets
        bridge.effect_update = lambda effect, params: DIB_ERR_GENERAL
        self._fail_push(handle, 4)
        self._fail_push(handle, 5)
        assert handle.effect_id                        # 2 + 2, never 3 in a row

    def test_acquisition_loss_never_invalidates(self, bridge):
        """FFB priority loss has its own latched handling and resolves when
        priority returns - churning re-creates would fight it."""
        device = DInputFFBDevice("{FAKE-GUID}", bridge=bridge, poll_interval_ms=0)
        handle = device.create_effect(EFFECT_CONSTANT)
        bridge.effect_update = lambda effect, params: DIB_ERR_ACQUISITION
        for magnitude in range(1, 8):
            self._fail_push(handle, magnitude)
        assert handle.effect_id                        # still the same effect

    def test_start_failures_also_heal(self, bridge):
        device = DInputFFBDevice("{FAKE-GUID}", bridge=bridge, poll_interval_ms=0)
        handle = device.create_effect(EFFECT_CONSTANT)
        bridge.effect_start = lambda effect, iterations=1: DIB_ERR_GENERAL
        for _ in range(3):
            handle.start()
        assert handle.effect_id == 0

    def test_recovery_is_device_wide_and_clears_orphans(self, bridge):
        """'Dead' effects are usually ORPHANS - still playing on the device,
        unreachable through their interfaces (E_HANDLE) - and they die in
        batches.  Recovery must be one device-level reset (the only thing
        that clears orphans) that invalidates EVERY handle for lazy
        re-creation, not per-effect churn."""
        device = DInputFFBDevice("{FAKE-GUID}", bridge=bridge, poll_interval_ms=0)
        failing = device.create_effect(EFFECT_CONSTANT)
        bystander = device.create_effect(EFFECT_SINE)
        bridge.effect_update = lambda effect, params: DIB_ERR_GENERAL
        for magnitude in (1, 2, 3):
            self._fail_push(failing, magnitude)
        assert failing.effect_id == 0
        assert bystander.effect_id == 0            # everyone re-creates
        assert not bridge.effects                  # device memory wiped
        assert bridge.reset_calls == 1

    def test_recovery_bursts_are_debounced(self, bridge):
        """A burst of dead handles discovered in one frame must trigger
        exactly one reset."""
        device = DInputFFBDevice("{FAKE-GUID}", bridge=bridge, poll_interval_ms=0)
        device.recover_effects(reason="test")
        device.recover_effects(reason="test")
        assert bridge.reset_calls == 1
