# STEP_04 — Logging instead of print in _selftest_layered

## Goal

Replace the six `print()` calls in `_selftest_layered()` (`telemffb/hw/ffb_shaker.py:561-588`)
with `logger.info()`.

## Procedure

1. The function `_selftest_layered()` lives at `ffb_shaker.py:561-588`. The six
   `print()` calls are at lines:
   - 563 — `print(f"ffb_shaker layered selftest: device={device!r} samplerate={samplerate}")`
   - 571 — `print("Layered start issued — expect 20 Hz (layer0) and 80 Hz (layer2) oscillators in synth")`
   - 574 — `print(f"  oscillators in synth: {names}")`
   - 578 — `print("  assertions passed")`
   - 581 — `print("Layered stop issued")`
   - 586 — `print("  stop assertions passed")`

2. Replace each `print(f"...")` with `logger.info("...")`. **Note: existing variable
   name is `logger` (line 43), not `log` as the brief mentions.** Convert
   f-strings to `%`-formatting per logging best practice, e.g.:

   ```python
   logger.info("ffb_shaker layered selftest: device=%r samplerate=%s", device, samplerate)
   logger.info("  oscillators in synth: %s", names)
   ```

3. **CLI logging configuration:** `main()` in `ffb_shaker.py:610-613` already calls
   `logging.basicConfig(level=logging.INFO, ...)` for the `--selftest-layered` path.
   Verify it remains effective; tighten only if the output isn't appearing.

4. **Out of scope:** the `shaker_synth.py` selftest CLI (`--selftest`,
   `--selftest-transient`) keeps its `print()` style — not flagged as a carry-over.

## Verification

```bash
python -m telemffb.hw.ffb_shaker --selftest-layered
```

Output should be visually identical (six lines describing oscillators / assertions),
just routed via logging instead of print.

```bash
git grep -n "^[[:space:]]*print(" -- 'telemffb/hw/ffb_shaker.py'
```

Should be empty (no `print()` calls in non-CLI paths in `ffb_shaker.py`).

## Acceptance

- `python -m telemffb.hw.ffb_shaker --selftest-layered` produces visually-identical
  CLI output to before.
- No `print()` calls remain in `ffb_shaker.py`.

Stop and request review.
