"""Session teardown: effects TelemFFB allocated must be freed when the sim
exits and when the app closes - without disturbing effects the sim itself
created on the same device.

Two failure modes this locks down:

1. `notify_sim_exited` used to call only `on_timeout()`, which is *pause*
   semantics: with `keep_forces_on_pause` it deliberately leaves spring /
   damper / inertia / friction / spring-adjuster running so the stick does
   not go limp mid-session.  On a sim *exit* the aircraft is dropped right
   after, so those forces stayed on the device forever.

2. Application exit never freed effects at all; the process simply died.

The fix must stay per-effect.  A device-level reset (`reset_effects()`,
which writes DEVICE_CONTROL / CONTROL_RESET) would also wipe the effects a
sim renders into shared VPforce hardware - DCS cannot recreate its spring
afterwards and needs restarting.
"""
import pytest

pytest.importorskip("PyQt6")

from telemffb.hw.ffb_rhino import HapticEffect

pytestmark = [pytest.mark.unit]


class FakeHandle:
    """Stand-in for FFBEffectHandle / DInputEffectHandle."""

    def __init__(self, effect_id=1):
        self.effect_id = effect_id
        self.name = "Spring"
        self.destroyed = False

    def destroy(self):
        self.destroyed = True
        self.effect_id = None


def _live_effect(name="test_effect"):
    """A HapticEffect with an allocated handle, as after .start()."""
    eff = HapticEffect()
    eff.name = name
    eff._h_effect = FakeHandle()
    return eff


@pytest.fixture(autouse=True)
def isolate_registry():
    """The instance registry is class-level; keep tests independent."""
    import weakref
    saved = HapticEffect._instances
    HapticEffect._instances = weakref.WeakSet()
    yield
    HapticEffect._instances = saved


class TestDestroyAll:
    def test_frees_every_allocated_effect(self):
        effects = [_live_effect(f"e{i}") for i in range(3)]
        handles = [e._h_effect for e in effects]

        assert HapticEffect.destroy_all() == 3

        assert all(h.destroyed for h in handles)
        assert all(e._h_effect is None for e in effects)

    def test_covers_effects_outside_the_dispenser(self):
        """The advanced spring adjuster and the MSFS/X-Plane constant force
        are held directly on their mixins, not in G.effects - clearing the
        dispenser alone would leave them allocated on the device."""
        standalone = _live_effect("spring_adjuster")
        handle = standalone._h_effect

        HapticEffect.destroy_all()

        assert handle.destroyed

    def test_unallocated_effects_are_not_counted(self):
        # bind both: the registry holds weak references, so an unbound
        # effect can be collected before the teardown runs
        pending = HapticEffect()          # configured but never started
        started = _live_effect()
        assert HapticEffect.destroy_all() == 1
        assert pending._h_effect is None and started._h_effect is None

    def test_is_idempotent(self):
        effect = _live_effect()
        assert HapticEffect.destroy_all() == 1
        assert HapticEffect.destroy_all() == 0
        assert effect._h_effect is None

    def test_one_failure_does_not_abort_the_rest(self):
        bad = _live_effect("bad")
        good = _live_effect("good")

        def boom():
            raise OSError("device disconnected")
        bad._h_effect.destroy = boom

        HapticEffect.destroy_all()
        assert good._h_effect is None      # teardown continued past the failure
        bad._h_effect = None               # don't let __del__ re-raise at GC

    def test_never_issues_a_device_reset(self):
        """The whole point: freeing our blocks must not touch the sim's.

        A device-level reset would take DCS's spring with it, leaving the
        sim unable to recreate it until restarted.
        """
        class SpyDevice:
            connected = True  # FFBRhino interface, keep the live path

            def __init__(self):
                self.reset_calls = 0

            def reset_effects(self):
                self.reset_calls += 1

        spy = SpyDevice()
        saved_device = HapticEffect.device
        HapticEffect.device = spy
        try:
            _live_effect()
            HapticEffect.destroy_all()
        finally:
            HapticEffect.device = saved_device

        assert spy.reset_calls == 0


# the simconnect package (pulled in via TelemManager) leaks a file handle at
# import time; keep its ResourceWarning from failing these tests
@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
class TestSimExitTeardown:
    def test_sim_exit_frees_effects_despite_keep_forces_on_pause(self):
        """The regression: keep_forces_on_pause must not keep condition
        effects alive past the end of the session."""
        import telemffb.globals as G
        from telemffb.telem.TelemManager import TelemManager

        mgr = TelemManager.__new__(TelemManager)
        mgr._sim_exit_signaled = False
        mgr._process_check_deadline = None
        mgr.currentAircraftName = "TestAircraft"

        timeout_called = []

        class FakeAircraft:
            keep_forces_on_pause = True

            def on_timeout(self):
                timeout_called.append(True)   # leaves condition effects running

        mgr.currentAircraft = FakeAircraft()

        effect = _live_effect("spring")
        handle = effect._h_effect

        emitted = []
        mgr.sim_exited = type("Sig", (), {"emit": lambda _self, src: emitted.append(src)})()

        mgr.notify_sim_exited("IL2")

        assert timeout_called, "pause-path teardown should still run"
        assert handle.destroyed, "effects must be freed when the sim exits"
        assert mgr.currentAircraft is None
        assert emitted == ["IL2"]
