"""Support bundle contents — user-supplied report context.

The exception-report dialog collects an optional Discord username and
free-text notes; both must land in user_report.txt as the FIRST entry of
the bundle so support can map a bundle to a Discord user at a glance.
"""
import io
import zipfile

from telemffb.utils import create_support_bundle_data


def _entries(bundle_bytes):
    with zipfile.ZipFile(io.BytesIO(bundle_bytes)) as z:
        return z.namelist(), {n: z.read(n).decode("utf-8", "replace")
                              for n in z.namelist() if n.endswith(".txt")}


def test_user_info_written_as_first_entry(tmp_path):
    bundle = create_support_bundle_data(
        str(tmp_path), exceptions=None,
        user_info={"discord_username": "TestPilot#1234",
                   "notes": "Crashed while switching aircraft in MSFS."})
    names, texts = _entries(bundle)
    assert names[0] == "user_report.txt"
    report = texts["user_report.txt"]
    assert "Discord username: TestPilot#1234" in report
    assert "Crashed while switching aircraft in MSFS." in report


def test_user_info_fields_optional(tmp_path):
    bundle = create_support_bundle_data(
        str(tmp_path), exceptions=None,
        user_info={"discord_username": "", "notes": ""})
    names, texts = _entries(bundle)
    assert names[0] == "user_report.txt"
    report = texts["user_report.txt"]
    assert "Discord username: (not provided)" in report
    assert "(none)" in report


def test_no_user_info_omits_report_file(tmp_path):
    bundle = create_support_bundle_data(str(tmp_path), exceptions=None)
    names, _ = _entries(bundle)
    assert "user_report.txt" not in names
