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
http://127.0.0.1:9873/api/status` reports connected.

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

Then copy the `vpforce-telemffb-panel` folder (**not** its `Build`
subfolder) into your Community folder, and fully restart MSFS after any
edit under `html_ui/` - those files aren't compiled into the `.spb`, MSFS
just reads them directly from Community, but the toolbar seems to cache
panel/icon content for the life of the sim session.

Confirmed working on the MSFS 2024 SDK (`fspackagetool` needs `-nopause`,
not the 2020 template's `-nomirroring`, which doesn't exist there - the
`.spb` lands at `vpforce-telemffb-panel\Build\Packages\vpforce-telemffb-panel\Build\vpforce-telemffb-panel.spb`
as `build.bat` assumes). Toolbar icons load from
`html_ui/icons/toolbar/<icon-attribute-lowercased>.svg` - a different
convention than the 2020-era template used
(`html_ui/Textures/Menu/toolbar/`), found via the exact path MSFS's debug
console reported when the icon failed to load.

`panel.js` talks to the API on port 9873 (`API_BASE` at the top of the
file) - keep this in sync with the `start_api_server()` call in `main.py`
if you ever change it.

**Coherent GT's JS engine doesn't implement `Promise.prototype.finally()`**
(ES2018) - it throws `TypeError: undefined is not a function` at the call
site, silently killing whatever promise chain it's in. `panel.js` avoids it
everywhere in favor of the two-argument `.then(onSuccess, onError)` form
(ES2015). Keep that in mind if you extend the script - it's not just this
one method that might be missing; the safe move is to stick to broadly
-supported Promise/fetch APIs and check the in-sim debug console (Options →
General → Developer Options) after any change that touches promises.

## Not yet tuned

- `minWidth`/`minHeight`/`defaultWidth`/`defaultHeight`/`defaultTop`/`defaultRight`
  in `Build/PackageSources/vpforce-telemffb-panel.xml` are guessed (scaled
  up from the template's tiny single-webpage-viewer defaults to something
  more reasonable for a scrollable settings list) - resize/reposition to
  taste.
