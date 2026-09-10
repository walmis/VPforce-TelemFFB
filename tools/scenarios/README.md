# Profile-matching scenario bench

Swaps a synthetic `defaults.xml` + `userconfig_v2.xml` pair into the repo root so a
dev build can be flown through each situation profile matching has to get right,
without owning every aircraft involved.

Run it with no arguments for the menu, which is where a whole test session lives:

```
py tools/scenarios/scenario.py
```

Pick a scenario by number, `v` to print what the app should be showing, `a` to point
verification at whichever livery of the aircraft you actually own, `q` to quit and put
your real config back. Start TelemFFB however you normally do and come back to the menu
to switch scenario; picking one while it is still running is refused, since the app
would write over the swap.

The bench refuses to run at all unless `dev_build` and `dev_userconfig` are both True in
`telemffb/globals.py` and a `userconfig_v2.xml` exists in the repo root. Without dev mode
the app reads and writes your real config in `%LOCALAPPDATA%`, and a swap here would
touch files nothing looks at.

The individual commands still work if you want them:

```
py tools/scenarios/scenario.py list
py tools/scenarios/scenario.py use <name>
py tools/scenarios/scenario.py verify
py tools/scenarios/scenario.py restore
```

`use` copies your real `defaults.xml`, `userconfig_v2.xml` and `match_history.json`
into `_scenario_backup/` the first time it runs, then writes the scenario over them.
`restore` puts them back and removes the backup. Anything the app writes while a
scenario is active (a recorded match, an accepted offer) is discarded with it.

**Restore before doing any real work.** `list` says loudly when a scenario is
swapped in.

## How a scenario is built

Each one is generated from your untouched `defaults.xml`, never from whatever is
currently swapped in, so the fixtures cannot go stale and cannot compound. A
scenario names curated patterns to remove (standing for "that profile had not
shipped yet"), curated rows to add (standing for "a tighter profile just shipped"),
the userconfig rows to write, and optionally a seeded `match_history.json`.

To add one, add an entry to `SCENARIOS` in `scenario.py`. Pick an aircraft you can
actually load, and one whose curated pattern carries enough settings to see the
difference.

## Verify

`verify` resolves the active scenario headlessly and prints the pattern that wins,
whose it is, the resolved values, the other patterns that also match, and the offer
that should appear. Run it before launching so you know what you are looking for,
and treat a mismatch between it and the running app as a bug in the app.

## The aircraft

`C172SP Classic Passengers`, chosen because the curated `C172SP.*` carries nine
settings across three device scopes plus a trim calibration, and its pattern is
short enough that a hand-made user pattern out-specifies it.
