"""The pedal axis-mapping check.

By firmware design an axis with no motor connected reports exactly 0,
so ANY nonzero Y on a pedals device means the pedal is mapped to Y -
the check is deliberately instantaneous.  The error message carries the
observed axis values, so a user's screenshot of the notification is the
diagnostic when a report disputes the finding.

That firmware contract is VPforce's, which is why the check applies to
VPforce hardware only - see TestDirectInputIsExempt.
"""
from telemffb.sim.base.PedalSpringOverrideMixIn import PedalSpringOverrideMixIn


class FakePedals(PedalSpringOverrideMixIn):
    """The mixin with its collaborators stubbed: axes are scripted, errors
    are recorded."""

    def __init__(self):                    # no super(): device-free
        self.errors = []
        self._axes = (0.0, 0.0)

    def is_pedals(self):
        return True

    def _get_device_axes(self):
        return self._axes

    def flag_error(self, message):
        self.errors.append(message)

    def feed(self, *frames):
        for x, y in frames:
            self._axes = (x, y)
            self.verify_pedal_axis()


class TestPedalAxisCheck:
    def test_nonzero_y_with_x_at_zero_is_flagged(self):
        pedals = FakePedals()
        pedals.feed((0.0, 0.25))
        assert pedals.errors
        assert 'Y axis used instead of X' in pedals.errors[0]

    def test_the_message_carries_the_observed_values(self):
        """A disputed report resolves from the notification screenshot
        alone: the values are what TelemFFB actually read from the HID
        report."""
        pedals = FakePedals()
        pedals.feed((0.0, 0.25))
        assert 'X=0.000' in pedals.errors[0]
        assert 'Y=0.250' in pedals.errors[0]

    def test_the_message_names_the_connected_device(self, monkeypatch):
        """The ident is the CONNECTED hardware's name, so a wrong device
        sitting in the pedals slot (a collective, say) exposes itself
        right in the message."""
        import telemffb.globals as G
        monkeypatch.setattr(G, 'device_ident', 'Rhino FFB Collective',
                            raising=False)
        pedals = FakePedals()
        pedals.feed((0.0, 0.25))
        assert 'device: Rhino FFB Collective' in pedals.errors[0]

    def test_no_device_ident_degrades_gracefully(self, monkeypatch):
        import telemffb.globals as G
        monkeypatch.delattr(G, 'device_ident', raising=False)
        pedals = FakePedals()
        pedals.feed((0.0, 0.25))
        assert 'device: unknown device' in pedals.errors[0]

    def test_a_disconnected_y_axis_stays_quiet(self):
        """Firmware contract: no motor on Y = Y reads exactly 0."""
        pedals = FakePedals()
        pedals.feed((0.0, 0.0), (-0.5, 0.0), (0.3, 0.0))
        assert pedals.errors == []

    def test_x_movement_suppresses_the_flag(self):
        pedals = FakePedals()
        pedals.feed((-0.008, 0.25))
        assert pedals.errors == []

    def test_the_error_keeps_asserting_while_mismatched(self):
        """flag_error fires per frame so the master's error state (and
        tray icon) stays held; the notification popup is de-duplicated
        downstream."""
        pedals = FakePedals()
        pedals.feed((0.0, 0.25), (0.0, 0.3), (0.0, 0.25))
        assert len(pedals.errors) == 3

    def test_not_pedals_is_a_no_op(self):
        pedals = FakePedals()
        pedals.is_pedals = lambda: False
        pedals.feed((0.0, 0.9))
        assert pedals.errors == []


class TestDirectInputIsExempt:
    """A generic DirectInput device makes neither half of the contract:
    its unused axes may idle with noise rather than a clean 0, and the
    remedy the message names (VPConfigurator) does not exist for
    third-party hardware - theirs is the FFB Axis pulldown.  A wrong
    choice there is self-evident: the spring lands on an axis that does
    not move."""

    def test_a_directinput_pedal_is_never_checked(self, monkeypatch):
        import telemffb.globals as G
        monkeypatch.setattr(G, 'device_di_guid', '{GUID}', raising=False)
        pedals = FakePedals()
        pedals.feed((0.0, 0.25))
        assert pedals.errors == []

    def test_a_vpforce_pedal_still_is(self, monkeypatch):
        import telemffb.globals as G
        monkeypatch.setattr(G, 'device_di_guid', None, raising=False)
        pedals = FakePedals()
        pedals.feed((0.0, 0.25))
        assert pedals.errors
