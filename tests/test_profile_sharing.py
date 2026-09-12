import xml.etree.ElementTree as ET

import pytest

from telemffb.ProfileSharing import (
    LocalProfileShareService,
    ROOT_TAG,
    SharedProfileItem,
    SharedProfileMetadata,
)


@pytest.mark.unit
def test_share_metadata_round_trips():
    service = LocalProfileShareService()
    root = ET.Element(ROOT_TAG)
    metadata = SharedProfileMetadata(
        title="Cessna profile",
        author="Tester",
        notes="Light spring profile",
        telemffb_version="local-test",
        exported_at="2026-07-07T12:00:00+00:00",
        items=[
            SharedProfileItem(
                sim="MSFS",
                cls="PropellerAircraft",
                model="C172.*",
                profile="Community",
            )
        ],
        devices=["joystick", "any"],
        omitted_vpconf_refs=["C:/Profiles/example.vpconf"],
    )

    service.add_metadata(root, metadata)

    parsed = service.parse_metadata(root)
    assert parsed == metadata


@pytest.mark.unit
def test_validate_import_rejects_bad_device_and_missing_profile():
    service = LocalProfileShareService()
    root = ET.fromstring(
        """
        <TelemFFB_v2>
            <models>
                <name>spring_gain</name>
                <model>C172.*</model>
                <value>0.5</value>
                <sim>MSFS</sim>
                <device>gamepad</device>
            </models>
        </TelemFFB_v2>
        """
    )

    errors = service.validate_import_root(root)

    assert "<models> entry has unsupported device 'gamepad'." in errors
    assert "Model setting is missing required profile tag." in errors


@pytest.mark.unit
def test_validate_import_allows_unprofiled_model_type():
    service = LocalProfileShareService()
    root = ET.fromstring(
        """
        <TelemFFB_v2>
            <models>
                <name>type</name>
                <model>C172.*</model>
                <value>PropellerAircraft</value>
                <sim>MSFS</sim>
                <device>any</device>
            </models>
        </TelemFFB_v2>
        """
    )

    assert service.validate_import_root(root) == []


@pytest.mark.unit
def test_find_omitted_vpconf_refs_ignores_empty_default_values():
    service = LocalProfileShareService()
    root = ET.fromstring(
        """
        <TelemFFB_v2>
            <models>
                <name>vpconf</name>
                <model>C172.*</model>
                <value>C:/Profiles/example.vpconf</value>
                <sim>MSFS</sim>
                <device>joystick</device>
                <profile>Community</profile>
            </models>
            <models>
                <name>vpconf</name>
                <model>Other</model>
                <value>-</value>
                <sim>MSFS</sim>
                <device>joystick</device>
                <profile>Community</profile>
            </models>
        </TelemFFB_v2>
        """
    )

    assert service.find_omitted_vpconf_refs(root) == ["C:/Profiles/example.vpconf"]
