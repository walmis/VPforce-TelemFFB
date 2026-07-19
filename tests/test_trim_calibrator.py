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
#  Optional airspeed-settle hold before the sweep
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

    def test_settle_hold_runs_for_configured_time(self, clock):
        ac = FakePlantAircraft()
        cal = TrimCalibrator(ac)
        cal.settle_before_sweep = True
        cal.start()
        state, settle_frames = self._run_tracking_settle(cal, ac, clock)
        assert state == CalState.DONE, cal.abort_reason
        # ~20 s at 30 fps; allow a little slack for phase-entry framing
        assert settle_frames >= cal.SPEED_SETTLE_S * 30 * 0.95
        assert cal.result["virtual_y"] == pytest.approx(0.5, abs=0.05)

    def test_settle_hold_skipped_when_disabled(self, clock):
        ac = FakePlantAircraft()
        cal = TrimCalibrator(ac)
        cal.settle_before_sweep = False
        cal.start()
        state, settle_frames = self._run_tracking_settle(cal, ac, clock)
        assert state == CalState.DONE, cal.abort_reason
        assert settle_frames == 0, "SPEED_SETTLE must be skipped when disabled"
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

    def test_assist_retrim_is_one_slope_sized_move(self, clock):
        # Trim must be the primary corrector: with the trim-response slope
        # learned during neutralization, a power change is answered by ONE
        # Newton move straight to the predicted level point (plus at most a
        # small cleanup) — not a blind probe-and-hunt sequence with the
        # elevator parked on the load in between.
        ac, cal = self._start_assist(clock, coupling=0.5, trim_natural=0.1)
        assert run_until(cal, ac, clock, lambda: cal.assist_stable, 180)
        assert cal._trim_slope_est == pytest.approx(-0.5, abs=0.15), \
            "neutralization must hand the assistant its measured slope"

        moves = []
        orig = cal._begin_neut_step
        cal._begin_neut_step = lambda target: (moves.append(target), orig(target))
        start_trim = ac.trim
        ac.trim_natural = 0.30
        assert run_until(cal, ac, clock, lambda: not cal.assist_stable, 5)
        assert run_until(cal, ac, clock, lambda: cal.assist_stable, 240)
        assert ac.trim == pytest.approx(0.30, abs=0.05)
        # Slope-sized moves capped at NEUT_STEP_MAX: a 0.24 correction is at
        # most two full bites plus a cleanup — never a blind probe hunt.
        assert len(moves) <= 3, f"expected slope-sized moves, got {moves}"
        assert moves[0] - start_trim >= cal.NEUT_STEP_MAX * 0.9, \
            f"first move must take a full slope-sized bite ({moves})"
        assert moves == sorted(moves), f"moves must not reverse direction ({moves})"


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

        moves = []
        orig = cal._begin_neut_step
        cal._begin_neut_step = lambda tgt: (moves.append(tgt), orig(tgt))
        # Seed the watchdog as if the cycle was already confirmed (detection
        # latency on a fresh oscillation is a separate, accepted window);
        # the run below replaces the seed with real measured gaps.
        cal._osc_recent = [(0.5, clock.t)]
        cal._osc_last_ext_t = clock.t
        cal._osc_half_gaps = [12.0, 12.0]
        # The closed loop rejects most of the forcing (the disturbance ends
        # up in the CONTROL signal, which is exactly what poisons the phase
        # measurements); this amplitude leaves a residual VS cycle whose
        # confirmed extrema clear the assist presence floor.
        ac.osc_amp = 0.25

        run_until(cal, ac, clock, lambda: False, 120)   # ride the porpoise

        assert cal.active, f"run died: {cal.abort_reason}"
        assert not moves, f"trim chased the phugoid: {moves}"
        assert abs(ac.trim) <= 0.02
        # (assist_stable MAY arm here: the residual cycle sits inside the
        # level tolerance and the period-matched mean shows a trimmed
        # aircraft — that meets the same bar a calm aircraft meets.)
        # the real cycle got measured and kept the protection alive
        assert cal._osc_period() == pytest.approx(24.0, abs=6.0)

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
        cal._osc_recent = [(0.5, clock.t)]
        cal._osc_last_ext_t = clock.t
        cal._osc_half_gaps = [12.0, 12.0]
        ac.osc_amp = 0.25
        run_until(cal, ac, clock, lambda: False, 60)   # ride the porpoise

        ac.osc_amp = 0.0    # damps out
        t0 = clock.t
        assert run_until(cal, ac, clock, lambda: cal.assist_stable, 30), \
            "gate never re-armed after the porpoise damped"
        assert clock.t - t0 <= 25.0, \
            f"gate re-arm took {clock.t - t0:.1f}s after damping"
