"""
Unit tests for the X-Plane axis-override handshake in MsfsXpSimConnectMixIn.

TelemFFB takes an axis away from X-Plane by sending ``OVERRIDE:<role>=true``
to the plugin, which latches an override dataref in the sim.  That dataref
survives everything on the TelemFFB side — a settings change, a reconnect,
even a full restart — so the release half of the handshake has to be driven
off what the sim reports it actually has latched (``jOvrd`` / ``pOvrd`` /
``cOvrd``, echoed in every telemetry frame) rather than off what this process
remembers sending.

These tests cover that reconciliation, including the reported regression: a
user who disabled axis control for one device only
(``local_disable_axis_control``) while leaving ``telemffb_controls_axes`` on
got no release command at all, and had to clear the override from the
plugin's own menu and restart TelemFFB before their non-FFB stick worked.
"""
from tests.framework.base import BaseTelemetryEffectTestCase
from tests.framework.utils import TelemetryDataBuilder
from telemffb.sim.msfs_xp.MsfsXpSimConnectMixIn import (
    MsfsXpSimConnectMixIn,
    XP_OVERRIDE_ECHO_GRACE_S,
)

_ACTIVE_ATTR = "_MsfsXpSimConnectMixIn__xplane_axis_override_active"
_SENT_AT_ATTR = "_MsfsXpSimConnectMixIn__xplane_override_sent_at"


class _FakeSocket:
    """Captures datagrams instead of putting them on the wire."""

    def __init__(self):
        self.sent = []

    def sendto(self, payload, addr):
        self.sent.append(payload.decode("utf-8"))


class XpOverrideTestCase(BaseTelemetryEffectTestCase):
    """Shared harness for driving toggle_xp_control() one frame at a time."""

    def make_instance(self, controls_axes=True, local_disable=False):
        instance = self.create_test_instance(MsfsXpSimConnectMixIn)
        instance.telemffb_controls_axes = controls_axes
        instance.local_disable_axis_control = local_disable
        socket = _FakeSocket()
        instance._socket = socket
        return instance, socket

    def frame(self, instance, ffb_type="joystick", aircraft_class=None, **reported):
        """Feed one telemetry frame and run the override reconciliation."""
        instance._telem_data = self.telemetry(ffb_type, aircraft_class, **reported)
        instance.toggle_xp_control()

    def telemetry(self, ffb_type="joystick", aircraft_class=None, **reported):
        builder = TelemetryDataBuilder().ffb_type(ffb_type)
        if aircraft_class is not None:
            builder = builder.set("AircraftClass", aircraft_class)
        for key, value in reported.items():
            builder = builder.set(key, value)
        return builder.build()

    def believes_active(self, instance, value):
        setattr(instance, _ACTIVE_ATTR, value)


class TestXpOverrideRelease(XpOverrideTestCase):
    """The release half of the handshake — the reported bug."""

    def test_local_disable_releases_override(self):
        """Disabling axis control for THIS device alone must release the axis.

        The regression: the release branch only tested
        ``not telemffb_controls_axes``, so a per-device disable left the
        override latched in the sim and the user's own stick inert.
        """
        instance, socket = self.make_instance(controls_axes=True, local_disable=True)
        self.believes_active(instance, True)

        self.frame(instance, jOvrd=1)

        assert socket.sent == ["OVERRIDE:joystick=false"]

    def test_release_on_initialization(self):
        """A fresh instance releases an override the sim still has latched.

        Nothing has been sent this session, so the local flag says "inactive"
        while the sim says otherwise — the case that previously required the
        plugin's "Clear All Overrides" menu item.
        """
        instance, socket = self.make_instance(controls_axes=True, local_disable=True)

        self.frame(instance, jOvrd=1)

        assert socket.sent == ["OVERRIDE:joystick=false"]

    def test_global_disable_releases_override(self):
        """Turning axis control off globally still releases the axis."""
        instance, socket = self.make_instance(controls_axes=False)
        self.believes_active(instance, True)

        self.frame(instance, jOvrd=1)

        assert socket.sent == ["OVERRIDE:joystick=false"]

    def test_pedals_release_uses_pedal_state_key(self):
        """Each role reconciles against its own reported state key."""
        instance, socket = self.make_instance(controls_axes=True, local_disable=True)

        self.frame(instance, ffb_type="pedals", jOvrd=0, pOvrd=1)

        assert socket.sent == ["OVERRIDE:pedals=false"]


class TestXpOverrideAcquire(XpOverrideTestCase):
    """The acquire half, and the no-op case."""

    def test_enabled_axis_control_takes_the_override(self):
        instance, socket = self.make_instance(controls_axes=True, local_disable=False)

        self.frame(instance, jOvrd=0)

        assert socket.sent == ["OVERRIDE:joystick=true"]

    def test_no_command_when_sim_already_matches(self):
        """A matching state must not put a command on the wire every frame."""
        instance, socket = self.make_instance(controls_axes=True, local_disable=False)
        self.believes_active(instance, True)

        self.frame(instance, jOvrd=1)
        self.frame(instance, jOvrd=1)

        assert socket.sent == []

    def test_stale_local_flag_self_heals(self):
        """Overrides cleared behind our back are re-acquired.

        The plugin's "Clear All Overrides" menu item zeroes the dataref
        without telling TelemFFB.  A device that still wants the axis should
        notice and take it back rather than sitting inert believing it holds
        an override it has actually lost.
        """
        instance, socket = self.make_instance(controls_axes=True, local_disable=False)
        self.believes_active(instance, True)

        self.frame(instance, jOvrd=0)

        assert socket.sent == ["OVERRIDE:joystick=true"]


class TestXpOverrideEchoGrace(XpOverrideTestCase):
    """Frames already in flight when a command is sent must not cause a resend."""

    def test_in_flight_frame_does_not_resend(self):
        instance, socket = self.make_instance(controls_axes=True, local_disable=False)

        # Frame 1 sends the acquire; frame 2 still carries the pre-command
        # state because the plugin had not applied it when that frame was built.
        self.frame(instance, jOvrd=0)
        self.frame(instance, jOvrd=0)

        assert socket.sent == ["OVERRIDE:joystick=true"]

    def test_persistent_mismatch_retries_after_grace(self):
        """A command that never took effect is retried, not abandoned."""
        instance, socket = self.make_instance(controls_axes=True, local_disable=False)

        self.frame(instance, jOvrd=0)
        # Age the send past the grace window: the sim still disagrees, so the
        # command is assumed lost and reissued.
        setattr(instance, _SENT_AT_ATTR,
                getattr(instance, _SENT_AT_ATTR) - (XP_OVERRIDE_ECHO_GRACE_S + 1.0))
        self.frame(instance, jOvrd=0)

        assert socket.sent == ["OVERRIDE:joystick=true", "OVERRIDE:joystick=true"]


class TestXpOverrideFallbacks(XpOverrideTestCase):
    """Roles and plugin builds that report no state."""

    def test_missing_state_key_falls_back_to_local_flag(self):
        """An older plugin reports no jOvrd; the local flag still drives it."""
        instance, socket = self.make_instance(controls_axes=True, local_disable=True)
        self.believes_active(instance, True)

        self.frame(instance)

        assert socket.sent == ["OVERRIDE:joystick=false"]

    def test_missing_state_key_stays_quiet_when_flag_agrees(self):
        instance, socket = self.make_instance(controls_axes=True, local_disable=True)

        self.frame(instance)

        assert socket.sent == []

    def test_trimwheel_never_sends_override(self):
        """The plugin has no trimwheel override keyword."""
        instance, socket = self.make_instance(controls_axes=True, local_disable=False)

        self.frame(instance, ffb_type="trimwheel")

        assert socket.sent == []


class TestXpOverrideCollective(XpOverrideTestCase):
    """Collective takes part in the handshake, but only on a helicopter."""

    def test_helicopter_collective_takes_the_override(self):
        instance, socket = self.make_instance(controls_axes=True, local_disable=False)

        self.frame(instance, ffb_type="collective",
                   aircraft_class="Helicopter", cOvrd=0)

        assert socket.sent == ["OVERRIDE:collective=true"]

    def test_helicopter_collective_honors_local_disable(self):
        instance, socket = self.make_instance(controls_axes=True, local_disable=True)

        self.frame(instance, ffb_type="collective",
                   aircraft_class="Helicopter", cOvrd=1)

        assert socket.sent == ["OVERRIDE:collective=false"]

    def test_fixed_wing_collective_never_takes_the_override(self):
        """On a fixed wing the plugin's "collective" is plain prop pitch."""
        instance, socket = self.make_instance(controls_axes=True, local_disable=False)

        self.frame(instance, ffb_type="collective",
                   aircraft_class="PropellerAircraft", cOvrd=0)

        assert socket.sent == []

    def test_fixed_wing_collective_releases_a_latched_override(self):
        """A prop-pitch override left over from a helicopter session is released."""
        instance, socket = self.make_instance(controls_axes=True, local_disable=False)

        self.frame(instance, ffb_type="collective",
                   aircraft_class="PropellerAircraft", cOvrd=1)

        assert socket.sent == ["OVERRIDE:collective=false"]


class TestXpOverrideReleaseOnClose(XpOverrideTestCase):
    """Shutdown must not leave an override latched in the sim."""

    def release(self, instance, ffb_type="joystick", is_xplane=True):
        instance._telem_data = self.telemetry(ffb_type)
        instance._test_sim_is_xplane = is_xplane
        instance.release_xp_axis_override()

    def test_release_sends_false(self):
        instance, socket = self.make_instance(controls_axes=True, local_disable=False)
        self.believes_active(instance, True)

        self.release(instance)

        assert socket.sent == ["OVERRIDE:joystick=false"]

    def test_release_is_sent_even_when_no_override_is_believed_held(self):
        """"Believed inactive" is exactly the state that cannot be trusted."""
        instance, socket = self.make_instance(controls_axes=True, local_disable=False)

        self.release(instance)

        assert socket.sent == ["OVERRIDE:joystick=false"]

    def test_release_uses_the_device_role(self):
        instance, socket = self.make_instance(controls_axes=True, local_disable=False)

        self.release(instance, ffb_type="pedals")

        assert socket.sent == ["OVERRIDE:pedals=false"]

    def test_no_release_when_sim_is_not_xplane(self):
        instance, socket = self.make_instance(controls_axes=True, local_disable=False)

        self.release(instance, is_xplane=False)

        assert socket.sent == []

    def test_no_release_for_trimwheel(self):
        instance, socket = self.make_instance(controls_axes=True, local_disable=False)

        self.release(instance, ffb_type="trimwheel")

        assert socket.sent == []
