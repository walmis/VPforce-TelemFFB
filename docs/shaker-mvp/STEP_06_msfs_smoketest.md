# STEP_06 — MSFS smoke test

**No code changes.** Verification only.

## Procedure

1. Configure: master = joystick (Rhino), shaker child launches alongside. Both processes connected to the same MSFS session.
2. Start MSFS, fly any GA aircraft (Cessna 172 or similar) — keep it simple.
3. Verify each event triggers shake and capture observations.

## Events to verify

| Effect | Trigger condition |
|---|---|
| Engine running on ground (idle rumble) | Likely via `prop_rpm*` |
| Takeoff roll | `runway0`, `runway1` |
| Flaps deploy | `flapsmovement` |
| Gear retract / extend | `gearmovement` |
| Touchdown | `touchdown` |
| Stall buffet | `buffeting` |

## Documentation

Document findings in `docs/shaker-mvp/MSFS_TEST_RESULTS.md` with one row per effect:

| Effect name | Triggered (Y/N) | Observed amplitude | Notes |
|---|---|---|---|

The "Notes" column should call out anything unexpected: telemetry not arriving, whitelist missing the name, frequency too low to feel on the user's hardware, etc.

## Acceptance

- At least 4 of the 6 effects above produce noticeable shake.
- Anything failing has a row in `MSFS_TEST_RESULTS.md` with a hypothesis (telemetry not being emitted? whitelist missing the name? frequency too low to feel on the user's shaker?).
