# STEP_03 — Public ShakerSynth.add_oscillator API

## Goal

The test-effect worker in the layer editor reaches into `synth._oscillators` and
`synth._lock` directly to inject a temporary oscillator (`SystemSettingsDialog.py:741-765`).
Replace this with a public API.

## Procedure

1. **Add to `ShakerSynth` (`telemffb/hw/shaker_synth.py:210-381`):**

   ```python
   def add_oscillator(self, name: str, oscillator) -> None:
       """Insert a pre-built oscillator under the given name. Replaces any
       existing oscillator with the same name. Thread-safe."""
       with self._lock:
           self._oscillators[name] = oscillator
   ```

   `add_oscillator` accepts any object satisfying the synth's expected oscillator
   interface (`render(num_samples) -> np.ndarray`, `is_silent` property, `stop()`
   method). It does NOT instantiate — the caller already has the object.

   `remove_oscillator` already exists at line 301 — keep as is.

2. **Refactor `_on_shaker_layer_test._run` (`SystemSettingsDialog.py:741-765`)** to
   call:

   ```python
   synth.add_oscillator(osc_name, osc)
   ```

   instead of:

   ```python
   with synth._lock:
       synth._oscillators[osc_name] = osc
   ```

   Cleanup retrieval (`synth._oscillators.get(...)`) becomes `synth.remove_oscillator(name)`
   followed by an explicit `osc.stop(...)` — or simply use the existing
   `osc.stop(ramp_ms=100)` and let `_callback`'s silence-skip + GC handle cleanup.

3. **`Oscillator` (and the future `BandpassNoiseGenerator`) stay in `shaker_synth.py`**
   and remain importable for the layer-editor test worker.

## Verification

```bash
git grep -n "synth\._oscillators\|synth\._lock" -- 'telemffb'
```

Should show **only** internal references inside `shaker_synth.py` after the change
(i.e. the class's own implementation).

## Acceptance

- No external code in the project accesses `ShakerSynth._oscillators` or
  `ShakerSynth._lock` directly.
- The Test-Effect button in the layer editor still works identically — plays the
  unsaved layer stack on a temporary synth for ~2 s.
- `add_oscillator` is documented in the class docstring or via inline comment.

Stop and request review.
