"""Unit tests for the elevator virtual_y auto-calibration engine (TrimCalibrator).

These tests are fully headless: no FFB device, no simulator. The closed-loop
tests drive the calibrator against an in-test ``FakePlantAircraft`` — a minimal
implementation of the aircraft interface the calibrator uses, plus a first-order
pitch/roll toy model — over a monkeypatched deterministic clock.
"""
import time

import pytest

from telemffb.sim.BaseTelemetryData import BaseTelemetryData
from telemffb.sim.msfs_xp.TrimCalibrator import TrimCalibrator, CalState

pytestmark = [pytest.mark.unit, pytest.mark.msfs, pytest.mark.joystick]


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

    def send_event_to_msfs(self, event, data=0):
        self.events.append((event, data))


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

    def _apply_trim_event(self, data):
        # MSFS AXIS_* events are sign-inverted relative to the SimVar
        # read-backs, so the "sim" negates the received value.
        return -data / 16383.0

    # --- plant integration ---
    def step(self, dt):
        # trim is applied by the "sim": the last commanded AXIS_ELEV_TRIM_SET.
        for event, data in self._simconnect.events:
            if event == "AXIS_ELEV_TRIM_SET":
                new_trim = self._apply_trim_event(data)
                self.max_trim_step = max(self.max_trim_step, abs(new_trim - self.trim))
                self.trim = new_trim
        self._simconnect.events.clear()

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
        return BaseTelemetryData(initial=data)


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
        # Some aircraft respond to AXIS_ELEV_TRIM_SET with the opposite sign
        # (cf. the trimwheel_axis_invert user setting). The engine must detect
        # the read-back moving against the command, flip its command sign, and
        # still produce the correct gain.
        class InvertedTrimPlant(FakePlantAircraft):
            def _apply_trim_event(self, data):
                return data / 16383.0   # no negation: mirrored response

        ac = InvertedTrimPlant(coupling=0.5, physical_y=1.0)
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
        class DeafTrimPlant(FakePlantAircraft):
            def _apply_trim_event(self, data):
                return self.trim        # trim never moves

        ac = DeafTrimPlant()
        cal = TrimCalibrator(ac)
        cal.start()
        state = run_to_completion(cal, ac, clock)
        assert state == CalState.ABORT
        assert "not responding" in cal.abort_reason


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

    def test_takeover_preserves_spring_offsets(self, clock):
        # The hold spring must keep the stick where the trim-following spring
        # left it — snapping the center to 0/0 yanks the controls at start.
        ac = FakePlantAircraft()
        ac.spring_y.cpOffset = 819    # trimmed rest position (~20% of 4096)
        ac.spring_x.cpOffset = -100
        cal = TrimCalibrator(ac)
        cal.start()
        for _ in range(5):
            clock.advance(1 / 30.0)
            cal.update(ac.telem())
            ac.step(1 / 30.0)
        assert ac.spring_y.cpOffset == 819
        assert ac.spring_x.cpOffset == -100

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
        # After the baseline window, the probe nudge rides on the baseline.
        for _ in range(int(cal.PROBE_BASELINE_S * 30) + 2):
            clock.advance(1 / 30.0)
            cal.update(ac.telem())
            ac.step(1 / 30.0)
        assert ac.cmd_elev == pytest.approx(expected_base + cal.PROBE_U, abs=1e-6)

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
