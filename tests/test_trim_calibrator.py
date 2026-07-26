"""Unit tests for the elevator virtual_y auto-calibration engine (TrimCalibrator).

These tests are fully headless: no FFB device, no simulator. The closed-loop
tests drive the calibrator against an in-test ``FakePlantAircraft`` — a minimal
implementation of the aircraft interface the calibrator uses, plus a first-order
pitch/roll toy model — over a monkeypatched deterministic clock.
"""
import math
import time

import pytest

import telemffb.globals as G
from telemffb.sim.BaseTelemetryData import BaseTelemetryData
from telemffb.sim.msfs_xp.TrimCalibrator import TrimCalibrator, CalState
from telemffb.utils import clamp

pytestmark = [pytest.mark.unit, pytest.mark.msfs, pytest.mark.joystick]


@pytest.fixture(autouse=True)
def _calibrator_test_env():
    """Never leak G.trimcal_hold_until (fake-clock timestamps). The diagnostic
    trace defaults OFF on the engine, so no CSVs hit the real log folder."""
    yield
    G.trimcal_hold_until = 0.0


# --------------------------------------------------------------------------- #
#  Fakes
# --------------------------------------------------------------------------- #

class FakeSpring:
    def __init__(self):
        self.cpOffset = 0
        self.coeff = 0

    def set_coefficient(self, c):
        self.coeff = c


class FakeSpringHandle:
    def __init__(self):
        self.name = ""
        self.started = False

    def setCondition(self, cond):
        pass

    def start(self):
        self.started = True


class FakeSimConnect:
    def __init__(self):
        self.events = []
        self.sim_data = []

    def send_event_to_msfs(self, event, data=0):
        self.events.append((event, data))

    def set_simdatum_to_msfs(self, simvar, value, units=""):
        self.sim_data.append((simvar, value, units))


class FakePlantAircraft:
    """Implements the calibrator's ``ac`` interface + a toy flight model.

    Pitch/VS model (first-order lag toward an algebraic target):
        target_vs = K_E * (cmd_elev + coupling * trim)
        vs        -> target_vs with time constant vs_tau
    so the steady-state elevator to hold vs=0 is ``cmd_elev = -coupling * trim``
    (slope = -coupling), giving ``virtual_y = 1 - coupling / physical_y``.

    Roll is an integrator of the aileron command (light).
    """

    def __init__(self, coupling=0.5, physical_y=1.0, K_E=25.0, vs_tau=0.4, K_A=60.0,
                 trim_natural=0.0):
        self.coupling = coupling
        self.K_E = K_E
        self.vs_tau = vs_tau
        self.K_A = K_A
        # Trim at which the elevator command needed to hold level is zero — the
        # aircraft's "natural" trim point the neutralization phase seeks.
        self.trim_natural = trim_natural

        # trim plumbing: travel limits enable the calibrator's direct SimVar
        # method (the shipping default); switches model corner-case aircraft
        self.report_limits = True
        self.trim_up_limit = 15.0      # degrees
        self.trim_dn_limit = -15.0     # degrees (signed, like the telemetry)
        self.responds_to_simvar = True # False models trim deaf to direct writes
        self.responds_to_axis = True   # False models trim deaf to axis events
        self.trim_response_sign = 1.0  # -1 models an inverted trim response
        self.simvar_trim_writes = 0    # trim writes received (even if ignored)
        self.axis_trim_events = 0
        self._telem_data = None        # set per frame by telem(), like TelemManager

        # calibrator-read settings / flags
        self.joystick_trim_follow_gain_physical_y = physical_y
        self.joystick_trim_follow_gain_virtual_y = 0.5
        self.joystick_trim_follow_gain_physical_x = 1.0
        self.joystick_trim_follow_gain_virtual_x = 1.0
        self.trim_following = True
        self.joystick_x_axis_scale = 1.0
        self.joystick_y_axis_scale = 1.0
        self.telemffb_controls_axes = True
        self.local_disable_axis_control = False
        self.phys_stick = (0.0, 0.0)    # hands-off device axes

        # spring stubs
        self.spring_x = FakeSpring()
        self.spring_y = FakeSpring()
        self._spring_handle = FakeSpringHandle()
        self._simconnect = FakeSimConnect()

        # commanded inputs captured from the calibrator
        self.cmd_elev = 0.0
        self.cmd_ail = 0.0
        self.trim = 0.0

        # plant state
        self.vs = 0.0
        self.roll = 0.0
        self.roll_bias_rate = 0.0   # external roll rate (deg/s), e.g. engine torque
        self.pitch = 0.0
        self.ias = 60.0
        self.max_trim_step = 0.0    # largest applied per-frame trim change seen

    # --- ac interface used by the calibrator ---
    def _sim_is_msfs(self):
        return True

    def _sim_is_xplane(self):
        return False

    def _use_firmware_axis_backend(self):
        return False

    def _get_msfs_axis_config(self, axis, default_var):
        return default_var, 16384

    def _send_msfs_axis_value(self, var, value, axis_range, scale):
        if "ELEV" in var:
            self.cmd_elev = value
        elif "AILER" in var:
            self.cmd_ail = value
        return 0

    def send_xp_command(self, cmd):
        pass

    def _send_firmware_fixed_axes(self, x, y):
        pass

    def write_xp_dataref(self, dataref, value, type="float"):
        pass

    def _get_device_raw_axes(self):
        return self.phys_stick

    def effective_coupling(self, trim):
        """Trim->pitch coupling; override for nonlinear/kinked aircraft."""
        return self.coupling

    def _trimwheel_trim_limits(self, telem_data):
        # aircraft interface used by the calibrator's direct write method
        if not self.report_limits:
            return None
        return (self.trim_dn_limit, self.trim_up_limit)

    def _apply_trim_event(self, data):
        # MSFS AXIS_* events are sign-inverted relative to the SimVar
        # read-backs, so the "sim" negates the received value.
        return -data / 16383.0

    def _apply_trim_simvar(self, radians):
        # ElevTrimPct read-back normalized per-side against the travel limits
        # (mirror of the calibrator's direct-mode mapping)
        deg = math.degrees(radians)
        return deg / self.trim_up_limit if deg >= 0 else deg / abs(self.trim_dn_limit)

    def _apply_new_trim(self, new_trim):
        new_trim *= self.trim_response_sign
        self.max_trim_step = max(self.max_trim_step, abs(new_trim - self.trim))
        self.trim = new_trim

    # --- plant integration ---
    def step(self, dt):
        # trim is applied by the "sim": the last commanded value on whichever
        # write method this aircraft honors.
        for event, data in self._simconnect.events:
            if event == "AXIS_ELEV_TRIM_SET":
                self.axis_trim_events += 1
                if self.responds_to_axis:
                    self._apply_new_trim(self._apply_trim_event(data))
        self._simconnect.events.clear()
        for simvar, value, units in self._simconnect.sim_data:
            if simvar == "ELEVATOR TRIM POSITION":
                self.simvar_trim_writes += 1
                if self.responds_to_simvar:
                    self._apply_new_trim(self._apply_trim_simvar(value))
        self._simconnect.sim_data.clear()

        trim_eff = self.trim - self.trim_natural
        target_vs = self.K_E * (self.cmd_elev + self.effective_coupling(trim_eff) * trim_eff)
        beta = min(dt / self.vs_tau, 1.0)
        self.vs += (target_vs - self.vs) * beta
        self.roll += (self.K_A * self.cmd_ail + self.roll_bias_rate) * dt
        self.pitch = max(-15.0, min(15.0, self.vs * 2.0))

    def telem(self, **over):
        data = {
            "FFBType": "joystick",
            "SimOnGround": 0,
            "VerticalSpeed": self.vs,
            "Pitch": self.pitch,
            "Roll": self.roll,
            "IAS": self.ias,
            "ElevTrimPct": self.trim,
            "APMaster": 0,
            "APServos": 0,
        }
        data.update(over)
        telem = BaseTelemetryData(initial=data)
        self._telem_data = telem   # TelemManager does this before on_telemetry
        return telem


# --------------------------------------------------------------------------- #
#  Clock helper
# --------------------------------------------------------------------------- #

class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


@pytest.fixture
def clock(monkeypatch):
    """Patch time.perf_counter everywhere (calibrator + utils LPF) with a fake."""
    c = FakeClock()
    monkeypatch.setattr(time, "perf_counter", c)
    return c


def run_to_completion(cal, ac, clock, dt=1 / 30.0, max_frames=40000):
    """Drive the closed loop until the engine reaches a terminal state."""
    for _ in range(max_frames):
        clock.advance(dt)
        cal.update(ac.telem())
        ac.step(dt)
        if cal.state in (CalState.DONE, CalState.ABORT):
            return cal.state
    return cal.state


# --------------------------------------------------------------------------- #
#  Solver
# --------------------------------------------------------------------------- #

class TestSolver:
    def test_fit_linear_exact(self):
        # y = -0.5x + 0.1
        samples = [(x / 10.0, -0.5 * (x / 10.0) + 0.1) for x in range(-4, 5)]
        slope, intercept, r2 = TrimCalibrator._fit(samples)
        assert slope == pytest.approx(-0.5, abs=1e-6)
        assert intercept == pytest.approx(0.1, abs=1e-6)
        assert r2 == pytest.approx(1.0, abs=1e-9)

    def test_fit_curved_low_r2(self):
        # A quadratic is a poor linear fit -> R^2 noticeably below 1.
        samples = [(x / 10.0, (x / 10.0) ** 2) for x in range(-4, 5)]
        _, _, r2 = TrimCalibrator._fit(samples)
        assert r2 < 0.95

    def test_solve_formula(self):
        # virtual_y = 1 + slope/physical_y ; slope=-0.5, physical_y=1.0 -> 0.5
        ac = FakePlantAircraft(physical_y=1.0)
        cal = TrimCalibrator(ac)
        cal._samples = [(t / 10.0, -0.5 * (t / 10.0)) for t in range(-4, 5)]
        cal._sweep_targets = [0]
        cal._solve()
        # Success path transitions to RESTORE (ramp trim back) before DONE.
        assert cal.state == CalState.RESTORE
        assert cal.result["virtual_y"] == pytest.approx(0.5, abs=1e-3)
        assert cal.result["linear_ok"] is True

    def test_solve_formula_scaled_by_physical_y(self):
        # physical_y = 0.5, slope = -0.5 -> virtual_y = 1 + (-0.5/0.5) = 0.0
        ac = FakePlantAircraft(physical_y=0.5)
        cal = TrimCalibrator(ac)
        cal._samples = [(t / 10.0, -0.5 * (t / 10.0)) for t in range(-4, 5)]
        cal._sweep_targets = [0]
        cal._solve()
        assert cal.result["virtual_y"] == pytest.approx(0.0, abs=1e-3)


# --------------------------------------------------------------------------- #
#  Closed loop
# --------------------------------------------------------------------------- #

class TestClosedLoop:
    def test_full_run_recovers_expected_gain(self, clock):
        ac = FakePlantAircraft(coupling=0.5, physical_y=1.0)
        cal = TrimCalibrator(ac)
        cal.start()
        assert cal.active

        state = run_to_completion(cal, ac, clock)

        assert state == CalState.DONE, f"ended in {state} ({cal.abort_reason})"
        assert not cal.active
        # virtual_y = 1 - coupling/physical_y = 0.5
        assert cal.result["virtual_y"] == pytest.approx(0.5, abs=0.05)
        assert cal.result["r_squared"] > 0.95
        # Weak coupling (0.5): the adaptive sweep should expand to the full
        # half-width on both sides (center + 7 + 7 stations).
        assert len(cal.result["samples"]) >= 13

    def test_trim_commands_are_rate_limited(self, clock):
        # No applied trim change may exceed the slew rate per 30 Hz frame —
        # instantaneous trim steps pitch the aircraft violently.
        ac = FakePlantAircraft()
        cal = TrimCalibrator(ac)
        cal.start()
        state = run_to_completion(cal, ac, clock)
        assert state == CalState.DONE
        per_frame_limit = cal.TRIM_RATE_PER_S * (1 / 30.0)
        assert ac.max_trim_step <= per_frame_limit * 1.5 + 2 / 16383.0, (
            f"trim jumped {ac.max_trim_step:.4f} in one frame"
        )

    def test_trim_restored_after_success(self, clock):
        ac = FakePlantAircraft()
        cal = TrimCalibrator(ac)
        cal.start()
        state = run_to_completion(cal, ac, clock)
        assert state == CalState.DONE
        assert ac.trim == pytest.approx(cal.result["trim0"], abs=0.01)

    def test_detects_polarity_and_gain_with_negative_coupling(self, clock):
        # coupling=-0.8, physical_y=1.0 -> virtual_y = 1 - (-0.8) = 1.8
        ac = FakePlantAircraft(coupling=-0.8, physical_y=1.0)
        cal = TrimCalibrator(ac)
        cal.start()
        state = run_to_completion(cal, ac, clock)
        assert state == CalState.DONE, f"ended in {state} ({cal.abort_reason})"
        assert cal.result["virtual_y"] == pytest.approx(1.8, abs=0.06)

    def test_inverted_trim_response_is_auto_corrected(self, clock):
        # Some aircraft respond to trim commands with the opposite sign
        # (cf. the trimwheel_axis_invert user setting). The engine must detect
        # the read-back moving against the command, flip its command sign, and
        # still produce the correct gain.
        ac = FakePlantAircraft(coupling=0.5, physical_y=1.0)
        ac.trim_response_sign = -1.0
        cal = TrimCalibrator(ac)
        cal.start()
        state = run_to_completion(cal, ac, clock)
        assert state == CalState.DONE, f"ended in {state} ({cal.abort_reason})"
        assert cal._trim_sign == -1.0
        assert cal.result["virtual_y"] == pytest.approx(0.5, abs=0.05)

    def test_adaptive_sweep_narrows_for_strong_trim(self, clock):
        # coupling 1.5: each 0.06 trim step costs 0.09 elevator, so the
        # SWEEP_U_BUDGET (0.45) stops expansion well before the half-width cap.
        ac = FakePlantAircraft(coupling=1.5, physical_y=1.0)
        cal = TrimCalibrator(ac)
        cal.start()
        state = run_to_completion(cal, ac, clock)
        assert state == CalState.DONE, f"ended in {state} ({cal.abort_reason})"
        assert cal.result["virtual_y"] == pytest.approx(-0.5, abs=0.05)
        max_off = max(abs(t - cal.result["trim0"]) for t, _ in cal.result["samples"])
        assert max_off < cal.SWEEP_MAX_HALF, "band should be narrower than the cap"

    def test_kinked_trim_response_reported_per_side(self, clock):
        # MSFS normalizes ELEVATOR TRIM PCT per-side (asymmetric up/down
        # limits), so the coupling can genuinely kink at trim=0. The sweep must
        # surface the per-side gains rather than hide them in one straight fit.
        class KinkedTrimPlant(FakePlantAircraft):
            def effective_coupling(self, trim):
                return 0.9 if trim > 0 else 0.3

        ac = KinkedTrimPlant(physical_y=1.0)
        cal = TrimCalibrator(ac)
        cal.start()
        state = run_to_completion(cal, ac, clock)
        assert state == CalState.DONE, f"ended in {state} ({cal.abort_reason})"
        split = cal.result["split"]
        assert split is not None, "expected per-side fit with samples on both sides"
        assert split["virtual_y_above"] == pytest.approx(1 - 0.9, abs=0.07)
        assert split["virtual_y_below"] == pytest.approx(1 - 0.3, abs=0.07)
        assert split["mismatch"] > 0.2

    def test_unresponsive_trim_aborts(self, clock):
        ac = FakePlantAircraft()
        ac.responds_to_simvar = False   # trim never moves
        ac.responds_to_axis = False
        cal = TrimCalibrator(ac)
        cal.start()
        state = run_to_completion(cal, ac, clock)
        assert state == CalState.ABORT
        assert "not responding" in cal.abort_reason

    def test_direct_simvar_write_is_the_default_method(self, clock):
        # Field-tested primary: direct ELEVATOR TRIM POSITION writes succeed
        # across the aircraft the axis event fails on (Just Flight mishandles
        # the event value); the axis event remains a debug-mode selection.
        ac = FakePlantAircraft(coupling=0.5, physical_y=1.0)
        cal = TrimCalibrator(ac)
        cal.start()
        state = run_to_completion(cal, ac, clock)
        assert state == CalState.DONE, f"ended in {state} ({cal.abort_reason})"
        assert ac.simvar_trim_writes > 0
        assert ac.axis_trim_events == 0, "axis event must not be used by default"
        assert cal.result["virtual_y"] == pytest.approx(0.5, abs=0.05)

    def test_axis_event_method_selectable(self, clock):
        ac = FakePlantAircraft(coupling=0.5, physical_y=1.0)
        cal = TrimCalibrator(ac)
        cal.trim_write_method = "axis"
        cal.start()
        state = run_to_completion(cal, ac, clock)
        assert state == CalState.DONE, f"ended in {state} ({cal.abort_reason})"
        assert ac.axis_trim_events > 0
        assert ac.simvar_trim_writes == 0
        assert cal.result["virtual_y"] == pytest.approx(0.5, abs=0.05)

    def test_direct_without_limits_falls_back_to_axis_event(self, clock):
        # No usable travel limits in telemetry: direct cannot map pct to
        # degrees, so the axis event carries the run.
        ac = FakePlantAircraft(coupling=0.5, physical_y=1.0)
        ac.report_limits = False
        cal = TrimCalibrator(ac)
        cal.start()
        state = run_to_completion(cal, ac, clock)
        assert state == CalState.DONE, f"ended in {state} ({cal.abort_reason})"
        assert ac.simvar_trim_writes == 0
        assert ac.axis_trim_events > 0
        assert cal.result["virtual_y"] == pytest.approx(0.5, abs=0.05)


# --------------------------------------------------------------------------- #
#  Diagnostic trace
# --------------------------------------------------------------------------- #

class TestDiagnosticTrace:
    def test_trace_records_frames_and_dumps_csv(self, clock, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        ac = FakePlantAircraft()
        cal = TrimCalibrator(ac)
        cal.trace_enabled = True
        cal.start()
        state = run_to_completion(cal, ac, clock)
        assert state == CalState.DONE
        files = list((tmp_path / "VPForce-TelemFFB" / "log").glob("trimcal_trace_*.csv"))
        assert len(files) == 1
        lines = files[0].read_text().splitlines()
        header = lines[0].split(",")
        assert lines[0] == TrimCalibrator.TRACE_COLUMNS
        assert len(lines) > 100, "a full run must record hundreds of frames"
        # spot-check a data row: state column populated, floats parse
        row = dict(zip(header, lines[1].split(",")))
        assert row["state"] in ("PROBE", "STABILIZE")
        float(row["vs_ms"])
        float(row["u_elev"])
        # rows are cleared after the dump (no growth across runs)
        assert cal._trace_rows == []

class FakeIPC:
    def __init__(self):
        self.broadcasts = []

    def send_broadcast_message(self, msg):
        self.broadcasts.append(msg)


class TestTrimwheelHold:
    def test_hold_broadcast_lifecycle(self, clock):
        # While the run owns the trim, a self-expiring hold must be set
        # locally, broadcast to children, refreshed during the run, and
        # released on completion.
        ac = FakePlantAircraft()
        cal = TrimCalibrator(ac)
        ipc = FakeIPC()
        G.ipc_instance = ipc
        try:
            cal.start()
            clock.advance(1 / 30.0)
            cal.update(ac.telem())
            ac.step(1 / 30.0)
            assert G.trimcal_hold_until > time.perf_counter(), "hold must be active"
            assert ipc.broadcasts[0] == "TRIMCAL HOLD:1"
            state = run_to_completion(cal, ac, clock)
            assert state == CalState.DONE, f"ended in {state} ({cal.abort_reason})"
            assert G.trimcal_hold_until == 0.0, "hold must be released on finish"
            assert ipc.broadcasts[-1] == "TRIMCAL HOLD:0"
            # refreshed ~1/s over a multi-second run, not sent just once
            assert ipc.broadcasts.count("TRIMCAL HOLD:1") >= 3
        finally:
            del G.ipc_instance

    def test_hold_released_on_abort(self, clock):
        ac = FakePlantAircraft()
        ac.responds_to_simvar = False
        ac.responds_to_axis = False
        cal = TrimCalibrator(ac)
        cal.start()
        state = run_to_completion(cal, ac, clock)
        assert state == CalState.ABORT
        assert G.trimcal_hold_until == 0.0, "hold must be released on abort"


# --------------------------------------------------------------------------- #
#  Station-deadline compromise (residual VS: accept 30-60 fpm flagged, abort >60)
# --------------------------------------------------------------------------- #

class TestStationDeadlineCompromise:
    def _run_to_sweep_with_samples(self, ac, cal, clock, min_samples=1):
        cal.start()
        for _ in range(40000):
            clock.advance(1 / 30.0)
            cal.update(ac.telem())
            ac.step(1 / 30.0)
            if cal.state == CalState.SWEEP and len(cal._samples) >= min_samples:
                return
        pytest.fail(f"never reached SWEEP with {min_samples} samples "
                    f"({cal.state}, {cal.abort_reason})")

    def test_residual_vs_in_band_is_accepted_and_flagged(self, clock):
        # A steady 30-60 fpm residual is a finite-gain chase of a drifting
        # target: at the station deadline the best dwell must be accepted,
        # flagged in the result, and the run must still complete.
        ac = FakePlantAircraft()
        cal = TrimCalibrator(ac)
        self._run_to_sweep_with_samples(ac, cal, clock)
        n_before = len(cal._samples)
        last_trim, last_u = cal._samples[-1]
        cal._station_best = (last_trim + 0.03, last_u, 60.0, 0.22)   # ~+43 fpm
        cal._step_settle_deadline = time.perf_counter() - 1.0
        clock.advance(1 / 30.0)
        cal.update(ac.telem())
        ac.step(1 / 30.0)
        assert cal.state != CalState.ABORT, f"aborted: {cal.abort_reason}"
        assert len(cal._samples) == n_before + 1, "compromise sample must be recorded"
        assert len(cal._flagged) == 1
        assert cal._flagged[0]["index"] == n_before
        assert cal._flagged[0]["vs_fpm"] == pytest.approx(0.22 * 196.85, abs=0.5)
        state = run_to_completion(cal, ac, clock)
        assert state == CalState.DONE, f"ended in {state} ({cal.abort_reason})"
        assert cal.result["flagged"], "flag must surface in the result payload"

    def test_residual_vs_above_hard_limit_aborts_with_reason(self, clock):
        ac = FakePlantAircraft()
        cal = TrimCalibrator(ac)
        self._run_to_sweep_with_samples(ac, cal, clock)
        # sit exactly on-station so the abort names the VS gate, not the trim ramp
        cal._current_target = ac.trim
        cal._trim_cmd = ac.trim
        cal._station_best = (ac.trim, 0.0, 60.0, 0.45)   # ~+89 fpm
        cal._step_settle_deadline = time.perf_counter() - 1.0
        clock.advance(1 / 30.0)
        cal.update(ac.telem())
        ac.step(1 / 30.0)
        assert cal.state == CalState.ABORT
        assert "would not settle below 60 fpm" in cal.abort_reason
        assert "+89 fpm" in cal.abort_reason

    def test_station_deadline_names_unreached_trim_target(self, clock):
        # The old message blamed VS regardless of the failing gate; a station
        # whose trim read-back never arrived must say so.
        ac = FakePlantAircraft()
        cal = TrimCalibrator(ac)
        self._run_to_sweep_with_samples(ac, cal, clock)
        cal._current_target = ac.trim + 0.2   # far from the read-back
        cal._station_best = None
        cal._step_settle_deadline = time.perf_counter() - 1.0
        clock.advance(1 / 30.0)
        cal.update(ac.telem())
        ac.step(1 / 30.0)
        assert cal.state == CalState.ABORT
        assert "never reached the station target" in cal.abort_reason

    def test_clean_run_has_no_flags(self, clock):
        ac = FakePlantAircraft()
        cal = TrimCalibrator(ac)
        cal.start()
        state = run_to_completion(cal, ac, clock)
        assert state == CalState.DONE
        assert cal.result["flagged"] == []


# --------------------------------------------------------------------------- #
#  Safety / aborts
# --------------------------------------------------------------------------- #

class TestSafety:
    def _armed(self, clock, **over):
        ac = FakePlantAircraft()
        cal = TrimCalibrator(ac)
        cal.start()
        # one clean frame to establish _last_t
        clock.advance(1 / 30.0)
        cal.update(ac.telem(**over))
        return ac, cal

    def test_abort_on_ap_engaged(self, clock):
        ac, cal = self._armed(clock)
        clock.advance(1 / 30.0)
        cal.update(ac.telem(APMaster=1))
        assert cal.state == CalState.ABORT
        assert not cal.active
        assert "utopilot" in cal.abort_reason

    def test_abort_on_ground(self, clock):
        ac, cal = self._armed(clock)
        clock.advance(1 / 30.0)
        cal.update(ac.telem(SimOnGround=1))
        assert cal.state == CalState.ABORT
        assert not cal.active

    def test_abort_on_pitch_excursion(self, clock):
        # Guard is relative to the attitude at start: first frame captures
        # pitch0 (~0 from the plant), then a large excursion aborts.
        ac, cal = self._armed(clock)
        clock.advance(1 / 30.0)
        cal.update(ac.telem(Pitch=45.0))
        assert cal.state == CalState.ABORT
        assert "Pitch moved" in cal.abort_reason

    def test_no_abort_at_steep_but_stable_deck_angle(self, clock):
        # A trimmed deck angle away from zero must NOT trip the guard as long
        # as the attitude stays near where it started (MSFS pitch is signed
        # nose-down-positive and rarely 0 in trim).
        ac = FakePlantAircraft()
        cal = TrimCalibrator(ac)
        cal.start()
        clock.advance(1 / 30.0)
        cal.update(ac.telem(Pitch=-12.0))   # captures pitch0 = -12
        clock.advance(1 / 30.0)
        cal.update(ac.telem(Pitch=-14.0))   # 2 deg excursion: fine
        assert cal.state != CalState.ABORT

    def test_abort_on_roll_limit(self, clock):
        ac, cal = self._armed(clock)
        clock.advance(1 / 30.0)
        cal.update(ac.telem(Roll=35.0))
        assert cal.state == CalState.ABORT
        assert "Roll" in cal.abort_reason

    def test_polarity_signs_persist_across_runs(self, clock):
        # Control polarity is an aircraft property: a re-run must start from
        # the previously detected signs, not reset to defaults (a wrong-sign
        # prior feeds on residual bank from the previous run's handoff).
        ac = FakePlantAircraft()
        cal = TrimCalibrator(ac)
        cal._elev_sign = -1.0
        cal._ail_sign = -1.0
        cal.start()
        assert cal._elev_sign == -1.0
        assert cal._ail_sign == -1.0

    def test_probe_detects_polarity_despite_roll_drift(self, clock):
        # Aircraft rolling on its own (torque, or residual motion right after
        # a previous run) must not fool the aileron polarity probe: the
        # baseline window measures the drift and the probe response is judged
        # against it.
        ac = FakePlantAircraft()
        ac.roll_bias_rate = -3.5    # rolling left on its own, deg/s
        cal = TrimCalibrator(ac)
        cal.start()
        state = run_to_completion(cal, ac, clock)
        assert cal._ail_sign == 1.0, "drift must not flip the detected sign"
        assert state == CalState.DONE, f"ended in {state} ({cal.abort_reason})"

    def test_can_start_rejects_banked_start(self, clock):
        ac = FakePlantAircraft()
        cal = TrimCalibrator(ac)
        ok, msg = cal.can_start(ac.telem(Roll=15.0))
        assert not ok
        assert "level" in msg.lower()

    def test_force_abort_releases_control(self, clock):
        # force_abort must work from the caller's thread without waiting for
        # another update() frame (there may be no clean frames coming).
        ac = FakePlantAircraft()
        cal = TrimCalibrator(ac)
        cal.start()
        clock.advance(1 / 30.0)
        cal.update(ac.telem())
        assert cal.active
        cal.force_abort("Internal error - see log")
        assert not cal.active
        assert cal.state == CalState.ABORT
        assert "Internal error" in cal.abort_reason

    def test_takeover_pins_stick_where_it_rests(self, clock):
        # The hold spring must pin the stick where it actually IS — a
        # hands-off stick resting at the trim-following center keeps
        # resting there (no snap to 0/0 at start). A stick held deflected
        # is covered by TestHoldSpringContinuity.
        ac = FakePlantAircraft()
        ac.phys_stick = (-100 / 4096, 819 / 4096)   # resting at the old center
        ac.spring_y.cpOffset = 819    # trimmed rest position (~20% of 4096)
        ac.spring_x.cpOffset = -100
        cal = TrimCalibrator(ac)
        cal.start()
        for _ in range(5):
            clock.advance(1 / 30.0)
            cal.update(ac.telem())
            ac.step(1 / 30.0)
        assert ac.spring_y.cpOffset == pytest.approx(819, abs=2)
        assert ac.spring_x.cpOffset == pytest.approx(-100, abs=2)

    def test_takeover_is_bumpless_on_axis_output(self, clock):
        # With trim-following active, the normal path was delivering
        # phys_y - trim*P*(1-V). The calibrator's first elevator command must
        # ride on that baseline (plus the probe nudge), not restart from 0.
        ac = FakePlantAircraft(physical_y=1.0)
        ac.joystick_trim_follow_gain_virtual_y = 0.5
        ac.trim = 0.2                 # aircraft trimmed before starting
        cal = TrimCalibrator(ac)
        cal.start()
        clock.advance(1 / 30.0)
        cal.update(ac.telem())
        # First frames are the probe's no-input baseline window: the command
        # must sit exactly on the takeover baseline (no step, no nudge yet).
        expected_base = 0.0 - 0.2 * 1.0 * (1 - 0.5)   # -0.10
        assert ac.cmd_elev == pytest.approx(expected_base, abs=1e-6)
        # Through the baseline window and into the probe, the command must
        # never jump — it stays on the baseline, then the probe eases in.
        prev = ac.cmd_elev
        for _ in range(int(cal.PROBE_BASELINE_S * 30) + 4):
            clock.advance(1 / 30.0)
            cal.update(ac.telem())
            ac.step(1 / 30.0)
            assert abs(ac.cmd_elev - prev) < cal.PROBE_U * 0.3, "command stepped"
            prev = ac.cmd_elev

    def test_abort_on_telemetry_gap(self, clock):
        ac, cal = self._armed(clock)
        clock.advance(2.0)  # exceeds TELEM_TIMEOUT_S
        cal.update(ac.telem())
        assert cal.state == CalState.ABORT
        assert "telemetry" in cal.abort_reason.lower()

    def test_can_start_requires_airborne(self):
        ac = FakePlantAircraft()
        cal = TrimCalibrator(ac)
        ok, msg = cal.can_start(ac.telem(SimOnGround=1))
        assert not ok
        ok, msg = cal.can_start(ac.telem(SimOnGround=0))
        assert ok, msg

    def test_can_start_rejects_paused_and_slew(self):
        # A frozen frame while paused is otherwise airborne/level/AP-off, so
        # without an explicit check the readiness indicator wrongly says ready.
        ac = FakePlantAircraft()
        cal = TrimCalibrator(ac)
        ok, msg = cal.can_start(ac.telem(SimPaused=1))
        assert not ok and "npause" in msg
        ok, msg = cal.can_start(ac.telem(Slew=1))
        assert not ok and "slew" in msg.lower()

    def test_pause_mid_run_aborts(self, clock):
        ac, cal = self._armed(clock)
        clock.advance(1 / 30.0)
        cal.update(ac.telem(SimPaused=1))
        assert cal.state == CalState.ABORT
        assert "paused" in cal.abort_reason.lower()


# --------------------------------------------------------------------------- #
#  Trim neutralization (settings-independent sweep centering)
# --------------------------------------------------------------------------- #

class TestTrimNeutralization:
    def test_band_centers_on_natural_trim_regardless_of_start(self, clock):
        # Field report: consecutive runs with different starting virtual
        # gains measured different trim regions of a curved response. The
        # neutralization phase must anchor the band at the natural trim point
        # (u ~ 0) no matter where the aircraft was trimmed at start.
        results = []
        for start_trim in (0.4, -0.15):
            ac = FakePlantAircraft(trim_natural=0.1)
            ac.trim = start_trim
            cal = TrimCalibrator(ac)
            cal.start()
            state = run_to_completion(cal, ac, clock)
            assert state == CalState.DONE, f"ended in {state} ({cal.abort_reason})"
            results.append(cal.result)
        # both bands must center on the plant's natural trim point (0.1)
        assert results[0]["trim0"] == pytest.approx(0.1, abs=0.06)
        assert results[1]["trim0"] == pytest.approx(0.1, abs=0.06)
        assert results[0]["virtual_y"] == pytest.approx(results[1]["virtual_y"], abs=0.05)

    def test_neutralization_handles_inverted_trim_response(self, clock):
        # coupling < 0 inverts the u-vs-trim slope. The secant root-find uses
        # the measured local slope, so a wrong initial probe direction self-
        # corrects and it still centers on the natural point (here 0.15).
        ac = FakePlantAircraft(coupling=-0.5, trim_natural=0.15)
        ac.trim = 0.35
        cal = TrimCalibrator(ac)
        cal.start()
        state = run_to_completion(cal, ac, clock)
        assert state == CalState.DONE, f"ended in {state} ({cal.abort_reason})"
        assert cal.result["trim0"] == pytest.approx(0.15, abs=0.05)
        assert cal.result["virtual_y"] == pytest.approx(1.5, abs=0.06)

    def test_neutralization_timeout_sweeps_around_current_point(self, clock):
        # Field crash: the give-up path (timeout / could-not-settle) referenced
        # a misspelled attribute and raised AttributeError instead of falling
        # back. Force the timeout and require a graceful hand-off: the residual
        # steady elevator becomes the sweep baseline and the run still finishes.
        # (The hand-off requires the trim to have proven responsive first.)
        ac = FakePlantAircraft(trim_natural=0.1)
        ac.trim = 0.3
        cal = TrimCalibrator(ac)
        cal.start()
        for _ in range(4000):
            clock.advance(1 / 30.0)
            cal.update(ac.telem())
            ac.step(1 / 30.0)
            if cal.state == CalState.TRIM_NEUTRAL and cal._trim_dir_verified:
                break
        assert cal.state == CalState.TRIM_NEUTRAL and cal._trim_dir_verified
        cal._neut_start_t = time.perf_counter() - cal.NEUT_TIMEOUT_S - 1
        clock.advance(1 / 30.0)
        cal.update(ac.telem())
        ac.step(1 / 30.0)
        assert cal.state not in (CalState.TRIM_NEUTRAL, CalState.ABORT), \
            "timeout must hand off to the sweep, not crash/abort"
        assert cal._u_base_y == pytest.approx(cal._neut_u_final)
        state = run_to_completion(cal, ac, clock)
        assert state == CalState.DONE, f"ended in {state} ({cal.abort_reason})"

    def test_neutralization_giveup_with_deaf_trim_aborts(self, clock):
        # Field failure: a give-up (step could not settle) proceeded to the
        # sweep even though the trim had never responded — the sweep was
        # doomed and only failed later, slowly. Unverified + commanded trim
        # at give-up must abort as unresponsive.
        ac = FakePlantAircraft()
        cal = TrimCalibrator(ac)
        cal.start()
        for _ in range(2000):
            clock.advance(1 / 30.0)
            cal.update(ac.telem())
            ac.step(1 / 30.0)
            if cal.state == CalState.TRIM_NEUTRAL:
                break
        assert cal.state == CalState.TRIM_NEUTRAL
        cal._trim_dir_verified = False
        cal._sign_cmd_accum = cal.TRIM_SIGN_CHECK_DIST   # trim was commanded
        cal._neut_deadline = time.perf_counter() - 1.0
        clock.advance(1 / 30.0)
        # unsettled frame (the deadline path only runs while not settled)
        cal.update(ac.telem(VerticalSpeed=2.0))
        assert cal.state == CalState.ABORT
        assert "not responding" in cal.abort_reason

    def test_neutralization_giveup_before_any_trim_command_aborts_with_settle_reason(self, clock):
        # The first neutralization step settles in place (no trim commanded
        # yet); if the aircraft never calms down, blaming the trim would be
        # wrong — the abort must name the settle problem instead.
        ac = FakePlantAircraft()
        cal = TrimCalibrator(ac)
        cal.start()
        for _ in range(2000):
            clock.advance(1 / 30.0)
            cal.update(ac.telem())
            ac.step(1 / 30.0)
            if cal.state == CalState.TRIM_NEUTRAL:
                break
        assert cal.state == CalState.TRIM_NEUTRAL
        cal._trim_dir_verified = False
        cal._sign_cmd_accum = 0.0                        # nothing commanded yet
        cal._neut_deadline = time.perf_counter() - 1.0
        clock.advance(1 / 30.0)
        # unsettled frame (the deadline path only runs while not settled)
        cal.update(ac.telem(VerticalSpeed=2.0))
        assert cal.state == CalState.ABORT
        assert "could not settle level" in cal.abort_reason

    def test_neutralization_giveup_names_uncommanded_trim_drift(self, clock):
        # Field report: abort said VS -12 fpm — virtually level — because the
        # message blamed VS regardless of the failing gate. When the trim
        # read-back has wandered off the hold point on its own, say THAT.
        ac = FakePlantAircraft()
        cal = TrimCalibrator(ac)
        cal.start()
        for _ in range(2000):
            clock.advance(1 / 30.0)
            cal.update(ac.telem())
            ac.step(1 / 30.0)
            if cal.state == CalState.TRIM_NEUTRAL:
                break
        assert cal.state == CalState.TRIM_NEUTRAL
        cal._trim_dir_verified = False
        cal._sign_cmd_accum = 0.0
        cal._neut_deadline = time.perf_counter() - 1.0
        clock.advance(1 / 30.0)
        # trim read-back far from the hold target, aircraft otherwise level
        cal.update(ac.telem(ElevTrimPct=cal._neut_target + 0.1))
        assert cal.state == CalState.ABORT
        assert "drifted" in cal.abort_reason and "off the hold point" in cal.abort_reason

    def test_abort_during_neutralization_restores_entry_trim(self, clock):
        ac = FakePlantAircraft(trim_natural=0.1)
        ac.trim = 0.3
        cal = TrimCalibrator(ac)
        cal.start()
        # run until the neutralization phase is active
        for _ in range(2000):
            clock.advance(1 / 30.0)
            cal.update(ac.telem())
            ac.step(1 / 30.0)
            if cal.state == CalState.TRIM_NEUTRAL:
                break
        assert cal.state == CalState.TRIM_NEUTRAL
        # let it move the trim a bit, then abort
        for _ in range(60):
            clock.advance(1 / 30.0)
            cal.update(ac.telem())
            ac.step(1 / 30.0)
        cal.stop("test abort")
        clock.advance(1 / 30.0)
        cal.update(ac.telem())
        ac.step(1 / 30.0)
        assert cal.state == CalState.ABORT
        assert ac.trim == pytest.approx(0.3, abs=0.02), \
            "abort during neutralization must restore the entry trim"


# --------------------------------------------------------------------------- #
#  Airspeed-settle hold before the sweep (always on for manual runs)
# --------------------------------------------------------------------------- #

class TestSpeedSettle:
    def _run_tracking_settle(self, cal, ac, clock, max_frames=40000):
        settle_frames = 0
        for _ in range(max_frames):
            clock.advance(1 / 30.0)
            cal.update(ac.telem())
            ac.step(1 / 30.0)
            if cal.state == CalState.SPEED_SETTLE:
                settle_frames += 1
            if cal.state in (CalState.DONE, CalState.ABORT):
                return cal.state, settle_frames
        return cal.state, settle_frames

    def test_manual_run_always_settles_for_full_duration(self, clock):
        # The settle is no longer optional on the manual path: skipping it
        # only ever traded 20 seconds for a drift-skewed slope (the checkbox
        # was removed; assistant handoffs use their own short confirm hold).
        ac = FakePlantAircraft()
        cal = TrimCalibrator(ac)
        cal.start()
        state, settle_frames = self._run_tracking_settle(cal, ac, clock)
        assert state == CalState.DONE, cal.abort_reason
        # ~20 s at 30 fps; allow a little slack for phase-entry framing
        assert settle_frames >= cal.SPEED_SETTLE_S * 30 * 0.95
        assert cal.result["virtual_y"] == pytest.approx(0.5, abs=0.05)


# --------------------------------------------------------------------------- #
#  Pitch-attitude cascade leveling loop
# --------------------------------------------------------------------------- #

class TestPitchCascade:
    def test_cascade_engaged_with_pitch_telemetry(self, clock):
        ac = FakePlantAircraft()
        cal = TrimCalibrator(ac)
        cal.start()
        state = run_to_completion(cal, ac, clock)
        assert state == CalState.DONE, f"ended in {state} ({cal.abort_reason})"
        assert cal._pitch_mode == "cascade"
        assert cal._pitch_sign_known and cal._pitch_sign == 1.0
        assert cal.result["virtual_y"] == pytest.approx(0.5, abs=0.05)

    def test_msfs_style_inverted_pitch_sign(self, clock):
        # MSFS reports pitch nose-up NEGATIVE (X-Plane positive). The probe
        # must detect the polarity and the cascade must calibrate accurately.
        class MsfsPitchPlant(FakePlantAircraft):
            def step(self, dt):
                super().step(dt)
                self.pitch = -self.pitch

        ac = MsfsPitchPlant()
        cal = TrimCalibrator(ac)
        cal.start()
        state = run_to_completion(cal, ac, clock)
        assert state == CalState.DONE, f"ended in {state} ({cal.abort_reason})"
        assert cal._pitch_mode == "cascade"
        assert cal._pitch_sign == -1.0
        assert cal.result["virtual_y"] == pytest.approx(0.5, abs=0.05)

    def test_probe_early_exit_waits_for_ramp_completion(self, clock):
        # Field failure: with the aircraft still surging from a previous
        # aborted run, drift-compensated VS crossed the exit threshold in
        # 0.15-0.22s — before the ramp had applied meaningful input — and the
        # phugoid's dPitch/dVS phase coin-flipped the pitch polarity, railing
        # the cascade. The early exit must not fire before PROBE_RAMP_S.
        ac = FakePlantAircraft(K_E=80.0)   # strong response: exit threshold
        cal = TrimCalibrator(ac)           # is reached well inside the ramp
        cal.start()
        probe_started_t = None
        exit_elapsed = None
        for _ in range(400):
            clock.advance(1 / 30.0)
            cal.update(ac.telem())
            ac.step(1 / 30.0)
            if cal.state != CalState.PROBE or cal._probe_stage != 0:
                break
            if cal._probe_phase == "probe" and probe_started_t is None:
                probe_started_t = clock.t
            if cal._probe_phase == "rampdown" and exit_elapsed is None:
                exit_elapsed = clock.t - probe_started_t
        assert exit_elapsed is not None, "elevator probe never concluded"
        assert exit_elapsed >= cal.PROBE_RAMP_S - 1 / 30.0, \
            f"probe exited {exit_elapsed:.2f}s in, before the ramp completed"
        assert cal._elev_sign == 1.0

    def test_can_start_rejects_climbing_aircraft(self, clock):
        ac = FakePlantAircraft()
        cal = TrimCalibrator(ac)
        ok, msg = cal.can_start(ac.telem(VerticalSpeed=8.0))   # ~1600 fpm
        assert not ok
        assert "Level off" in msg
        ok, _ = cal.can_start(ac.telem(VerticalSpeed=1.0))
        assert ok

    def test_probe_input_is_ramped_not_stepped(self, clock):
        # An abrupt (step) probe rings the phugoid on sensitive aircraft; the
        # probe must ease its input in over several frames, and still detect
        # polarity.
        ac = FakePlantAircraft()
        cal = TrimCalibrator(ac)
        cal.start()
        prev = ac.cmd_elev
        max_jump = 0.0
        for _ in range(400):
            clock.advance(1 / 30.0)
            cal.update(ac.telem())
            ac.step(1 / 30.0)
            # Measure across BOTH the ramp-up and the ramp-down of the
            # elevator probe: neither onset nor release may step.
            if cal.state == CalState.PROBE and cal._probe_stage == 0 \
                    and cal._probe_phase != "baseline":
                max_jump = max(max_jump, abs(ac.cmd_elev - prev))
            prev = ac.cmd_elev
            if cal._probe_stage == 1 or cal.state != CalState.PROBE:
                break
        assert max_jump < cal.PROBE_U * 0.3, f"probe stepped ({max_jump:.3f}); should ramp"
        assert cal._elev_sign == 1.0, "polarity must still be detected from the ramped probe"

    def test_probe_magnitude_scales_with_control_response(self, clock):
        # Minimal control response must gentle the probe too, not just the
        # leveling loop — but never below the response floor.
        ac = FakePlantAircraft()
        cal = TrimCalibrator(ac)
        cal.initial_gain_scale = 0.25
        cal.start()
        peak = 0.0
        for _ in range(400):
            clock.advance(1 / 30.0)
            cal.update(ac.telem())
            ac.step(1 / 30.0)
            if cal.state == CalState.PROBE and cal._probe_stage == 0 \
                    and cal._probe_phase == "probe":
                peak = max(peak, abs(ac.cmd_elev))
            if cal._probe_stage == 1 or cal.state != CalState.PROBE:
                break
        # 0.08 * 0.25 = 0.02, floored at PROBE_U_MIN (0.03); well under full 0.08
        assert peak <= cal.PROBE_U * 0.6, f"derated probe too strong ({peak:.3f})"
        assert peak >= cal.PROBE_U_MIN * 0.8, f"derated probe below floor ({peak:.3f})"

    def test_legacy_fallback_when_pitch_unresponsive(self, clock):
        # No usable pitch response (e.g. bad telemetry): polarity stays
        # unknown, so the safe legacy VS-only loop must fly the run.
        class DeadPitchPlant(FakePlantAircraft):
            def step(self, dt):
                super().step(dt)
                self.pitch = 0.0

        ac = DeadPitchPlant()
        cal = TrimCalibrator(ac)
        cal.start()
        state = run_to_completion(cal, ac, clock)
        assert state == CalState.DONE, f"ended in {state} ({cal.abort_reason})"
        assert cal._pitch_mode == "legacy"
        assert not cal._pitch_sign_known
        assert cal.result["virtual_y"] == pytest.approx(0.5, abs=0.05)


# --------------------------------------------------------------------------- #
#  Oscillation watchdog (adaptive pitch-gain backoff)
# --------------------------------------------------------------------------- #

def _feed_oscillation(cal, peaks, mean=0.0):
    """Drive the watchdog with alternating half-cycles of the given |peak|s
    around ``mean``. Extremum confirmation needs a hysteresis retreat, so each
    peak is followed by a return to the mean; the first two peaks establish
    direction/reference, so n peaks yield n-2 amplitudes."""
    for i, peak in enumerate(peaks):
        sign = 1 if i % 2 == 0 else -1
        cal._watch_oscillation(mean + sign * peak)
        cal._watch_oscillation(mean)


GROWING = [0.5, 0.6, 0.75, 0.95, 1.2]


class TestOscillationWatchdog:
    def test_growing_peaks_back_gains_off(self, clock):
        ac = FakePlantAircraft()
        cal = TrimCalibrator(ac)
        cal.start()
        assert cal._pitch_pid.kp == pytest.approx(cal.PITCH_KP)
        _feed_oscillation(cal, GROWING)
        assert cal._gain_scale == pytest.approx(0.5)
        assert cal._pitch_pid.kp == pytest.approx(cal.PITCH_KP * 0.5)
        assert cal._pitch_pid.ki == pytest.approx(cal.PITCH_KI * 0.5)

    def test_detects_oscillation_on_nonzero_mean(self, clock):
        # The original zero-crossing detector was blind to oscillation riding
        # on a nonzero mean (integrator still converging) — the exact case
        # where a first run diverged undetected until the stabilize timeout.
        ac = FakePlantAircraft()
        cal = TrimCalibrator(ac)
        cal.start()
        _feed_oscillation(cal, GROWING, mean=1.5)   # never crosses zero
        assert cal._gain_scale == pytest.approx(0.5)

    def test_decaying_or_small_peaks_leave_gains_alone(self, clock):
        ac = FakePlantAircraft()
        cal = TrimCalibrator(ac)
        cal.start()
        _feed_oscillation(cal, [0.9, 0.7, 0.55, 0.45, 0.4])   # decaying
        _feed_oscillation(cal, [0.1, 0.2, 0.3])               # below jitter floor
        assert cal._gain_scale == pytest.approx(1.0)

    def test_initial_gain_scale_pre_derates_loop(self, clock):
        # User-selected Control Response: start gentler for known-sensitive
        # aircraft; the watchdog backs off from there if still needed.
        ac = FakePlantAircraft()
        cal = TrimCalibrator(ac)
        cal.initial_gain_scale = 0.5
        cal.start()
        assert cal._gain_scale == pytest.approx(0.5)
        assert cal._pitch_pid.kp == pytest.approx(cal.PITCH_KP * 0.5)
        _feed_oscillation(cal, GROWING)
        assert cal._gain_scale == pytest.approx(0.25)

    def test_backoff_preserves_integral_output_term(self, clock):
        # The integral carries the trim-holding output; backing off must not
        # step it (a step would drop the nose mid-run).
        ac = FakePlantAircraft()
        cal = TrimCalibrator(ac)
        cal.start()
        pid = cal._pitch_pid
        for _ in range(60):
            pid.update(0.5, 1 / 30.0)
        term_before = pid.ki * pid._integral
        cal._set_pitch_gain_scale(0.5)
        term_after = pid.ki * pid._integral
        assert term_after == pytest.approx(term_before)

    def test_persistent_divergence_aborts_at_gain_floor(self, clock):
        ac = FakePlantAircraft()
        cal = TrimCalibrator(ac)
        cal.start()
        clock.advance(1 / 30.0)
        cal.update(ac.telem())   # arm cleanly
        # 0.5^4 = 0.0625 < floor (0.1): the 4th backoff requests the abort.
        for _ in range(4):
            cal._backoff_pitch_gains()
        clock.advance(1 / 30.0)
        cal.update(ac.telem())   # deferred stop is consumed on the next frame
        assert cal.state == CalState.ABORT
        assert "oscillation" in cal.abort_reason.lower()

    def test_stabilize_timeout_with_growth_evidence_backs_off(self, clock):
        # A slow phugoid may not finish 3 half-cycles inside the stabilize
        # window; two growing amplitudes at timeout must trigger a backoff
        # and retry rather than a "could not stabilize" abort.
        ac = FakePlantAircraft()
        cal = TrimCalibrator(ac)
        cal.start()
        clock.advance(1 / 30.0)
        cal.update(ac.telem())
        cal.state = CalState.STABILIZE
        cal._phase_start_t = time.perf_counter() - cal.STABILIZE_TIMEOUT_S - 1
        cal._osc_amps = [0.4, 0.6]
        cal._do_stabilize(ac.telem(), 1 / 30.0)
        assert cal.state == CalState.STABILIZE, "should retry, not abort"
        assert cal._gain_scale == pytest.approx(0.5)

    def test_gain_scale_resets_on_new_run(self, clock):
        ac = FakePlantAircraft()
        cal = TrimCalibrator(ac)
        cal.start()
        _feed_oscillation(cal, GROWING)
        assert cal._gain_scale == pytest.approx(0.5)
        cal.stop()
        clock.advance(1 / 30.0)
        cal.update(ac.telem())
        cal.start()
        assert cal._gain_scale == pytest.approx(1.0)
        assert cal._pitch_pid.kp == pytest.approx(cal.PITCH_KP)


# --------------------------------------------------------------------------- #
#  Calibrated trim curve (non-linear enhancement)
# --------------------------------------------------------------------------- #

class KinkedTrimPlant(FakePlantAircraft):
    """Trim coupling that kinks at neutral, like MSFS per-side normalization."""

    def effective_coupling(self, trim):
        return 0.9 if trim > 0 else 0.3


class TestTrimCurve:
    def test_piecewise_linear_interp_and_edge_extrapolation(self):
        from telemffb.utils import piecewise_linear
        xs = [-0.2, 0.0, 0.2]
        ys = [0.2, 0.0, -0.4]    # slope -1 below zero, -2 above (kinked)
        assert piecewise_linear(xs, ys, -0.1) == pytest.approx(0.1)
        assert piecewise_linear(xs, ys, 0.1) == pytest.approx(-0.2)
        assert piecewise_linear(xs, ys, 0.2) == pytest.approx(-0.4)
        # beyond the band the edge segment's slope continues
        assert piecewise_linear(xs, ys, -0.3) == pytest.approx(0.3)
        assert piecewise_linear(xs, ys, 0.3) == pytest.approx(-0.6)

    def test_solver_emits_zero_referenced_curve(self):
        ac = FakePlantAircraft()
        cal = TrimCalibrator(ac)
        cal._samples = [(-0.2, 0.25), (0.0, 0.05), (0.2, -0.35)]
        cal._sweep_targets = [0]
        cal._station_ias = [60.0]
        cal._solve()
        pts = {p["t"]: p["offs"] for p in cal.result["curve"]["points"]}
        # offs(T) = -(u(T) - u(0)); u(0) = 0.05
        assert pts[-0.2] == pytest.approx(-0.2, abs=1e-3)
        assert pts[0.0] == pytest.approx(0.0, abs=1e-3)
        assert pts[0.2] == pytest.approx(0.4, abs=1e-3)

    def test_curve_property_parsing_and_fallback(self):
        from tests.framework.base import BaseTelemetryEffectTestCase
        from telemffb.sim.msfs_xp.MsfsXpFlightControlsMixIn import MsfsXpFlightControlsMixIn
        import json as _json

        harness = BaseTelemetryEffectTestCase()
        harness.setup_method()
        try:
            inst = harness.create_test_instance(MsfsXpFlightControlsMixIn)

            inst.joystick_trim_follow_curve_y = "none"
            assert inst._trim_curve_offset(0.1) is None

            curve = {"points": [{"t": -0.5, "offs": 0.25}, {"t": 0.5, "offs": -0.25}]}
            inst.joystick_trim_follow_curve_y = _json.dumps(curve)
            assert inst._trim_curve_offset(0.0) == pytest.approx(0.0)
            assert inst._trim_curve_offset(0.5) == pytest.approx(-0.25)
            assert inst.joystick_trim_follow_curve_y == _json.dumps(curve)

            inst.joystick_trim_follow_curve_y = "{not valid json"
            assert inst._trim_curve_offset(0.1) is None
            inst.joystick_trim_follow_curve_y = '{"points": [{"t": 0.0, "offs": 0.0}]}'
            assert inst._trim_curve_offset(0.1) is None, "single-point curve must be ignored"
        finally:
            harness.teardown_method()

    def test_virtual_offset_selects_curve_or_legacy(self):
        from tests.framework.base import BaseTelemetryEffectTestCase
        from telemffb.sim.msfs_xp.MsfsXpFlightControlsMixIn import MsfsXpFlightControlsMixIn
        import json as _json

        harness = BaseTelemetryEffectTestCase()
        harness.setup_method()
        try:
            inst = harness.create_test_instance(MsfsXpFlightControlsMixIn)
            # Explicit: the expected values below assume unity gain.
            inst.joystick_trim_follow_gain_physical_y = 1.0
            inst.joystick_trim_follow_gain_virtual_y = 0.5

            # legacy formula when no curve / disabled
            assert inst._trim_follow_virtual_offset_y(0.2, 0.2) == pytest.approx(0.1)

            curve = {"points": [{"t": -0.5, "offs": 0.4}, {"t": 0.5, "offs": -0.4}]}
            inst.joystick_trim_follow_curve_y = _json.dumps(curve)
            inst.joystick_trim_follow_use_curve_y = False
            assert inst._trim_follow_virtual_offset_y(0.2, 0.2) == pytest.approx(0.1)

            inst.joystick_trim_follow_use_curve_y = True
            assert inst._trim_follow_virtual_offset_y(0.2, 0.2) == pytest.approx(-0.16)
        finally:
            harness.teardown_method()

    def test_curve_enabled_without_calibration_flags_error(self):
        from tests.framework.base import BaseTelemetryEffectTestCase
        from telemffb.sim.msfs_xp.MsfsXpFlightControlsMixIn import MsfsXpFlightControlsMixIn

        harness = BaseTelemetryEffectTestCase()
        harness.setup_method()
        try:
            inst = harness.create_test_instance(MsfsXpFlightControlsMixIn)
            inst.joystick_trim_follow_gain_virtual_y = 0.5
            inst.joystick_trim_follow_use_curve_y = True
            inst.joystick_trim_follow_curve_y = "none"

            # Falls back to the static gain and raises a UI notification.
            assert inst._trim_follow_virtual_offset_y(0.2, 0.2) == pytest.approx(0.1)
            errors = getattr(inst, "_flagged_errors", [])
            assert any("no calibration is stored" in e for e in errors)

            # With a curve loaded, no error is flagged.
            import json as _json
            inst._flagged_errors = []
            inst.joystick_trim_follow_curve_y = _json.dumps(
                {"points": [{"t": -0.5, "offs": 0.4}, {"t": 0.5, "offs": -0.4}]})
            inst._trim_follow_virtual_offset_y(0.2, 0.2)
            assert inst._flagged_errors == []
        finally:
            harness.teardown_method()

    def test_solver_curve_includes_natural_trim_anchor(self):
        ac = FakePlantAircraft()
        cal = TrimCalibrator(ac)
        cal._samples = [(-0.2, 0.25), (0.0, 0.05), (0.2, -0.35)]
        cal._sweep_targets = [0]
        cal._station_ias = [60.0]
        cal._trim0 = -0.123
        cal._solve()
        assert cal.result["curve"]["t0"] == pytest.approx(-0.123, abs=1e-4)

    def test_center_walks_curve_in_axis_units(self):
        # The Hawk lesson: force relief must happen at the measured
        # trim-vs-elevator authority rate. A slope-19 aircraft's spring
        # center must walk ~19x the raw trim movement (clamped), while
        # legacy mode keeps the raw-trim center.
        from tests.framework.base import BaseTelemetryEffectTestCase
        from telemffb.sim.msfs_xp.MsfsXpFlightControlsMixIn import MsfsXpFlightControlsMixIn
        import json as _json

        harness = BaseTelemetryEffectTestCase()
        harness.setup_method()
        try:
            inst = harness.create_test_instance(MsfsXpFlightControlsMixIn)
            inst.joystick_trim_follow_gain_physical_y = 1.0
            curve = {"points": [{"t": -0.05, "offs": -0.95},
                                {"t": 0.05, "offs": 0.95}],
                     "t0": 0.0}
            inst.joystick_trim_follow_curve_y = _json.dumps(curve)

            # Legacy mode: center is the raw-trim center regardless of curve.
            inst.joystick_trim_follow_use_curve_y = False
            assert inst._trim_follow_center_y(0.01, 0.19) == pytest.approx(0.01)

            inst.joystick_trim_follow_use_curve_y = True
            offs = inst._trim_curve_offset(0.01)
            assert offs == pytest.approx(0.19)
            # 1% trim -> 19% center walk (t0=0 anchor contributes nothing).
            assert inst._trim_follow_center_y(0.01, offs) == pytest.approx(0.19)
            # Stick travel scales the walk (the CENTER only). Curve mode uses
            # its own setting, NOT the legacy signed physical gain.
            inst.joystick_trim_follow_gain_physical_y = 0.25   # legacy: ignored
            inst.joystick_trim_follow_curve_gain_y = 0.5
            assert inst._trim_follow_center_y(0.01, offs) == pytest.approx(0.095)
            inst.joystick_trim_follow_gain_physical_y = 1.0
            # Beyond the band the walk clamps at full deflection.
            inst.joystick_trim_follow_curve_gain_y = 1.0
            offs_far = inst._trim_curve_offset(0.10)
            assert inst._trim_follow_center_y(0.10, offs_far) == pytest.approx(1.0)
        finally:
            harness.teardown_method()

    def test_delivered_datum_is_never_scaled_by_physical_gain(self):
        # THE primary invariant: trim with the stick HELD and the nose must
        # not move. The delivered input is (stick - virtual_offset), and the
        # virtual offset is the measured elevator-equivalent of the trim, so
        # subtracting it cancels the trim's effect EXACTLY. Scaling it by the
        # physical gain cancels gain-times the effect: at 200% physical,
        # trimming drove the nose the wrong way (field report 2026-07-26).
        # The gain may move the spring CENTER; it must never touch this.
        from tests.framework.base import BaseTelemetryEffectTestCase
        from telemffb.sim.msfs_xp.MsfsXpFlightControlsMixIn import MsfsXpFlightControlsMixIn
        import json as _json

        harness = BaseTelemetryEffectTestCase()
        harness.setup_method()
        try:
            inst = harness.create_test_instance(MsfsXpFlightControlsMixIn)
            inst.joystick_trim_follow_use_curve_y = True
            inst.joystick_trim_follow_curve_y = _json.dumps(
                {"points": [{"t": -0.5, "offs": -0.2}, {"t": 0.5, "offs": 0.2}],
                 "t0": 0.0})

            baseline = {t: inst._trim_curve_offset(t)
                        for t in (-0.4, -0.1, 0.0, 0.25, 0.45)}
            for gain in (0.25, 0.5, 1.0, 1.5, 2.0):
                inst.joystick_trim_follow_gain_physical_y = gain
                for t, want in baseline.items():
                    got = inst._trim_follow_virtual_offset_y(t, clamp(t * gain, -1, 1), None)
                    assert got == pytest.approx(want, abs=1e-12), \
                        f"gain {gain}: datum moved with gain ({got} != {want})"

                # Held stick: the delivered input must track the elevator the
                # aircraft needs at each trim, so trimming leaves pitch alone.
                held = 0.0
                for t in (0.1, 0.2, 0.3):
                    delivered = held - inst._trim_follow_virtual_offset_y(
                        t, clamp(t * gain, -1, 1), None)
                    assert delivered == pytest.approx(-inst._trim_curve_offset(t),
                                                      abs=1e-12)
        finally:
            harness.teardown_method()

    def test_curve_stick_travel_is_its_own_setting(self):
        # Curve mode reads joystick_trim_follow_curve_gain_y (0..200%), never
        # the legacy signed joystick_trim_follow_gain_physical_y (-100..100).
        # A user with an inverted or reduced legacy gain must not have it
        # silently applied to a calibrated curve.
        from tests.framework.base import BaseTelemetryEffectTestCase
        from telemffb.sim.msfs_xp.MsfsXpFlightControlsMixIn import MsfsXpFlightControlsMixIn
        import json as _json

        harness = BaseTelemetryEffectTestCase()
        harness.setup_method()
        try:
            inst = harness.create_test_instance(MsfsXpFlightControlsMixIn)
            inst.joystick_trim_follow_use_curve_y = True
            inst.joystick_trim_follow_curve_y = _json.dumps(
                {"points": [{"t": -0.5, "offs": -0.2}, {"t": 0.5, "offs": 0.2}],
                 "t0": 0.0})
            offs = inst._trim_curve_offset(0.5)
            assert offs == pytest.approx(0.2)

            # Legacy gain swept (including inverted): curve center unmoved.
            inst.joystick_trim_follow_curve_gain_y = 1.0
            for legacy in (-1.0, 0.0, 0.3, 2.0):
                inst.joystick_trim_follow_gain_physical_y = legacy
                assert inst._trim_follow_center_y(0.5, offs) == pytest.approx(0.2), \
                    f"legacy gain {legacy} leaked into curve mode"

            # The new setting is what scales it — including above 100%.
            for travel, want in ((0.0, 0.0), (0.5, 0.1), (1.0, 0.2), (2.0, 0.4)):
                inst.joystick_trim_follow_curve_gain_y = travel
                assert inst._trim_follow_center_y(0.5, offs) == pytest.approx(want)

            # Legacy mode still uses the legacy gain (via the caller's
            # elev_trim), untouched by the new setting.
            inst.joystick_trim_follow_use_curve_y = False
            inst.joystick_trim_follow_curve_gain_y = 2.0
            assert inst._trim_follow_center_y(0.35, offs) == pytest.approx(0.35)
        finally:
            harness.teardown_method()

    def test_anchor_referencing_rebases_curve_at_natural_trim(self):
        # The curve is stored zero-referenced at trim-gauge 0 but flown
        # anchor-referenced: the parse-time rebase pins offs(t0) == 0, so
        # "trimmed for level" means stick at physical center, zero force,
        # zero delivered input — and no lookup ever depends on extrapolating
        # a narrow band back to gauge zero.
        from tests.framework.base import BaseTelemetryEffectTestCase
        from telemffb.sim.msfs_xp.MsfsXpFlightControlsMixIn import MsfsXpFlightControlsMixIn
        import json as _json

        harness = BaseTelemetryEffectTestCase()
        harness.setup_method()
        try:
            inst = harness.create_test_instance(MsfsXpFlightControlsMixIn)
            inst.joystick_trim_follow_gain_physical_y = 1.0
            inst.joystick_trim_follow_use_curve_y = True

            # Off-zero natural point (SR22T-like, slope 2.2, anchor t0=-0.1;
            # points as the solver stores them: zero-referenced at T=0).
            curve = {"points": [{"t": -0.37, "offs": -0.814},
                                {"t": 0.03, "offs": 0.066}],
                     "t0": -0.1}
            inst.joystick_trim_follow_curve_y = _json.dumps(curve)
            # Trimmed for level: zero virtual offset (delivered = phys) and
            # the spring center at physical center.
            assert inst._trim_curve_offset(-0.1) == pytest.approx(0.0, abs=1e-9)
            assert inst._trim_follow_center_y(-0.1, 0.0) == pytest.approx(0.0, abs=1e-9)
            # The walk away from the anchor still moves at the measured slope.
            offs_near = inst._trim_curve_offset(-0.2)
            assert offs_near == pytest.approx(-0.22, abs=1e-3)
            assert inst._trim_follow_center_y(-0.2, offs_near) == \
                pytest.approx(-0.22, abs=1e-3)
            # Stick travel scales the walk but leaves the anchor at center.
            inst.joystick_trim_follow_curve_gain_y = 0.5
            assert inst._trim_follow_center_y(-0.1, 0.0) == pytest.approx(0.0, abs=1e-9)
            assert inst._trim_follow_center_y(-0.2, offs_near) == \
                pytest.approx(-0.11, abs=1e-3)
            inst.joystick_trim_follow_curve_gain_y = 1.0

            # Curves saved before t0 existed rebase at the band midpoint.
            curve_old = {"points": [{"t": -0.37, "offs": -0.814},
                                    {"t": 0.03, "offs": 0.066}]}
            inst.joystick_trim_follow_curve_y = _json.dumps(curve_old)
            mid = (-0.37 + 0.03) / 2.0
            assert inst._trim_curve_offset(mid) == pytest.approx(0.0, abs=1e-9)

            # The rebase is idempotent: feeding back already-rebased points
            # (offs(t0) == 0) changes nothing.
            rebased = {"points": [{"t": t, "offs": o} for t, o in
                                  zip(*inst._trim_curve_y_pts)]}
            inst.joystick_trim_follow_curve_y = _json.dumps(rebased)
            assert inst._trim_curve_offset(mid) == pytest.approx(0.0, abs=1e-9)
            assert inst._trim_curve_offset(0.03) == pytest.approx(0.44, abs=1e-3)

            # Standard aircraft (slope ~1, t0~0): rebase is a no-op and the
            # center reduces to the raw-trim center — imperceptible change.
            curve_std = {"points": [{"t": -0.4, "offs": -0.41},
                                    {"t": 0.4, "offs": 0.41}],
                         "t0": 0.0}
            inst.joystick_trim_follow_curve_y = _json.dumps(curve_std)
            for t in (-0.3, -0.1, 0.2):
                offs = inst._trim_curve_offset(t)
                assert inst._trim_follow_center_y(t, offs) == pytest.approx(t, abs=0.02)
        finally:
            harness.teardown_method()

    def test_family_blend_through_runtime_offset_helper(self):
        # Two-speed family on a real mixin instance: the runtime helper must
        # blend by the frame's IAS, hold the last speed on an IAS-dropout
        # frame, and honor the trimmed-stick-position mode.
        from tests.framework.base import BaseTelemetryEffectTestCase
        from telemffb.sim.msfs_xp.MsfsXpFlightControlsMixIn import MsfsXpFlightControlsMixIn
        import json as _json

        def frame(ias_kt):
            return BaseTelemetryData(initial={"IAS": ias_kt / 1.94384})

        harness = BaseTelemetryEffectTestCase()
        harness.setup_method()
        try:
            inst = harness.create_test_instance(MsfsXpFlightControlsMixIn)
            # Explicit: the expected values below assume unity gain.
            inst.joystick_trim_follow_gain_physical_y = 1.0
            inst.joystick_trim_follow_use_curve_y = True
            # 100 kt: slope 2 anchored at +0.1; 200 kt: slope 2 anchored at
            # -0.1 (SR22T-style translation). r = [+0.4, 0].
            inst.joystick_trim_follow_curve_y = _json.dumps({"curves": [
                {"ias_kt": 100, "t0": 0.1,
                 "points": [{"t": -0.1, "offs": -0.2}, {"t": 0.3, "offs": 0.6}]},
                {"ias_kt": 200, "t0": -0.1,
                 "points": [{"t": -0.3, "offs": -0.6}, {"t": 0.1, "offs": 0.2}]},
            ]})

            # follows-trim (default): S(T - t0(v)) + R(v)
            assert inst._trim_follow_virtual_offset_y(0.1, 0.0, frame(100)) == \
                pytest.approx(0.4, abs=1e-9)   # at own anchor: 0 + r=0.4
            assert inst._trim_follow_virtual_offset_y(0.0, 0.0, frame(150)) == \
                pytest.approx(0.2, abs=1e-9)   # mid: S(0)=0 + R=0.2
            # IAS-dropout frame holds the last valid speed (150 kt)
            assert inst._trim_follow_virtual_offset_y(0.0, 0.0, frame(0)) == \
                pytest.approx(0.2, abs=1e-9)
            assert inst._trim_follow_virtual_offset_y(0.0, 0.0, None) == \
                pytest.approx(0.2, abs=1e-9)

            # centered mode: R suppressed, pure aligned shape
            inst.joystick_trim_follow_stick_position = "Stays Centered"
            assert inst._trim_follow_virtual_offset_y(0.1, 0.0, frame(100)) == \
                pytest.approx(0.0, abs=1e-9)
            assert inst._trim_follow_virtual_offset_y(0.1, 0.0, frame(150)) == \
                pytest.approx(0.2, abs=1e-9)   # S(0.1 - 0.0) = 2*0.1
        finally:
            harness.teardown_method()

    def test_single_entry_family_matches_legacy_lookup(self):
        # One stored curve must behave exactly as the single-curve runtime
        # did — speed-independent, both position modes identical.
        from tests.framework.base import BaseTelemetryEffectTestCase
        from telemffb.sim.msfs_xp.MsfsXpFlightControlsMixIn import MsfsXpFlightControlsMixIn
        import json as _json

        harness = BaseTelemetryEffectTestCase()
        harness.setup_method()
        try:
            inst = harness.create_test_instance(MsfsXpFlightControlsMixIn)
            # Explicit: the expected values below assume unity gain.
            inst.joystick_trim_follow_gain_physical_y = 1.0
            inst.joystick_trim_follow_use_curve_y = True
            inst.joystick_trim_follow_curve_y = _json.dumps(
                {"points": [{"t": -0.37, "offs": -0.814}, {"t": 0.03, "offs": 0.066}],
                 "t0": -0.1, "ias_kt": 132.0})
            for t in (-0.3, -0.1, 0.0):
                expect = inst._trim_curve_offset(t)
                for td in (BaseTelemetryData(initial={"IAS": 60.0}), None):
                    assert inst._trim_follow_virtual_offset_y(t, 0.0, td) == \
                        pytest.approx(expect, abs=1e-9)
            inst.joystick_trim_follow_stick_position = "Stays Centered"
            assert inst._trim_follow_virtual_offset_y(-0.3, 0.0, None) == \
                pytest.approx(inst._trim_curve_offset(-0.3), abs=1e-9)
        finally:
            harness.teardown_method()

    def test_takeover_baseline_mirrors_runtime_offset_helper(self):
        # Field abort (C208B, trace 20260719_201957): the takeover baseline
        # was computed with the static-gain formula even when the runtime was
        # flying the calibrated curve. The false baseline stepped the
        # delivered axis at t=0, upset the aircraft, and contaminated the
        # polarity probe into a wrong-sign cascade dive. The capture must
        # mirror the runtime's own offset helper, exactly like the handback
        # continuity targets do.
        ac = FakePlantAircraft()
        ac.trim = -0.0565
        # Stick resting at the curve-mode spring center; the runtime's curve
        # lookup says the virtual offset there is -0.4 (anchor-referenced).
        ac.phys_stick = (0.0, -0.4)
        ac._trim_follow_virtual_offset_y = lambda t, elev_trim, telem_data=None: -0.4
        cal = TrimCalibrator(ac)
        cal._capture_takeover_baseline(ac.telem())
        # Runtime was delivering phys - offs = -0.4 - (-0.4) = 0: bumpless.
        assert cal._u_base_y == pytest.approx(0.0, abs=1e-9)

        # Without the helper (plants/aircraft without curve support) the
        # legacy static formula still applies: phys - T*P*(1-vy).
        ac2 = FakePlantAircraft()
        ac2.trim = -0.0565
        ac2.phys_stick = (0.0, -0.4)
        cal2 = TrimCalibrator(ac2)
        cal2._capture_takeover_baseline(ac2.telem())
        expected = -0.4 - (-0.0565 * 1.0 * (1 - 0.5))
        assert cal2._u_base_y == pytest.approx(expected, abs=1e-9)

    def test_curve_cancels_kinked_coupling_where_static_cannot(self, clock):
        # The user-reported failure: with a kinked trim response, the static
        # gain holds the nose only near the fit tangent — trimming across the
        # band walks the nose away. The calibrated curve must cancel the
        # coupling everywhere the sweep measured.
        from telemffb.utils import piecewise_linear

        ac = KinkedTrimPlant(physical_y=1.0)
        cal = TrimCalibrator(ac)
        cal.start()
        state = run_to_completion(cal, ac, clock)
        assert state == CalState.DONE, f"ended in {state} ({cal.abort_reason})"

        curve = cal.result["curve"]
        xs = [p["t"] for p in curve["points"]]
        ys = [p["offs"] for p in curve["points"]]
        vy = cal.result["virtual_y"]

        def required_delta(t):
            # delivered-axis change (vs trim 0) needed to hold level: u = -c(t)*t
            return -ac.effective_coupling(t) * t

        ts = [i / 100.0 for i in range(-40, 41, 2)]
        # stick held: delivered change = -offs(t); residual vs required must be
        # ~constant for the nose to hold while trimming continuously.
        resid_curve = [-piecewise_linear(xs, ys, t) - required_delta(t) for t in ts]
        resid_static = [-t * 1.0 * (1 - vy) - required_delta(t) for t in ts]

        def spread(r):
            return max(r) - min(r)

        assert spread(resid_curve) < 0.02, "curve must hold level across the band"
        assert spread(resid_static) > 0.05, "static gain should fail on a kinked plant"


# --------------------------------------------------------------------------- #
#  Suppression of normal flight controls while active
# --------------------------------------------------------------------------- #

class _DummyActiveCalibrator:
    def __init__(self):
        self.active = True
        self.updated = 0

    def update(self, telem_data):
        self.updated += 1


class _CrashingCalibrator:
    def __init__(self):
        self.active = True
        self.aborted_with = None

    def update(self, telem_data):
        raise RuntimeError("boom")

    def force_abort(self, reason):
        self.aborted_with = reason
        self.active = False


class TestMultiSpeedEndToEnd:
    """Two REAL engine calibrations at different (coupling, anchor, speed)
    points -> solver payloads -> family JSON -> blend: the full phase-3
    pipeline in the exact formats each stage stores and consumes."""

    def _calibrate(self, clock, coupling, t_nat, ias_ms):
        ac = FakePlantAircraft(coupling=coupling, trim_natural=t_nat)
        ac.ias = ias_ms
        ac.trim = t_nat
        ac.joystick_trim_follow_gain_virtual_y = 1.0
        cal = TrimCalibrator(ac)
        cal.start()
        state = run_to_completion(cal, ac, clock)
        assert state == CalState.DONE, f"ended {state} ({cal.abort_reason})"
        return cal.result["curve"]

    def test_family_from_real_runs_blends_between_speeds(self, clock):
        import json as _json
        from telemffb.utils import parse_trim_follow_family, trim_follow_blend

        c_slow = self._calibrate(clock, coupling=2.0, t_nat=0.1, ias_ms=50.0)
        c_fast = self._calibrate(clock, coupling=4.0, t_nat=-0.1, ias_ms=100.0)
        fam = parse_trim_follow_family(_json.dumps({"curves": [c_slow, c_fast]}))
        assert len(fam) == 2 and fam[0]["ias_kt"] < fam[1]["ias_kt"]

        v_mid = (fam[0]["ias_kt"] + fam[1]["ias_kt"]) / 2.0
        t0_mid = (fam[0]["t0"] + fam[1]["t0"]) / 2.0
        h = 0.02
        # Local gain at the blended anchor must be the lerped coupling (3.0)
        s = (trim_follow_blend(fam, t0_mid + h, v_mid, include_r=False)
             - trim_follow_blend(fam, t0_mid - h, v_mid, include_r=False)) / (2 * h)
        assert s == pytest.approx(3.0, rel=0.05)
        # ...and the blend must cross zero at the blended anchor: trimmed for
        # level at the intermediate speed = zero delivered input.
        assert trim_follow_blend(fam, t0_mid, v_mid, include_r=False) == \
            pytest.approx(0.0, abs=0.02)

        # Motivation guard: the single wrong-speed curve fails the same check.
        fam_slow_only = parse_trim_follow_family(_json.dumps(c_slow))
        s1 = (trim_follow_blend(fam_slow_only, t0_mid + h, v_mid)
              - trim_follow_blend(fam_slow_only, t0_mid - h, v_mid)) / (2 * h)
        assert abs(s1 - 3.0) > 0.5, \
            "a single-speed curve must not accidentally pass the mid-speed check"


class TestSuppression:
    def test_active_calibrator_bypasses_flight_controls(self):
        from tests.framework.base import BaseTelemetryEffectTestCase
        from telemffb.sim.msfs_xp.MsfsXpFlightControlsMixIn import MsfsXpFlightControlsMixIn

        harness = BaseTelemetryEffectTestCase()
        harness.setup_method()
        try:
            instance = harness.create_test_instance(MsfsXpFlightControlsMixIn)
            instance._test_sim_is_msfs = True
            instance.telemffb_controls_axes = True
            instance._simconnect = harness.mock_simconnect

            dummy = _DummyActiveCalibrator()
            instance._trim_calibrator = dummy

            telem = BaseTelemetryData(initial={"FFBType": "joystick", "APMaster": 0})
            instance.msfs_update_flight_controls(telem)

            assert dummy.updated == 1
            # Early return means no axis events were sent to the sim.
            assert not any(
                "AXIS" in ev for ev, _ in harness.mock_simconnect.sent_events
            )
        finally:
            harness.teardown_method()

    def test_calibrator_crash_does_not_kill_telemetry_loop(self):
        # dev_guidelines "Error Handling": an exception in the telemetry hot
        # path kills the processing loop. A calibrator bug must be contained
        # by the hook, force-abort the run, and release control.
        from tests.framework.base import BaseTelemetryEffectTestCase
        from telemffb.sim.msfs_xp.MsfsXpFlightControlsMixIn import MsfsXpFlightControlsMixIn

        harness = BaseTelemetryEffectTestCase()
        harness.setup_method()
        try:
            instance = harness.create_test_instance(MsfsXpFlightControlsMixIn)
            instance._test_sim_is_msfs = True
            instance.telemffb_controls_axes = True
            instance._simconnect = harness.mock_simconnect

            crash = _CrashingCalibrator()
            instance._trim_calibrator = crash

            telem = BaseTelemetryData(initial={"FFBType": "joystick", "APMaster": 0})
            instance.msfs_update_flight_controls(telem)   # must not raise

            assert crash.aborted_with is not None, "hook must force-abort on crash"
            assert crash.active is False, "control must be released"
        finally:
            harness.teardown_method()


# --------------------------------------------------------------------------- #
#  Trim assistant (start(assist=True) -> ASSIST_HOLD -> begin_sweep())
# --------------------------------------------------------------------------- #

def run_until(cal, ac, clock, pred, seconds, dt=1 / 30.0):
    """Drive the closed loop until ``pred()`` is true (or the time budget or
    the run ends); returns the predicate's final value."""
    for _ in range(int(seconds / dt)):
        clock.advance(dt)
        cal.update(ac.telem())
        ac.step(dt)
        if pred() or not cal.active:
            break
    return pred()


class TestTrimAssistant:
    def _start_assist(self, clock, **plant_kw):
        ac = FakePlantAircraft(**plant_kw)
        cal = TrimCalibrator(ac)
        cal.start(assist=True)
        return ac, cal

    def test_assist_reaches_hold_and_reports_stable(self, clock):
        ac, cal = self._start_assist(clock, coupling=0.5, trim_natural=0.1)
        assert run_until(cal, ac, clock,
                         lambda: cal.state == CalState.ASSIST_HOLD, 120), \
            f"never reached ASSIST_HOLD ({cal.state}, {cal.abort_reason})"
        assert not cal.assist_stable, "must not report stable before the calm window"
        assert run_until(cal, ac, clock, lambda: cal.assist_stable, 60), \
            "assistant never reported stable"
        # Holding at the natural trim point, level, with no sweep started.
        assert ac.trim == pytest.approx(0.1, abs=0.05)
        assert abs(ac.vs) < 0.5
        assert cal.state == CalState.ASSIST_HOLD
        assert cal._samples == []

    def test_assist_retrims_after_power_change(self, clock):
        ac, cal = self._start_assist(clock, coupling=0.5, trim_natural=0.1)
        assert run_until(cal, ac, clock, lambda: cal.assist_stable, 180)

        # "Power change": the level trim point moves; the disturbance shows up
        # as a VS excursion the assistant must chase back down.
        ac.trim_natural = 0.30
        assert run_until(cal, ac, clock, lambda: not cal.assist_stable, 5), \
            "stability flag must clear as soon as the disturbance is seen"
        assert run_until(cal, ac, clock, lambda: cal.assist_stable, 240), \
            f"assistant never re-trimmed (trim {ac.trim:+.3f}, vs {ac.vs:+.2f})"
        assert ac.trim == pytest.approx(0.30, abs=0.05)
        assert cal.state == CalState.ASSIST_HOLD

    def test_assist_stable_clears_immediately_on_disturbance(self, clock):
        ac, cal = self._start_assist(clock, coupling=0.5)
        assert run_until(cal, ac, clock, lambda: cal.assist_stable, 180)
        # Asymmetric hysteresis: one disturbed frame clears the flag at once.
        ac.vs = 2.0
        clock.advance(1 / 30.0)
        cal.update(ac.telem())
        assert not cal.assist_stable

    def test_assist_begin_sweep_completes_calibration(self, clock):
        ac, cal = self._start_assist(clock, coupling=0.5, physical_y=1.0)
        assert run_until(cal, ac, clock, lambda: cal.assist_stable, 180)

        cal.begin_sweep()
        state = run_to_completion(cal, ac, clock)
        assert state == CalState.DONE, f"ended in {state} ({cal.abort_reason})"
        # Polarity and neutral trim carried over from the assistant; the
        # sweep still measures the same plant coupling.
        assert cal.result["virtual_y"] == pytest.approx(0.5, abs=0.05)
        assert cal.result["r_squared"] > 0.95

    def test_assist_begin_sweep_ignored_outside_hold(self, clock):
        ac, cal = self._start_assist(clock, coupling=0.5)
        # Still probing/stabilizing: the request must be a no-op.
        cal.begin_sweep()
        assert not cal._sweep_requested

    def test_assist_cancel_keeps_current_trim(self, clock):
        # Cancelling the assistant must NOT snap trim back to the pre-assist
        # value: the held trim IS the level trim for the power the user chose.
        ac, cal = self._start_assist(clock, coupling=0.5, trim_natural=0.2)
        assert run_until(cal, ac, clock, lambda: cal.assist_stable, 180)
        held_trim = ac.trim
        assert held_trim == pytest.approx(0.2, abs=0.05)

        cal.stop("Cancelled by user")
        # Soft cancel settles the stick at the release-continuity position
        # for ASSIST_CANCEL_SETTLE_S before handing back.
        clock.advance(1 / 30.0)
        cal.update(ac.telem())
        ac.step(1 / 30.0)
        assert cal.active, "soft cancel must settle briefly before release"
        assert run_until(cal, ac, clock, lambda: not cal.active, 5)
        assert cal.state == CalState.ABORT
        assert ac.trim == pytest.approx(held_trim, abs=0.01), \
            "cancel from the hold must leave the current (level) trim in place"

    def test_assist_abort_during_sweep_restores_natural_trim(self, clock):
        # Once the sweep has started, an abort restores the sweep anchor
        # (the natural trim at the chosen speed) exactly like a normal run.
        ac, cal = self._start_assist(clock, coupling=0.5, trim_natural=0.1)
        assert run_until(cal, ac, clock, lambda: cal.assist_stable, 180)
        cal.begin_sweep()
        assert run_until(cal, ac, clock,
                         lambda: cal.state == CalState.SWEEP, 60)
        anchor = cal._trim0
        cal.stop("Cancelled by user")
        run_until(cal, ac, clock, lambda: not cal.active, 5)
        assert ac.trim == pytest.approx(anchor, abs=0.05)

    def test_assist_unresponsive_trim_in_hold_aborts(self, clock):
        # Neutralization completes without commanding trim (already natural);
        # the trim then goes deaf and a power change forces the assistant to
        # step it — the unresponsive-trim give-up must fire, not hang forever.
        ac, cal = self._start_assist(clock, coupling=0.5, trim_natural=0.0)
        assert run_until(cal, ac, clock, lambda: cal.assist_stable, 180)

        ac.responds_to_simvar = False
        ac.responds_to_axis = False
        ac.trim_natural = 0.3
        run_until(cal, ac, clock, lambda: not cal.active, 240)
        assert cal.state == CalState.ABORT
        assert "not responding" in (cal.abort_reason or "")

    def test_assist_retrim_probes_then_newtons(self, clock):
        # The human rule: trim relieves the elevator. With no verified slope
        # (a 0/1-step neutralization leaves none), the first move after a
        # power change is a GENTLE probe in the relief direction; its
        # observed effect teaches the slope, and the follow-ups are
        # Newton-sized. Never a reversal, never a runaway.
        ac, cal = self._start_assist(clock, coupling=0.5, trim_natural=0.1)
        assert run_until(cal, ac, clock, lambda: cal.assist_stable, 180)

        moves = []
        orig = cal._begin_neut_step
        cal._begin_neut_step = lambda target: (moves.append(target), orig(target))
        start_trim = ac.trim
        ac.trim_natural = 0.30
        assert run_until(cal, ac, clock, lambda: not cal.assist_stable, 5)
        assert run_until(cal, ac, clock, lambda: cal.assist_stable, 240)
        assert ac.trim == pytest.approx(0.30, abs=0.05)
        assert len(moves) <= 5, f"expected probe + Newton follow-ups, got {moves}"
        assert moves[0] - start_trim == pytest.approx(0.06, abs=0.02), \
            f"unverified slope must start with the gentle probe ({moves})"
        assert moves == sorted(moves), f"moves must not reverse direction ({moves})"
        assert cal._trim_slope_est == pytest.approx(-0.5, abs=0.2), \
            "the moves' observed effects must teach the true slope"


# --------------------------------------------------------------------------- #
#  Read-back closed loop (misreported travel limits)
# --------------------------------------------------------------------------- #

class TestTrimReadbackClosedLoop:
    def test_misreported_down_travel_still_completes(self, clock):
        # Field failure: the aircraft's actual nose-down travel is ~10% more
        # than the reported limit, so every direct write lands the read-back
        # proportionally short of the command — under the 2% station
        # tolerance near center, over it deep in the band ("Sweep station 14:
        # trim read-back never reached the station target (off by 2.7%)").
        # The overdrive loop must bleed the residual off and finish the run.
        class MisreportedTravelPlant(FakePlantAircraft):
            def _apply_trim_simvar(self, radians):
                deg = math.degrees(radians)
                if deg >= 0:
                    return deg / self.trim_up_limit
                return deg / (abs(self.trim_dn_limit) * 1.10)

        ac = MisreportedTravelPlant(coupling=0.5, physical_y=1.0)
        cal = TrimCalibrator(ac)
        cal.start()
        state = run_to_completion(cal, ac, clock)

        assert state == CalState.DONE, f"ended in {state} ({cal.abort_reason})"
        assert cal.result["virtual_y"] == pytest.approx(0.5, abs=0.05)
        # The band must actually cover the down side (where the error bites).
        assert min(t for t, _ in cal.result["samples"]) < -0.2
        assert abs(cal._trim_overdrive) <= cal.TRIM_OVERDRIVE_MAX

    def test_overdrive_stays_zero_on_accurate_mapping(self, clock):
        # 1:1 aircraft: the closed loop must be a no-op.
        ac = FakePlantAircraft(coupling=0.5)
        cal = TrimCalibrator(ac)
        cal.start()
        state = run_to_completion(cal, ac, clock)
        assert state == CalState.DONE
        assert abs(cal._trim_overdrive) <= cal.TRIM_RB_DEADBAND


# --------------------------------------------------------------------------- #
#  Live control-response change (set_gain_scale)
# --------------------------------------------------------------------------- #

class TestLiveGainScale:
    def test_applies_live_after_probe(self, clock):
        ac = FakePlantAircraft(coupling=0.5)
        cal = TrimCalibrator(ac)
        cal.start()
        assert run_until(cal, ac, clock,
                         lambda: cal.state == CalState.STABILIZE, 60)
        assert cal._gain_scale == pytest.approx(1.0)

        cal.set_gain_scale(0.25)
        clock.advance(1 / 30.0)
        cal.update(ac.telem())
        assert cal._gain_scale == pytest.approx(0.25)
        kp, _, _ = cal._pitch_base_gains
        assert cal._pitch_pid.kp == pytest.approx(kp * 0.25)

    def test_deferred_during_probe(self, clock):
        # A mid-probe change must NOT step the probe's input amplitude; it
        # applies the moment the probe completes.
        ac = FakePlantAircraft(coupling=0.5)
        cal = TrimCalibrator(ac)
        cal.start()
        dt = 1 / 30.0
        clock.advance(dt)
        cal.update(ac.telem())
        ac.step(dt)
        assert cal.state == CalState.PROBE

        cal.set_gain_scale(0.5)
        clock.advance(dt)
        cal.update(ac.telem())
        ac.step(dt)
        assert cal._gain_scale == pytest.approx(1.0), "must not apply mid-probe"
        assert cal._gain_scale_request == pytest.approx(0.5)

        assert run_until(cal, ac, clock,
                         lambda: cal.state == CalState.STABILIZE, 60)
        clock.advance(dt)
        cal.update(ac.telem())
        assert cal._gain_scale == pytest.approx(0.5)
        assert cal._gain_scale_request is None

    def test_request_clamped_to_floor(self, clock):
        ac = FakePlantAircraft()
        cal = TrimCalibrator(ac)
        cal.start()
        cal.set_gain_scale(0.01)
        assert cal._gain_scale_request == pytest.approx(cal.OSC_MIN_GAIN_SCALE)

    def test_assist_relieves_mean_load_while_porpoising(self, clock):
        # Field failure: a steady-amplitude porpoise (|VS| swinging well past
        # the level tolerance) kept the level gate shut forever, freezing the
        # trim while the elevator carried the whole mean load. The long
        # (phugoid-averaged) load path must keep moving the trim toward the
        # natural point even though the aircraft never reports "level".
        class PorpoisingPlant(FakePlantAircraft):
            osc_amp = 0.0        # m/s VS swing, enabled once holding
            osc_period = 6.0
            _t = 0.0

            def step(self, dt):
                super().step(dt)
                self._t += dt
                self.vs += self.osc_amp * math.sin(
                    2 * math.pi * self._t / self.osc_period)

        ac = PorpoisingPlant(coupling=0.5, trim_natural=0.0)
        cal = TrimCalibrator(ac)
        cal.start(assist=True)
        assert run_until(cal, ac, clock,
                         lambda: cal.state == CalState.ASSIST_HOLD, 240), \
            f"never reached ASSIST_HOLD ({cal.state}, {cal.abort_reason})"
        # Seed a slope estimate as a real run would have from neutralization.
        cal._trim_slope_est = -0.5
        cal._assist_sized = True

        # "Power change" that also excites a sustained porpoise (level tol is
        # 0.5 m/s; a 2 m/s swing keeps the level gate shut essentially always).
        ac.osc_amp = 2.0
        ac.trim_natural = 0.40
        # The force path stops once the (period-matched) mean load drops
        # under its threshold, so convergence is to within FORCE/|slope| of
        # the natural point — the point is that trim MOVES substantially
        # instead of freezing.
        assert run_until(cal, ac, clock,
                         lambda: abs(ac.trim - 0.40) < 0.20, 120), \
            f"trim frozen at {ac.trim:+.3f} despite mean load (vs {ac.vs:+.2f})"
        assert ac.trim > 0.15
        assert not cal.assist_stable, \
            "still porpoising - must not report ready"


# --------------------------------------------------------------------------- #
#  Takeover / handback continuity (no stick snap)
# --------------------------------------------------------------------------- #

class TestHoldSpringContinuity:
    def test_hold_pins_at_current_stick_position(self, clock):
        # Users start runs while holding the stick deflected against an
        # out-of-trim spring; the hold spring must pin where the HAND is,
        # not at the old trim-following center (which yanked the stick).
        ac = FakePlantAircraft()
        ac.phys_stick = (0.10, -0.30)
        ac.spring_x.cpOffset = 800   # stale trim-following center
        ac.spring_y.cpOffset = -1500
        cal = TrimCalibrator(ac)
        cal.start()
        clock.advance(1 / 30.0)
        cal.update(ac.telem())
        assert ac.spring_x.cpOffset == pytest.approx(0.10 * 4096, abs=2)
        assert ac.spring_y.cpOffset == pytest.approx(-0.30 * 4096, abs=2)

    def test_release_continuity_after_completed_run(self, clock):
        # At handback the parked stick must sit where the normal path's
        # first frame delivers what the engine's last frame delivered:
        # phys* = u_end + trim_follow_offset(T_restored).
        ac = FakePlantAircraft(coupling=0.5, trim_natural=0.1)
        cal = TrimCalibrator(ac)
        cal.start()
        state = run_to_completion(cal, ac, clock)
        assert state == CalState.DONE, f"ended in {state} ({cal.abort_reason})"

        p_y = ac.joystick_trim_follow_gain_physical_y
        v_y = ac.joystick_trim_follow_gain_virtual_y
        offs_y = max(-1, min(1, ac.trim * p_y)) * (1 - v_y)
        expect_y = max(-1, min(1, cal._u_elev + offs_y))
        assert ac.spring_y.cpOffset / 4096 == pytest.approx(expect_y, abs=0.03)
        # No aileron trim in telemetry -> x continuity is just the residual u.
        expect_x = max(-1, min(1, cal._u_ail))
        assert ac.spring_x.cpOffset / 4096 == pytest.approx(expect_x, abs=0.03)

    def test_assist_cancel_parks_at_continuity_position(self, clock):
        ac = FakePlantAircraft(coupling=0.5, trim_natural=0.2)
        cal = TrimCalibrator(ac)
        cal.start(assist=True)
        assert run_until(cal, ac, clock, lambda: cal.assist_stable, 180)

        cal.stop("Cancelled by user")
        assert run_until(cal, ac, clock, lambda: not cal.active, 5)
        # Trimmed hold (u ~ 0): continuity position is just the trim-follow
        # offset at the held trim.
        offs_y = max(-1, min(1, ac.trim * 1.0)) * (1 - 0.5)
        assert ac.spring_y.cpOffset / 4096 == pytest.approx(offs_y, abs=0.05)

    def test_hard_abort_still_releases_immediately(self, clock):
        # Safety trips must not gain a settle delay: pause mid-hold.
        ac = FakePlantAircraft(coupling=0.5)
        cal = TrimCalibrator(ac)
        cal.start(assist=True)
        assert run_until(cal, ac, clock,
                         lambda: cal.state == CalState.ASSIST_HOLD, 180)
        clock.advance(1 / 30.0)
        cal.update(ac.telem(SimPaused=1))
        assert not cal.active
        assert cal.state == CalState.ABORT


# --------------------------------------------------------------------------- #
#  Slow-phugoid protection (P-38L field case)
# --------------------------------------------------------------------------- #

class TestSlowPhugoidProtection:
    def test_increased_response_scale_applies(self, clock):
        # Lightly damped long-period phugoids need MORE damping authority:
        # the Increased option starts the loop above 1.0.
        ac = FakePlantAircraft()
        cal = TrimCalibrator(ac)
        cal.initial_gain_scale = 1.5
        cal.start()
        assert run_until(cal, ac, clock,
                         lambda: cal.state == CalState.STABILIZE, 60)
        assert cal._gain_scale == pytest.approx(1.5)
        kp, _, _ = cal._pitch_base_gains
        assert cal._pitch_pid.kp == pytest.approx(kp * 1.5)

    def test_live_request_clamps_to_increased_ceiling(self, clock):
        ac = FakePlantAircraft()
        cal = TrimCalibrator(ac)
        cal.start()
        cal.set_gain_scale(3.0)
        assert cal._gain_scale_request == pytest.approx(cal.GAIN_SCALE_MAX)

    def test_trim_does_not_chase_slow_phugoid(self, clock):
        # At a VS zero crossing of an oscillation the load sits at its swing
        # extreme and is momentarily flat — the plateau check passes at the
        # worst phase and the trim chased (and pumped) the porpoise. With a
        # cycle confirmed, settled measurements are suppressed and the mean
        # path averages over one measured period, so a trimmed aircraft in a
        # slow porpoise gets NO trim moves at all.
        class SlowPhugoidPlant(FakePlantAircraft):
            osc_amp = 0.0        # per-frame VS forcing, enabled once holding
            osc_period = 24.0
            _t = 0.0

            def step(self, dt):
                super().step(dt)
                self._t += dt
                self.vs += self.osc_amp * math.sin(
                    2 * math.pi * self._t / self.osc_period)

        ac = SlowPhugoidPlant(coupling=0.5, trim_natural=0.0)
        cal = TrimCalibrator(ac)
        cal.start(assist=True)
        assert run_until(cal, ac, clock,
                         lambda: cal.state == CalState.ASSIST_HOLD, 240)
        cal._trim_slope_est = -0.5
        cal._assist_sized = True

        moves = []
        orig = cal._begin_neut_step
        cal._begin_neut_step = lambda tgt: (moves.append(tgt), orig(tgt))
        # The closed loop rejects most of the forcing (the disturbance ends
        # up in the CONTROL signal, which is exactly what used to poison the
        # phase measurements); the residual sub-tolerance VS cycle leaks into
        # trailing windows, which must therefore be held to the rough
        # threshold — a trimmed aircraft gets NO moves.
        ac.osc_amp = 0.25

        run_until(cal, ac, clock, lambda: False, 120)   # ride the porpoise

        assert cal.active, f"run died: {cal.abort_reason}"
        assert not moves, f"trim chased the phugoid: {moves}"
        assert abs(ac.trim) <= 0.02
        # (assist_stable MAY arm here: the residual cycle sits inside the
        # level tolerance and full-cycle means show a trimmed aircraft —
        # that meets the same bar a calm aircraft meets.)

    def test_gate_rearms_promptly_after_porpoise_damps(self, clock):
        # Field: with the aircraft pegged level after the porpoise damped,
        # a lingering oscillation-presence flag kept the settled path
        # suppressed (small residual loads unretrimmable) and the ready
        # gate dark. Presence must exit within ~0.6 period of the last
        # extremum and the gate re-arm shortly after.
        class SlowPhugoidPlant(FakePlantAircraft):
            osc_amp = 0.0
            osc_period = 24.0
            _t = 0.0

            def step(self, dt):
                super().step(dt)
                self._t += dt
                self.vs += self.osc_amp * math.sin(
                    2 * math.pi * self._t / self.osc_period)

        ac = SlowPhugoidPlant(coupling=0.5, trim_natural=0.0)
        cal = TrimCalibrator(ac)
        cal.start(assist=True)
        assert run_until(cal, ac, clock,
                         lambda: cal.state == CalState.ASSIST_HOLD, 240)
        cal._trim_slope_est = -0.5
        cal._assist_sized = True
        ac.osc_amp = 0.25
        run_until(cal, ac, clock, lambda: False, 60)   # ride the porpoise

        ac.osc_amp = 0.0    # damps out
        t0 = clock.t
        assert run_until(cal, ac, clock, lambda: cal.assist_stable, 30), \
            "gate never re-armed after the porpoise damped"
        assert clock.t - t0 <= 25.0, \
            f"gate re-arm took {clock.t - t0:.1f}s after damping"


# --------------------------------------------------------------------------- #
#  Measured pct->deg scale (G-111 Albatross field case)
# --------------------------------------------------------------------------- #

class TestMeasuredTrimScale:
    class SymmetricNormPlant(FakePlantAircraft):
        """Reports asymmetric travel (-6.5..+12.0 deg) but normalizes the pct
        read-back by the UP limit on BOTH sides — limits-based down-side
        writes land 46% short, and the first hold-in-place write RELOCATES
        the trim (the no-op assumption needs write and read-back to agree
        on the scale)."""

        def __init__(self, **kw):
            super().__init__(**kw)
            self.trim_up_limit = 12.0
            self.trim_dn_limit = -6.5
            # start force-free like a field run: no trim-following leak into
            # the takeover baseline (v=1 -> zero delivered axis at rest)
            self.joystick_trim_follow_gain_virtual_y = 1.0

        def _apply_trim_simvar(self, radians):
            return math.degrees(radians) / self.trim_up_limit

        def telem(self, **over):
            over.setdefault("ElevTrim", self.trim * self.trim_up_limit)
            return super().telem(**over)

    def test_full_run_completes_on_symmetric_normalization(self, clock):
        # Field: two neutralization aborts (hold-in-place relocated the trim,
        # reported as read-back drift) and sweep aborts at the deep down-side
        # stations (overdrive railed at its bound against the 46% deficit).
        ac = self.SymmetricNormPlant(coupling=0.5, physical_y=1.0,
                                     trim_natural=-0.15)
        ac.trim = -0.15   # user starts trimmed, like the field runs
        cal = TrimCalibrator(ac)
        cal.start()
        state = run_to_completion(cal, ac, clock)

        assert state == CalState.DONE, f"ended in {state} ({cal.abort_reason})"
        assert cal.result["virtual_y"] == pytest.approx(0.5, abs=0.05)
        # the band must reach the deep down side where limits-based writes
        # (even with overdrive) provably could not place stations
        assert min(t for t, _ in cal.result["samples"]) < -0.3
        # the true scale was measured on the down side (12.0, not 6.5)
        assert cal._trim_deg_per_pct.get(-1) == pytest.approx(12.0, rel=0.05)

    def test_hold_in_place_does_not_relocate_trim(self, clock):
        # The exact first-failure signature: neutralization holds the trim
        # at its current position; the write must be a true no-op.
        ac = self.SymmetricNormPlant(coupling=0.5, trim_natural=-0.095)
        ac.trim = -0.095   # matches the field trace (user starts trimmed)
        cal = TrimCalibrator(ac)
        cal.start()
        assert run_until(cal, ac, clock,
                         lambda: cal.state == CalState.TRIM_NEUTRAL, 120), \
            f"never reached TRIM_NEUTRAL ({cal.state}, {cal.abort_reason})"
        start_trim = ac.trim
        for _ in range(30):   # one second of hold-in-place writes
            clock.advance(1 / 30.0)
            cal.update(ac.telem())
            ac.step(1 / 30.0)
            if cal._neut_steps > 0 or not cal.active:
                break
        assert cal.active, f"aborted: {cal.abort_reason}"
        assert ac.trim == pytest.approx(start_trim, abs=0.005), \
            "hold-in-place write relocated the trim (scale mismatch)"


# --------------------------------------------------------------------------- #
#  Retrim must never deadlock (G-111 at 84 kt field hang)
# --------------------------------------------------------------------------- #

class TestRetrimNeverStarves:
    def test_irregular_oscillation_with_big_load_still_moves_trim(self, clock):
        # Field hang: messy low-speed flight produced ragged VS extrema —
        # oscillation "present" but the period never consistent — and every
        # move path ended up gated off. Trim froze at the wrong point with
        # the elevator parked near full aft until the user changed power.
        # Whatever the measurement machinery thinks of the motion, a large
        # sustained mean load MUST keep moving the trim.
        class IrregularPlant(FakePlantAircraft):
            osc_on = False
            _t = 0.0

            def step(self, dt):
                super().step(dt)
                self._t += dt
                if self.osc_on:
                    # two incommensurate periods -> ragged extremum gaps,
                    # with residual swing big enough to keep confirming
                    # extrema (the field case was a visible porpoise)
                    self.vs += 0.35 * math.sin(2 * math.pi * self._t / 9.0)
                    self.vs += 0.35 * math.sin(2 * math.pi * self._t / 14.0)

        ac = IrregularPlant(coupling=0.5, trim_natural=0.0)
        cal = TrimCalibrator(ac)
        cal.start(assist=True)
        assert run_until(cal, ac, clock,
                         lambda: cal.state == CalState.ASSIST_HOLD, 240)
        cal._trim_slope_est = -0.5
        cal._assist_sized = True

        ac.osc_on = True
        ac.trim_natural = 0.40    # big sustained mean load appears
        # The long window must fill (24 s) before it has authority under
        # disagreement, then capped moves land every cadence — bounded at
        # roughly 30 s worst case. The old trust-gated design froze for
        # 27 s stretches whenever the period estimate wavered, taking 45+ s
        # (and the real field case simply never moved).
        assert run_until(cal, ac, clock, lambda: ac.trim > 0.25, 40), \
            f"trim starved at {ac.trim:+.3f} (vs {ac.vs:+.2f}) despite mean load"


# --------------------------------------------------------------------------- #
#  Relief-verified retrim (wrong-slope runaway field case)
# --------------------------------------------------------------------------- #

class TestReliefVerifiedRetrim:
    def test_power_change_cannot_teach_wrong_direction(self, clock):
        # Field runaway: 0-step neutralization left no slope; the first
        # probe move coincided with a power change, and the load change got
        # attributed to the move — a wrong-SIGN slope that then drove every
        # Newton move the wrong way (nose-down trim against held up
        # elevator) until the elevator railed and the run aborted. With
        # relief-verified direction, the trim must converge to the new
        # natural point and never wander meaningfully the wrong way.
        class StrongTrimPlant(FakePlantAircraft):
            pass

        ac = StrongTrimPlant(coupling=1.8, trim_natural=0.0)
        cal = TrimCalibrator(ac)
        cal.start(assist=True)
        assert run_until(cal, ac, clock,
                         lambda: cal.state == CalState.ASSIST_HOLD, 240)
        assert cal._trim_slope_est is None, "scenario needs the 0-step case"

        min_trim = [0.0]
        orig_step = ac.step
        def step_watch(dt):
            orig_step(dt)
            min_trim[0] = min(min_trim[0], ac.trim)
        ac.step = step_watch

        ac.trim_natural = 0.20   # power reduction: level trim moves nose-up
        assert run_until(cal, ac, clock,
                         lambda: abs(ac.trim - 0.20) < 0.05, 120), \
            f"never converged (trim {ac.trim:+.3f}, u {cal._u_elev:+.2f})"
        assert cal.active, f"aborted: {cal.abort_reason}"
        assert min_trim[0] > -0.08, \
            f"trim ran the wrong way to {min_trim[0]:+.3f} (field runaway)"

    def test_wrong_initial_direction_recovers_by_flipping(self, clock):
        # An aircraft with an inverted trim response (positive du/dtrim)
        # and no measured slope: the default relief direction is wrong, the
        # first two gentle probes make things worse, the direction flips,
        # and the assistant converges — cost: two bounded 6% probes.
        ac = FakePlantAircraft(coupling=-0.5, trim_natural=0.0)
        cal = TrimCalibrator(ac)
        cal.start(assist=True)
        assert run_until(cal, ac, clock,
                         lambda: cal.state == CalState.ASSIST_HOLD, 240)
        assert cal._trim_slope_est is None
        assert cal._assist_dir == 1   # default: normal convention (wrong here)

        ac.trim_natural = -0.20
        assert run_until(cal, ac, clock,
                         lambda: abs(ac.trim - (-0.20)) < 0.06, 180), \
            f"never converged (trim {ac.trim:+.3f}, dir {cal._assist_dir})"
        assert cal.active, f"aborted: {cal.abort_reason}"
        assert cal._assist_dir == -1, "direction must have flipped"


# --------------------------------------------------------------------------- #
#  Throttle-movement tracking (decision-log context)
# --------------------------------------------------------------------------- #

class TestThrottleTracking:
    def test_lever_movement_detected_and_settled(self, clock):
        ac = FakePlantAircraft(coupling=0.5)
        cal = TrimCalibrator(ac)
        cal.start(assist=True)
        dt = 1 / 30.0

        def run(seconds, thr):
            for _ in range(int(seconds / dt)):
                clock.advance(dt)
                cal.update(ac.telem(ThrottlePct=thr))
                ac.step(dt)

        # Real telemetry shape (field sample): 0-100 percent, four lever
        # slots with the unused pair pinned at 0 on a twin — the zeros must
        # not dilute the mean (field: logged 3170% from 100x + dilution).
        run(5.0, [63.4, 63.4, 0.0, 0.0])
        assert cal._thr_ref == pytest.approx(63.4)
        assert cal._thr_last_move_t is None
        assert "untouched" in cal._thr_context(clock.t)

        run(1.0, [70.0, 68.0, 0.0, 0.0])   # levers moved (mean 69)
        assert cal._thr_moving
        assert cal._thr_last_move_t is not None

        run(3.0, [70.0, 68.0, 0.0, 0.0])   # levers still: episode settles
        assert not cal._thr_moving
        assert cal._thr_ref == pytest.approx(69.0)
        assert "last moved" in cal._thr_context(clock.t)

        # Idle pull: the learned engine mask keeps reading the two real
        # levers (0%), not a four-way mean of zeros with no movement seen.
        run(1.0, [0.0, 0.0, 0.0, 0.0])
        assert cal._thr_moving
        run(3.0, [0.0, 0.0, 0.0, 0.0])
        assert cal._thr_ref == pytest.approx(0.0)

    def test_no_throttle_telemetry_is_inert(self, clock):
        ac = FakePlantAircraft(coupling=0.5)
        cal = TrimCalibrator(ac)
        cal.start(assist=True)
        for _ in range(60):
            clock.advance(1 / 30.0)
            cal.update(ac.telem())
            ac.step(1 / 30.0)
        assert cal._thr_ref is None
        assert cal._thr_context(clock.t) == ""

    def test_restore_walks_spring_center_no_jumps(self, clock):
        # The stick is sim-inert during RESTORE, but the spring is very much
        # hand-active: an instant center set snaps the stick hard enough to
        # whack a resting hand. The hold center must move rate-limited.
        ac = FakePlantAircraft(coupling=0.5, trim_natural=0.1)
        cal = TrimCalibrator(ac)
        cal.start()
        dt = 1 / 30.0
        max_jump = 0.0
        prev = None
        for _ in range(40000):
            clock.advance(dt)
            cal.update(ac.telem())
            ac.step(dt)
            if cal.state == CalState.RESTORE:
                y = ac.spring_y.cpOffset
                if prev is not None:
                    max_jump = max(max_jump, abs(y - prev))
                prev = y
            else:
                prev = None
            if cal.state in (CalState.DONE, CalState.ABORT):
                break
        assert cal.state == CalState.DONE, f"ended {cal.state} ({cal.abort_reason})"
        limit = cal.HOLD_WALK_RATE * dt * 1.5   # rate + rounding slack
        assert max_jump <= limit, \
            f"spring center jumped {max_jump:.0f} units in one frame (limit {limit:.0f})"


# --------------------------------------------------------------------------- #
#  Watchdog vs commanded trim motion (SR22T sweep-abort field case)
# --------------------------------------------------------------------------- #

class TestWatchdogTrimMotionGate:
    def test_extrema_during_trim_motion_do_not_count(self, clock):
        # Station hops and retrim moves each ring the phugoid once; a
        # sequence of moves with growing effect reads as growing
        # oscillation and backed the gains off mid-sweep on the SR22T.
        ac = FakePlantAircraft()
        cal = TrimCalibrator(ac)
        cal.start()
        cal._trim_slew_t = clock.t   # trim is being commanded right now
        _feed_oscillation(cal, GROWING)
        assert cal._gain_scale == pytest.approx(1.0), \
            "forced transients must not back the gains off"

        clock.advance(cal.OSC_TRIM_RINGDOWN_S + 0.5)   # ringdown over
        _feed_oscillation(cal, GROWING)
        assert cal._gain_scale == pytest.approx(0.5), \
            "genuine oscillation clear of trim motion must still back off"

    def test_steep_slope_full_run_keeps_full_gains(self, clock):
        # SR22T-class steep response: station hops force big VS transients.
        # The whole run must complete at full leveling authority — the
        # field abort came from a x0.25 leveler too weak for slope ~3.
        ac = FakePlantAircraft(coupling=2.2, physical_y=1.0, trim_natural=-0.1)
        ac.trim = -0.1
        ac.joystick_trim_follow_gain_virtual_y = 1.0   # start force-free
        cal = TrimCalibrator(ac)
        cal.start()
        state = run_to_completion(cal, ac, clock)
        assert state == CalState.DONE, f"ended {state} ({cal.abort_reason})"
        assert cal._gain_scale == pytest.approx(1.0), \
            "no backoff should fire from commanded-move transients"
        assert cal.result["virtual_y"] == pytest.approx(1 - 2.2, abs=0.08)


# --------------------------------------------------------------------------- #
#  Slope-adaptive sweep step (Hawk T1 saturation field case)
# --------------------------------------------------------------------------- #

class TestAdaptiveSweepStep:
    def test_steep_aircraft_gets_fine_stations_and_completes(self, clock):
        # Hawk-class authority (slope ~13): a 0.06 station hop is ~0.78
        # elevator - station 2 consumed the whole budget and the run
        # saturated with 2 samples. With the learned slope, the step must
        # shrink so the band fits >= MIN_SAMPLES stations over the
        # aircraft's physically usable trim window.
        ac = FakePlantAircraft(coupling=13.0, physical_y=1.0, trim_natural=0.0)
        ac.joystick_trim_follow_gain_virtual_y = 1.0
        cal = TrimCalibrator(ac)
        cal.start()
        cal._trim_slope_est = -13.0   # as the assistant would have learned
        state = run_to_completion(cal, ac, clock)

        assert state == CalState.DONE, f"ended {state} ({cal.abort_reason})"
        assert cal._sweep_step == pytest.approx(0.2 / 13.0, rel=0.05)
        assert cal._station_tol < cal.TRIM_STATION_TOL
        assert len(cal.result["samples"]) >= cal.MIN_SAMPLES_FOR_FIT
        # band stays within the usable window (full elevator ~ 1/13 trim)
        assert max(abs(t) for t, _ in cal.result["samples"]) < 0.06
        # gain clamps at the setting bound (known authority-exceeded case),
        # but the CURVE over the usable window is the real product
        assert cal.result["virtual_y"] == pytest.approx(-2.0)
        assert cal.result["curve"] is not None

    def test_saturating_side_truncates_and_other_side_completes(self, clock):
        # C208B field abort 2026-07-19 20:29: three nose-up stations, the
        # approach to station 4 rode into the elevator-saturation guard, and
        # the run aborted "before enough samples" with the nose-down side
        # never visited. Saturation on one side must truncate that side and
        # continue on the other.
        class OuterWallPlant(FakePlantAircraft):
            # Mild near center, brutal outside +-0.125 trim: the global fit
            # under-predicts the outer stations, so the predictive budget
            # cannot see the wall coming — only the saturation guard can.
            def effective_coupling(self, trim):
                return 1.0 if abs(trim) <= 0.125 else 6.0

        ac = OuterWallPlant(physical_y=1.0)
        ac.joystick_trim_follow_gain_virtual_y = 1.0
        cal = TrimCalibrator(ac)
        cal.start()
        state = run_to_completion(cal, ac, clock)
        assert state == CalState.DONE, f"ended {state} ({cal.abort_reason})"
        ts = [t for t, _ in cal.result["samples"]]
        assert len(ts) >= cal.MIN_SAMPLES_FOR_FIT
        assert min(ts) < 0 < max(ts), "both sides must be represented"

    def test_blind_steep_aircraft_self_sizes_and_completes(self, clock):
        # No assist power change => no learned slope => the sweep enters at
        # the default 0.06 step. On Hawk-class authority (slope ~13) station
        # 2 would be a 0.78-elevator lurch: the outbound freeze must stop and
        # measure early instead, the step must resize from that sample, and
        # the run must complete without ever touching the saturation guard.
        ac = FakePlantAircraft(coupling=13.0, physical_y=1.0, trim_natural=0.0)
        ac.joystick_trim_follow_gain_virtual_y = 1.0
        cal = TrimCalibrator(ac)
        cal.start()
        max_u = 0.0
        dt = 1 / 30.0
        for _ in range(40000):
            clock.advance(dt)
            cal.update(ac.telem())
            ac.step(dt)
            if cal.state == CalState.SWEEP:
                max_u = max(max_u, abs(cal._u_elev))
            if cal.state in (CalState.DONE, CalState.ABORT):
                break
        assert cal.state == CalState.DONE, f"ended {cal.state} ({cal.abort_reason})"
        assert len(cal.result["samples"]) >= cal.MIN_SAMPLES_FOR_FIT
        assert max_u < cal.U_ELEV_SAT, \
            f"blind entry rode to {max_u:.2f} elevator; freeze must stop earlier"
        assert cal._sweep_step <= 0.02, \
            f"step must self-size from the first response (got {cal._sweep_step:.3f})"

    def test_predictive_budget_stops_side_before_saturation(self, clock):
        # Linear slope-3.5 aircraft (C208B at cruise): the sweep must stop
        # each side from its own fit prediction without ever riding the
        # elevator into the saturation guard.
        ac = FakePlantAircraft(coupling=3.5, physical_y=1.0)
        ac.joystick_trim_follow_gain_virtual_y = 1.0
        cal = TrimCalibrator(ac)
        cal.start()
        max_u = 0.0
        dt = 1 / 30.0
        for _ in range(40000):
            clock.advance(dt)
            cal.update(ac.telem())
            ac.step(dt)
            if cal.state == CalState.SWEEP:
                max_u = max(max_u, abs(cal._u_elev))
            if cal.state in (CalState.DONE, CalState.ABORT):
                break
        assert cal.state == CalState.DONE, f"ended {cal.state} ({cal.abort_reason})"
        assert len(cal.result["samples"]) >= cal.MIN_SAMPLES_FOR_FIT
        assert max_u < cal.U_ELEV_SAT, \
            f"sweep rode to {max_u:.2f} elevator; predictive budget must stop earlier"
        sampled_u = max(abs(u) for _, u in cal.result["samples"])
        assert sampled_u <= cal.SWEEP_U_BUDGET + 0.1

    def test_normal_aircraft_step_unchanged(self, clock):
        # Everything field-validated (|slope| <= 3.3) keeps the exact
        # current step - the 0.2 target was chosen to guarantee it.
        ac = FakePlantAircraft(coupling=2.2, physical_y=1.0, trim_natural=-0.1)
        ac.trim = -0.1
        ac.joystick_trim_follow_gain_virtual_y = 1.0
        cal = TrimCalibrator(ac)
        cal.start()
        cal._trim_slope_est = -2.2
        state = run_to_completion(cal, ac, clock)
        assert state == CalState.DONE
        assert cal._sweep_step == pytest.approx(cal.SWEEP_STEP)
        assert cal._station_tol == pytest.approx(cal.TRIM_STATION_TOL)


# --------------------------------------------------------------------------- #
#  Assistant direction recovery (RV-10 field incident, 2026-07-22)
# --------------------------------------------------------------------------- #

class TestAssistDirectionRecovery:
    """A deceleration-confounded neutralization secant seeded the relief
    direction backwards; on a weak-trim aircraft (|du/dtrim| ~ 0.18) every
    wrong-way move worsened the load by less than the per-move verdict
    threshold, so the two-worsenings flip never armed and the trim marched
    to its rail with the load still growing."""

    def _poisoned_hold(self, clock, coupling=0.18, natural=0.5):
        # Reach a clean, stable hold at natural trim 0 (no moves needed, so
        # the direction is still unproven), then inject the field state: a
        # noise secant taught a wrong-sign slope, and a power change makes a
        # load appear.
        ac = FakePlantAircraft(coupling=coupling, trim_natural=0.0)
        cal = TrimCalibrator(ac)
        cal.start(assist=True)
        assert run_until(cal, ac, clock,
                         lambda: cal.state == CalState.ASSIST_HOLD, 240), \
            f"never reached ASSIST_HOLD ({cal.state}, {cal.abort_reason})"
        assert run_until(cal, ac, clock, lambda: cal.assist_stable, 120)
        assert not cal._assist_dir_proven
        cal._trim_slope_est = +0.067   # the RV-10's confounded export
        cal._assist_sized = True
        cal._assist_dir = -1
        ac.trim_natural = natural      # load appears; correct relief is UP
        return ac, cal

    def test_wrong_direction_recovers_via_sequence_secant(self, clock):
        ac, cal = self._poisoned_hold(clock)
        assert run_until(cal, ac, clock, lambda: cal._assist_dir == 1, 180), \
            f"direction never flipped (trim {ac.trim:+.3f})"
        # The whole point: the flip must come from judging the unrelieved
        # run, long before the trim reaches the rail.
        assert ac.trim > -0.6, \
            f"flip only came at the rail (trim {ac.trim:+.3f})"
        assert run_until(cal, ac, clock, lambda: cal.assist_stable, 300), \
            f"assistant never recovered (trim {ac.trim:+.3f})"
        # Weak trim: "trimmed" means the residual LOAD is inside tolerance
        # (the trim band that allows is NEUT_U_TOL / coupling wide).
        assert abs(0.18 * (ac.trim - 0.5)) <= cal.NEUT_U_TOL + 0.01

    def test_unproven_direction_flips_at_the_rail(self, clock):
        # Same poison, but the hold is already near the rail so the march has
        # no room to build a sequence baseline before pegging: the rail
        # itself must force the reversal.
        ac, cal = self._poisoned_hold(clock, natural=0.2)
        ac.trim = -0.9
        cal._neut_target = -0.9
        assert run_until(cal, ac, clock, lambda: cal._assist_dir == 1, 180), \
            f"direction never flipped at the rail (trim {ac.trim:+.3f})"
        assert run_until(cal, ac, clock, lambda: cal.assist_stable, 300), \
            f"assistant never recovered (trim {ac.trim:+.3f})"
        assert abs(0.18 * (ac.trim - 0.2)) <= cal.NEUT_U_TOL + 0.01

    def test_out_of_authority_reports_and_stops_stepping(self, clock):
        # Relief-proven direction that genuinely runs out of trim range must
        # NOT flip — it reports and stops issuing no-op steps at the rail.
        ac = FakePlantAircraft(coupling=0.4, trim_natural=0.0)
        cal = TrimCalibrator(ac)
        cal.start(assist=True)
        assert run_until(cal, ac, clock, lambda: cal.assist_stable, 240)
        ac.trim_natural = 1.2   # beyond the +/-0.95 command clamp
        assert run_until(cal, ac, clock, lambda: cal._assist_railed, 400), \
            f"never reported the rail (trim {ac.trim:+.3f}, dir {cal._assist_dir})"
        assert cal._assist_dir == 1, "a proven direction must never flip"
        assert ac.trim == pytest.approx(cal.TRIM_CMD_CLAMP, abs=0.02)
        assert "limit" in cal.status_message
        # No further stepping: the trim stays parked at the clamp and no move
        # is pending verification.
        for _ in range(int(30 / (1 / 30.0))):
            clock.advance(1 / 30.0)
            cal.update(ac.telem())
            ac.step(1 / 30.0)
        assert ac.trim == pytest.approx(cal.TRIM_CMD_CLAMP, abs=0.02)
        assert cal._assist_premove is None

    def test_sequence_secant_never_overrides_a_proven_direction(self, clock):
        ac = FakePlantAircraft()
        cal = TrimCalibrator(ac)
        cal._assist_dir = 1
        cal._assist_dir_proven = True
        cal._assist_u_mean = 0.3
        cal._assist_seq0 = (0.0, 0.1)
        cal._assist_seq_n = 5
        telem = ac.telem(ElevTrimPct=0.5)
        cal._assist_seq_flip_check(telem)
        assert cal._assist_dir == 1
        # Identical evidence with the direction unproven must flip it.
        cal._assist_dir_proven = False
        cal._assist_seq_flip_check(telem)
        assert cal._assist_dir == -1

    def test_neutralization_noise_secant_not_exported(self, clock):
        # Trim so weak each probe moves the load ~0.006 — drift-level du.
        # Neutralization may still converge, but the noise secant must NOT be
        # exported to sign/size the assistant's retrims (the hold then enters
        # with the normal-convention default direction, unsized).
        ac = FakePlantAircraft(coupling=0.1, trim_natural=0.5)
        cal = TrimCalibrator(ac)
        cal.start(assist=True)
        assert run_until(cal, ac, clock,
                         lambda: cal.state == CalState.ASSIST_HOLD, 240), \
            f"never reached ASSIST_HOLD ({cal.state}, {cal.abort_reason})"
        assert cal._trim_slope_est is None
        assert cal._assist_dir == 1
        assert not cal._assist_sized


# --------------------------------------------------------------------------- #
#  Glider descent target (engineless aircraft can't hold level flight)
# --------------------------------------------------------------------------- #

class TestGliderDescent:
    """A glider run holds a steady sink (cal.vs_target) instead of level: the
    pitch loop already targets vs_target, and every settle/level gate measures
    VS relative to it, so calibration converges in descent exactly as a
    powered aircraft converges at level."""

    def test_assist_holds_target_sink_not_level(self, clock):
        ac = FakePlantAircraft(coupling=0.5, trim_natural=0.1)
        cal = TrimCalibrator(ac)
        cal.vs_target = -0.5  # ~ -100 fpm
        cal.start(assist=True)
        assert run_until(cal, ac, clock,
                         lambda: cal.state == CalState.ASSIST_HOLD, 180), \
            f"never reached ASSIST_HOLD ({cal.state}, {cal.abort_reason})"
        assert run_until(cal, ac, clock, lambda: cal.assist_stable, 120), \
            f"glider never stabilized (vs {ac.vs:+.2f})"
        # Settled on the target sink, NOT level.
        assert ac.vs == pytest.approx(-0.5, abs=0.15)

    def test_full_calibration_in_descent_recovers_slope(self, clock):
        ac = FakePlantAircraft(coupling=0.5, physical_y=1.0)
        cal = TrimCalibrator(ac)
        cal.vs_target = -0.5
        cal.start()
        state = run_to_completion(cal, ac, clock)
        assert state == CalState.DONE, f"ended {state} ({cal.abort_reason})"
        # The measured trim->elevator slope is the plant's, unchanged by the
        # descent operating point.
        assert cal.result["virtual_y"] == pytest.approx(0.5, abs=0.05)
        assert cal.result["r_squared"] > 0.95
        # Provenance: the held sink is stamped into the curve entry
        # (-0.5 m/s = -98 fpm) and survives the parser for display.
        assert cal.result["curve"]["vs_fpm"] == -98
        from telemffb.utils import parse_trim_follow_family
        parsed = parse_trim_follow_family(cal.result["curve"])
        assert parsed[0]["vs_fpm"] == -98

    def test_level_run_default_target_unchanged(self, clock):
        # vs_target defaults to 0.0 (level): a powered run is byte-identical.
        ac = FakePlantAircraft(coupling=0.5, physical_y=1.0)
        cal = TrimCalibrator(ac)
        assert cal.vs_target == 0.0
        cal.start()
        state = run_to_completion(cal, ac, clock)
        assert state == CalState.DONE, f"ended {state} ({cal.abort_reason})"
        assert cal.result["virtual_y"] == pytest.approx(0.5, abs=0.05)
        # No provenance key on a level run: powered payloads are unchanged.
        assert "vs_fpm" not in cal.result["curve"]

    def test_sink_target_change_mid_hold_resettles(self, clock):
        # The spinbox is the glider's throttle: changing the sink target
        # mid-hold must drop the ready gate, chase the new sink, and re-arm
        # once settled there — exactly like a power change on a powered
        # aircraft.
        ac = FakePlantAircraft(coupling=0.5, trim_natural=0.1)
        cal = TrimCalibrator(ac)
        cal.vs_target = -0.5
        cal.start(assist=True)
        assert run_until(cal, ac, clock, lambda: cal.assist_stable, 180), \
            f"never stabilized on the first sink ({cal.state}, {cal.abort_reason})"
        assert ac.vs == pytest.approx(-0.5, abs=0.15)

        cal.vs_target = -1.5   # user turns the spinbox while holding
        assert run_until(cal, ac, clock, lambda: not cal.assist_stable, 5), \
            "ready gate must drop while chasing the new sink"
        assert run_until(cal, ac, clock, lambda: cal.assist_stable, 240), \
            f"never re-settled on the new sink (vs {ac.vs:+.2f})"
        # The gate's contract: settled within STABLE_VS_TOL of the NEW target
        # (the outer integral keeps converging beyond that, but stable may
        # legitimately arm anywhere inside the tolerance).
        assert abs(ac.vs - (-1.5)) <= cal.STABLE_VS_TOL, \
            f"stable armed at vs {ac.vs:+.2f}, outside tolerance of -1.5"

    def test_can_start_judges_glider_against_target(self, clock):
        # Descending at ~590 fpm (3.0 m/s), beyond START_MAX_VS.
        ac = FakePlantAircraft()
        cal = TrimCalibrator(ac)
        telem = ac.telem(VerticalSpeed=-3.0)
        cal.vs_target = 0.0  # powered: "level off first"
        ok, msg = cal.can_start(telem)
        assert not ok and "climbing/descending" in msg
        cal.vs_target = -3.0  # glider holding that sink: ready
        ok, msg = cal.can_start(telem)
        assert ok, msg

    def test_vs_err_is_relative_to_target(self, clock):
        ac = FakePlantAircraft()
        cal = TrimCalibrator(ac)
        cal.vs_target = -0.5
        assert cal._vs_err(-0.5) == pytest.approx(0.0)
        assert cal._vs_err(0.0) == pytest.approx(0.5)
        assert cal._vs_err(None) == pytest.approx(0.5)
