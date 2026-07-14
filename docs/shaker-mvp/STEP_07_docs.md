# STEP_07 — Docs & known limitations

## Tasks

### 1. `README.md` addendum

Add a short paragraph in the Features section:

> **Bass shaker support:** TelemFFB can drive a tactile transducer via your soundcard. See `docs/shaker-mvp/ARCHITECTURE.md`.

### 2. Create `docs/shaker-mvp/KNOWN_LIMITATIONS.md`

List explicitly what's MVP-out-of-scope:

- Only MSFS is tested. DCS / IL-2 / BMS / X-Plane may or may not work — they will not receive tuning attention in MVP.
- The `direction` parameter is stored but unused — no spatial crossfade between stick and shaker yet.
- No per-aircraft tuning UI for shaker (uses global gain only).
- Effect whitelist is hardcoded — extend by editing `SHAKER_EFFECT_WHITELIST` in `telemffb/hw/ffb_shaker.py`.
- Multiple shakers (e.g. seat + rudder pedals) not supported — single mono output only.
- Constant-force effects map to a fixed 25 Hz; not configurable per effect.
- No EQ / shaping filters — raw sine output.

## Acceptance

- All docs present and internally consistent.
- The link from `README.md` resolves to `docs/shaker-mvp/ARCHITECTURE.md` and that file exists.
- `docs/shaker-mvp/KNOWN_LIMITATIONS.md` exists with the bullet list above.
