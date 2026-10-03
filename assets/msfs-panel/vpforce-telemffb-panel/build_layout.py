"""Regenerate layout.json for this package.

MSFS community packages ship a layout.json cataloguing every content file
with its size and a Windows FILETIME timestamp. fspackagetool writes one for
the .spb it compiles, but the html_ui/manifest files sitting alongside it
need the same treatment - this script walks the package folder and writes
a fresh layout.json rather than hand-maintaining stale sizes/dates.

Run this after any edit to html_ui/*, manifest.json, or after copying a
freshly-built .spb into InGamePanels/ (see build.bat).
"""
import json
import os
import re

PACKAGE_ROOT = os.path.dirname(os.path.abspath(__file__))
INCLUDE_TOP_LEVEL = ("html_ui", "InGamePanels")

FILETIME_EPOCH_OFFSET = 11644473600  # seconds between 1601-01-01 and 1970-01-01


def to_filetime(mtime: float) -> int:
    return int((mtime + FILETIME_EPOCH_OFFSET) * 10**7)


def stamp_panel_version():
    """Copy manifest.json's package_version into panel.js, which shows it:
    the panel cannot read the manifest itself."""
    with open(os.path.join(PACKAGE_ROOT, "manifest.json"), encoding="utf-8") as f:
        version = json.load(f)["package_version"]
    path = os.path.join(PACKAGE_ROOT, "html_ui", "InGamePanels", "VpforceSettings", "panel.js")
    with open(path, "rb") as f:
        text = f.read().decode("utf-8")
    stamped, found = re.subn(r'const PANEL_VERSION = "[^"]*";',
                             f'const PANEL_VERSION = "{version}";', text, count=1)
    if not found:
        raise SystemExit("panel.js has no PANEL_VERSION constant to stamp")
    if stamped != text:
        with open(path, "wb") as f:
            f.write(stamped.encode("utf-8"))


def main():
    stamp_panel_version()
    entries = []
    for top in INCLUDE_TOP_LEVEL:
        top_path = os.path.join(PACKAGE_ROOT, top)
        if not os.path.isdir(top_path):
            continue
        for dirpath, _dirnames, filenames in os.walk(top_path):
            for fname in filenames:
                full = os.path.join(dirpath, fname)
                rel = os.path.relpath(full, PACKAGE_ROOT).replace(os.sep, "/")
                st = os.stat(full)
                entries.append({
                    "path": rel,
                    "size": st.st_size,
                    "date": to_filetime(st.st_mtime),
                })

    entries.sort(key=lambda e: e["path"].lower())
    layout = {"content": entries}

    out_path = os.path.join(PACKAGE_ROOT, "layout.json")
    # LF regardless of platform: the file ships as written
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(layout, f, indent=2)
        f.write("\n")

    print(f"Wrote {out_path} with {len(entries)} entries")


if __name__ == "__main__":
    main()
