# VPforce TelemFFB - MSFS toolbar settings panel

An in-sim toolbar panel (built on the [bymaximus toolbar window
template](https://github.com/bymaximus/msfs2020-toolbar-window-template))
that shows the current aircraft's TelemFFB settings and lets you edit them
without leaving the cockpit - sliders, toggles, and choice pills sized for
VR laser-pointer / controller interaction.

It talks to `telemffb/api_server.py`, a small local HTTP API baked into the
TelemFFB app. That server only runs while MSFS is the connected sim (see
`_maybe_start_api_server` / `_maybe_stop_api_server` in `main.py`), started
by the master TelemFFB instance on the first MSFS telemetry frame and
stopped on sim exit. The panel itself is a static page bundled into the MSFS
package (`html_ui/InGamePanels/VpforceSettings/panel.html`), so opening the
toolbar panel works regardless of exactly when the API server comes up; it
just shows "Waiting for TelemFFB / MSFS aircraft..." until `GET
http://127.0.0.1:9010/api/status` reports connected.

## What's exposed

`api_server.py` reads the same settings the desktop Settings panel shows
(`xmlutils.read_single_model()` for the current sim/class/aircraft) and
keeps only the ones that map to a plain toggle, choice list, or slider:

- **bool** -> toggle
- **list / anylist / enumlist** -> choice pills (the free-text entry that
  `anylist` allows on desktop is dropped - VR has no keyboard)
- **float / negfloat / n_float / pct_float / cfgfloat / d_float / d_int /
  spin_int** -> a slider, with the same min/max/step math as the desktop
  sliders (see `_build_control()` in api_server.py for the per-datatype
  formulas)

Left out entirely: anything that opens a dialog on desktop (`advspr`,
`advgs`, `trimcal`, `configurator`), anything that needs typed input
(`text`, `path`, `int`, `anyfloat`), group headers, and the `type` field
(changing an aircraft's effects class isn't really a "setting").

Writes go through `SettingsManager.write_to_xml()`, the same call the
desktop UI makes, so changes land in `userconfig.xml` exactly like they
would from the desktop Settings panel. If the aircraft's active profile is
`Built-In`, writing a setting forks it into a new `Auto User` profile (this
mirrors existing `ConfigWriter` behavior) - the API refreshes its own
`active_profile` immediately after a write so a `GET` right after a `POST`
never reads a stale value.

## Building the package

Requires the MSFS SDK (`MSFS_SDK` environment variable pointing at it).

```
build.bat
```

This runs `fspackagetool` against `vpforce-telemffb-panel\Build\vpforce-telemffb-panel.xml`,
copies the compiled `.spb` into `vpforce-telemffb-panel\InGamePanels\`, and
regenerates `layout.json` via `build_layout.py` (which just walks the
package folder and writes real file sizes/timestamps - no need to
hand-maintain that file after edits).

**Unverified:** the `.spb` output filename in `build.bat` is inferred from
how the upstream template's build.bat named it (matching the `AssetGroup`
name, not the `<Filename>` tag in the `PackageSources` XML) - I couldn't
run `fspackagetool` myself to confirm. If the copy step in `build.bat`
fails, check what `fspackagetool` actually produced under
`vpforce-telemffb-panel\Build\Packages\vpforce-telemffb-panel\Build\` and
adjust the source filename in `build.bat` to match.

Then copy the `vpforce-telemffb-panel` folder (**not** its `Build`
subfolder) into your Community folder.

## Not yet tuned / tested in-sim

I don't have an MSFS install to test against, so a few things are
placeholder and worth checking once you can actually open the panel:

- `minWidth`/`minHeight`/`defaultWidth`/`defaultHeight`/`defaultTop`/`defaultRight`
  in `Build/PackageSources/vpforce-telemffb-panel.xml` are guessed (scaled
  up from the template's tiny single-webpage-viewer defaults to something
  more reasonable for a scrollable settings list) - resize/reposition to
  taste.
- The toolbar icon is a plain placeholder SVG (three sliders) - swap
  `html_ui/Textures/Menu/toolbar/ICON_TOOLBAR_VPFORCE_TELEMFFB_SETTINGS.svg`
  for real artwork if you want.
- `panel.js` hardcodes `http://127.0.0.1:9010` as the API base - keep this
  in sync if you ever start the server on a different port (see the
  `start_api_server()` call in `main.py`).
