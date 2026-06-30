# STEP_05 — Smoke test in MSFS

**No code changes.** Validation in MSFS with the user's actual hardware
(VPforce Rhino + chair-coupled bass shaker).

## Outputs

Two files, both created during this step under
`docs/shaker-polish-layers/`:

- `MSFS_LAYER_TEST.md` — per-effect subjective notes from the test run.
- `KNOWN_ISSUES.md` — anything discovered (or "no known issues" as the
  baseline content).

## Test matrix (minimum)

For each effect listed below, run a representative scenario and capture
notes. Aim for direct A/B comparisons where possible — toggle
`shakerGain` to 0 (or quit the shaker child) to compare "stick only" vs
"stick + layered shaker".

| Effect | Trigger | What to listen / feel for |
|--------|---------|---------------------------|
| `je_rumble_*` or `prop_rpm*` | Cruise + power sweep low-to-high | Body-coupled bass on shaker, fundamental on stick. Power-sweep should feel continuous, not phasey. |
| `touchdown` | Land at low Vs, then with a heavier sink rate | Single low body-thump on shaker; quick haptic crack on stick. Both should be one "event" — no perceived double-hit. |
| `gunfire` (DCS) **or** `gearmovement` (MSFS retract/extend) | Fire a burst / cycle the gear | Snap on stick, low rumble on shaker. For gear: each clunk-end should also pop on the shaker. |
| `buffeting` | Approach stall, hold | Continuous low rumble on shaker, mid texture on stick. No audible beating between layers. |
| `runway0` | Take-off roll | Shaker rumble grows with speed; stick texture overlaid. Should not "double" the same band that the chair already provides via Rhino mounting. |

## Per-effect note template

For each tested effect, capture in `MSFS_LAYER_TEST.md`:

```markdown
### <effect_name> — <scenario>

- Audibly different from pre-layer behaviour? yes / no
- Shaker more / same / less prominent vs Rhino-via-chair?
- Subjective immersion: better / same / worse / off
- Notes (if worse, hypothesis: gain too low? freq_factor wrong direction?
  routing wrong band?):
```

## Required outcomes

- At least **one effect** is documented as **clearly improved** by the
  layered routing — gives the user evidence the effort was worth it.
- At least **one effect** is documented as **same or worse** — gives the
  user a starting point for tuning via the UI.
- Total effects covered: at least 5 from the matrix above.

## `KNOWN_ISSUES.md` baseline

Created in this step. If nothing notable came up:

```markdown
# Known issues — shaker layered routing

_Last updated: <date>_

## No known issues

Smoke test on <date> covered <N> effects in MSFS; layered routing
behaved as designed. Subjective tuning of the bundled defaults can be
done via System Settings → Shaker → Effect layers.
```

If issues did come up, list them in three buckets:

```markdown
# Known issues — shaker layered routing

_Last updated: <date>_

## Defaults that feel wrong (user can retune via UI)

- `<effect>` — <observation> — suggested next try: <freq_factor /
  gain / route adjustment>

## Latency / timing

- <observation>

## Audible interference between layers

- <observation>
```

## Acceptance

- `MSFS_LAYER_TEST.md` exists, ≥ 5 effects covered using the per-effect
  template, with at least one "improved" and one "same/worse".
- `KNOWN_ISSUES.md` exists (baseline content if nothing came up).
- `PLAN_LAYERS.md` has STEP_05 ticked.
- Notes section of `PLAN_LAYERS.md` mentions any cross-cutting findings
  (e.g. "shaker amp clipping when buffeting + ab_rumble overlap" — that
  kind of thing belongs there if found).

## Stop here

This is the final step of the layered-routing iteration. After STEP_05
sign-off, the iteration is closed. Future tuning lives in the UI; future
features (per-aircraft packs, stick-side layer awareness, etc.) get a
new iteration brief.
