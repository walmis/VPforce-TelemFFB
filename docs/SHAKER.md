# TelemFFB Shaker — Funktionsumfang, Architektur, Limitationen

_Stand: 2026-05-03 — Branch `claude/improve-shaker-effects-eaOIE`, nach der Phase-Locked-Impulse-Iteration (`53f0c5b`), die echte physikalisch-synchrone Rotor-/Prop-/Zylinder-Impulse ergänzt._

Diese Datei ist der zentrale Einstieg, wenn jemand verstehen will, **was die
Shaker-Integration kann, wie sie funktioniert und was sie (noch) nicht kann**.
Sie fasst den Stand nach fünf Iterationen zusammen — der ursprünglichen
MVP-Bringt-Effekte-auf-den-Shaker-Phase
(`docs/shaker-mvp/`), der Polish-Phase mit Envelope, räumlicher Positionierung
und frequenzband-spezifischem Layered Routing
(`docs/shaker-polish-layers/`), der Cleanup- + Bandpass-Noise-Iteration
(`docs/shaker-cleanups-noise/`), die die Carry-overs der Polish-Phase auflöst
und einen `bandpass_noise`-Synthese-Primitiv ergänzt, der Schema-v3-
Envelope-Override-Iteration (`docs/shaker-envelope-override/`) mit per-Layer-
`attack_ms`/`decay_ms`-Feldern für Impulse-Layer, der Calibration-Iteration
(Commit `a48f1b4`) mit pro-Shaker-Profilen, Single-Pulse-Tuner und Active
Braking, und der aktuellen Phase-Locked-Impulse-Iteration (Commit `53f0c5b`),
die kontinuierliche Sinus-Rumbles für Rotor / Propeller / Zylinder durch
physikalisch synchrone Impulse ersetzt und ein VS-getriebenes
Touchdown-Impulse-Voice ergänzt.

---

## 1. Zweck / Was ist „der Shaker" hier?

Ein Bass-Shaker ist ein körperlich gekoppelter taktiler Transducer (oft am
Sitz, am Sitzrahmen oder unter dem Pedalbrett montiert). Er macht keine
gerichteten Kräfte wie ein Force-Feedback-Stick, sondern wandelt ein
niederfrequentes Audiosignal in spürbare Schwingungen um.

Im TelemFFB-Modell ist „Shaker" ein eigener Device-Typ neben Joystick,
Pedalen, Collective und Trim Wheel. Er wird:

- als eigene **Child-Instanz** vom Master gestartet (`--type shaker --child`),
- bekommt **Telemetrie über IPC/ZeroMQ** vom Master,
- läuft den gleichen `aircraft_base`-Code wie alle anderen Children,
- hat aber **kein Rhino-HID-Backend**, sondern einen
  Audio-Synthese-Backend (`ShakerSynth` über `sounddevice`/PortAudio).

Die Effekte, die `aircraft_base.py` produziert (`runway0`, `gunfire`,
`gearclunk`, `je_rumble_1_1`, …), werden vom Shaker-Backend in Sinus-
und Impuls-Schwingungen übersetzt und auf eine vom User wählbare Audio-
Ausgabe (z. B. dedizierte Soundkarte → Endstufe → Shaker) ausgegeben.

---

## 2. Hardware-Annahmen

Die Default-Konfiguration und die kuratierten Layer-Splits gehen vom
folgenden Setup aus:

- **VPforce Rhino** als Stick, am Sitzrahmen montiert. Das Rhino hat selbst
  schon ein bewegliches Massensystem, das tieffrequente Energie über den
  Stuhlrahmen in den Pilot überträgt.
- **Bass-Shaker** zusätzlich am Sitz. Komplementär gedacht — soll nicht das
  doppeln, was das Rhino-Gewicht ohnehin liefert, sondern andere
  Frequenzbänder bedienen.
- Audio-Ausgabe für den Shaker auf einer **separaten Soundkarte** oder einem
  dedizierten Output-Device, damit Master-Lautstärke und sonstige PC-Sounds
  nicht reingrätschen.

Ein Mono-Shaker funktioniert. Stereo-Routing (Pan / L/R-only) ist
optional und nur sinnvoll, wenn zwei räumlich getrennte Shaker
existieren oder eine räumliche Trennung Stick/Shaker per Audio-Layout
gewünscht ist.

---

## 3. Architektur

### 3.1 Prozess-Modell

```
                  +----------------------------+
                  |  Simulator (MSFS / DCS /   |
                  |  XPlane / IL-2 / BMS)      |
                  +--------------+-------------+
                                 | telemetry frames
                                 v
              +-----------------------------------+
              |  TelemFFB master process          |
              |  (--type joystick, no --child)    |
              |  reads telemetry, publishes       |
              |  ZeroMQ frames to all children    |
              +------+----------------------+-----+
                     |                      |
       ZeroMQ telem  |                      |  ZeroMQ telem
                     v                      v
        +--------------------+   +--------------------------+
        | Joystick child /   |   | Shaker child             |
        | self (Rhino HID)   |   | --type shaker --child    |
        |                    |   |                          |
        | aircraft_base ->   |   | aircraft_base ->         |
        |   effects[name]    |   |   effects[name]          |
        |   .periodic().start|   |   .periodic().start      |
        |        |           |   |        |                 |
        |        v           |   |        v                 |
        | HapticEffect       |   | HapticEffect (shaker     |
        | (ffb_rhino)        |   |   facade)                |
        |        |           |   |        |                 |
        |        v           |   |        v                 |
        |  USB HID -> Rhino  |   |   ShakerSynth            |
        +--------------------+   |   (numpy + sounddevice)  |
                                 |        |                 |
                                 |        v                 |
                                 |  PortAudio output stream |
                                 |  -> selected soundcard   |
                                 |  -> bass shaker          |
                                 +--------------------------+
```

- Jeder Device-Typ läuft als **eigener OS-Prozess**. Das ist nicht neu für den
  Shaker — Pedale, Collective, Trim Wheel laufen heute schon so.
- Stick und Shaker sind **prozess-isoliert**: ein blockierter PortAudio-
  Callback oder ein gecrashtes Audio-Backend kann den Rhino-FFB-Loop nicht
  stallen. Genauso umgekehrt.
- Pro Device-Typ existiert ein **eigener Settings-Slot** (per `pidShaker`
  als „synthetic ID"). Der User konfiguriert Master Gain,
  Output-Device etc. unabhängig vom Stick.

### 3.2 Wo schaltet das System um?

Der Backend-Tausch passiert in genau einer Stelle:
`aircraft_base.use_shaker_backend()` (gerufen aus `main.py`, Shaker-Branch
des `_initialize_device_connection()`). Das schwenkt den `HapticEffect`-
Symbol-Import auf `telemffb.hw.ffb_shaker` statt `telemffb.hw.ffb_rhino`.
Der Rest von `aircraft_base.py` (≈54 Effekt-Namen, alle `.periodic()`/
`.constant()`/`.start()`-Calls) ändert sich **nicht**.

### 3.3 Audio-Synthese

`telemffb/hw/shaker_synth.py` (≈630 Zeilen, dependency-frei: nur `numpy` +
`sounddevice`, kein `telemffb.*`-Import) liefert:

- **`Oscillator`** — phasenkontinuierliche Sinus-Quelle pro benannter
  Effekt-Instanz. Drei Modi:
  - `set(freq, amp, ramp_ms)` für Continuous-Effekte mit linearer
    Amplituden-Rampe (Default 50 ms).
  - `trigger(freq, amp, attack_ms, decay_ms)` für klassische One-Shot-
    Transients. Linear Attack, exponentielles Decay
    (`k = ln(256)/decay_samples`) (Defaults: `attack_ms=4.0`,
    `decay_ms=90.0`; via Layer-Schema-v3 pro Layer überschreibbar),
    Envelope endet sich selbst, Re-Trigger startet bei Sample 0 neu.
  - `trigger_pulse(carrier_hz, halfwaves, amplitude, attack_ms, release_ms,
    brake_amp, brake_delay_ms)` für Single-Pulse mit gated Halbwellen-Drive
    und optional aktivem Brake (siehe §3.5). Reset auf Phase 0 garantiert
    halbwellen-genaue Dauer; Brake ist exakt eine phasen-invertierte
    Halbwelle bei `carrier_hz`, sample-genau geschrieben (nicht moduliert)
    via `build_pulse_envelope()`. Ersetzt jeden laufenden Envelope.
- **`PhaseAccumulator`** — integriert Winkelgeschwindigkeit über Δt und
  meldet die Anzahl ganzzahliger Crossings einer Per-Revolution-
  Divisions-Grenze (Blade-Pass, Zylinder-Zündung). Stateless bzgl. Welt-
  Zeit: nutzt das Caller-supplied `dt`. Wird in `ImpulseTrainOscillator`
  pro Audio-Block gefüttert (`dt = blocksize / samplerate`); kann auch
  vom Telemetry-Tick getrieben werden.
- **`ImpulseTrainOscillator`** — phasen-gelockter Impulse-Train-
  Generator (siehe §3.6). Wickelt einen inneren `Oscillator` ein,
  ruft `trigger_pulse()` an jedem Crossing auf. Honoriert
  `(is_silent, render(n))`-Vertrag, läuft daher transparent im
  ShakerSynth-Audio-Callback wie jeder andere Generator.
- **`BandpassNoiseGenerator`** — band-limitiertes Rauschen, gleiche
  Render/Stop/`is_silent`-Schnittstelle wie `Oscillator`. White-Noise-
  Quelle (per-Instance `np.random.default_rng()`) durch ein RBJ
  Constant-Skirt-Gain-Bandpass-Biquad geleitet.
  - `set(center_hz, bandwidth_hz, amplitude, ramp_ms=50)` —
    Q wird aus `center / bandwidth` mit 0.5-Floor abgeleitet,
    Center und Bandwidth werden auf `>= 1.0 Hz` geklemmt.
  - Filter-Koeffizienten werden lazy bei Center/Bandwidth-Änderung
    neu berechnet; Filter-Delay-Line (`z1`, `z2`) bleibt über
    `render()`-Calls hinweg erhalten.
  - Output `clip(-1, +1)` als Sicherheit gegen Q-bedingte Peaks.
  - Inner-Biquad-Loop ist eine Python-`for`-Schleife (für die MVP
    akzeptabel bei 256-Sample-Blöcken @ 187 Hz Callback-Rate);
    Vektorisierung via `scipy.signal.lfilter` ist als Erweiterung
    bewusst zurückgestellt.
- **`ShakerSynth`** — Mixer + sounddevice-OutputStream-Wrapper:
  - hält ein `dict[str, Oscillator | BandpassNoiseGenerator]`, im
    Audio-Callback iteriert es alle nicht-stillen Generatoren und
    summiert.
  - Master Gain wird als finaler Multiplier angewandt, danach `clip(-1, +1)`.
  - **Channel-Routing** im Output: `mono` / `left only` / `right only` /
    `pan` (equal-power: `cos((pan+1)·π/4)` und `sin(...)`).
  - Block-Size 256 Samples @ 48 kHz Default → ~5 ms Latenz im Synth.
  - **Public API** für externe Caller (HapticEffect, Layer-Editor-
    Test-Worker): `get_oscillator(name)`, `get_noise_oscillator(name)`
    (auto-vivify), `add_oscillator(name, osc)` (Replace),
    `peek_oscillator(name)` (Read-only, kein Auto-Vivify),
    `list_oscillator_names()` (Snapshot), `remove_oscillator(name)`.
    Alle thread-safe — kein Caller fasst `_oscillators` oder `_lock`
    direkt an.
- **CLI-Selftest**: `python -m telemffb.hw.shaker_synth --selftest`,
  `--selftest-transient`, `--selftest-noise [--center HZ --bandwidth HZ]`,
  `--list-devices`. Dependency-frei und ohne TelemFFB-Master nutzbar.

### 3.4 Shaker-Profil (Kalibrierung & Active Braking)

Pro Shaker-Hardware kann ein **`ShakerProfile`** (Dataclass in
`telemffb/hw/shaker_profile.py`) hinterlegt werden, das die Drive-
Parameter für alle Single-Pulse-Effekte vorgibt. Felder:

| Feld | Bedeutung |
|------|-----------|
| `name` | UI-Anzeigename (z. B. `"Buttkicker LFE"`) |
| `f_res_hz` | Mechanische Resonanzfrequenz (Advisory; von der Tuner-UI als Default benutzt) |
| `carrier_offset_pct` | Carrier liegt typisch leicht **oberhalb** Resonanz; Default 15 % |
| `halfwaves` | Anzahl Halbwellen im Drive — bestimmt die Pulse-Dauer |
| `attack_ms` / `release_ms` | Linear-Attack/Release auf den Drive-Envelope |
| `gain` | Globaler Multiplikator für Pulse-Amplituden |
| `brake_enabled` | Active Braking an/aus |
| `brake_amp_pct` | Brake-Amplitude in Prozent der Drive-Amplitude |
| `brake_delay_ms` | Delay zwischen Drive-Ende und Brake-Beginn |

**Active Braking** ist genau eine phasen-invertierte Halbwelle bei
`carrier_hz`, die zeitlich passend gelegt wird, um die Eigenschwingung der
Shaker-Mass nach dem Drive aktiv abzubremsen. Auf schweren Shakern
(Buttkicker LFE) macht das den Unterschied zwischen einem klar
abgegrenzten Thomp und einem nachklingelnden „Boing".

**Bundled Default-Profile** (`telemffb/data/shaker_profiles_default.json`):

- **Generic** — konservativer Starter, no-brake. Pflicht-Default,
  funktioniert auf jedem Shaker, klingt auf keinem optimal.
- **Dayton DAEX-25** — leichte Mass, ~45 Hz Resonanz, milder Brake.
- **Buttkicker LFE** — schwere Mass, ~25 Hz Resonanz, 1 Halbwelle Drive
  + starker Brake (50 %, 0.6 ms Delay).

**Persistenz**: User-Profile leben in
`<userconfig_rootpath>/shaker_profiles.json`, Format identisch zu
`shaker_effects.json` (`{"version": 1, "active": "<name>", "profiles": [...]}`).
JSON-I/O in `telemffb/hw/shaker_profiles_io.py` (`load`, `save`,
`get_active`, atomic-write via `.tmp`-Rename).

**Aktivierung**: `ffb_shaker.set_active_profile(profile)` setzt das
modul-globale `ACTIVE_PROFILE`. `_pulse_kwargs(layer=None|Layer)` baut
daraus die kwargs für `Oscillator.trigger_pulse()` zusammen, mergt
optional Layer-Overrides drüber. Genutzt von:

- der Layer-Routing-Pipeline für Impulse-Layer in `_start_layered()`,
- der `SHAKER_EFFECT_PROFILES`-Routing-Stufe für Transient-Effekte,
- `HapticEffect.fire_impulse()` (Phase-Locked-Iteration, §3.6).

> **Live-Push-Limit**: Profile werden beim Start der Shaker-Child
> geladen. Änderung über die Calibration-UI erfordert einen Restart der
> Shaker-Child, um zu greifen — IPC-Live-Push ist heute nicht
> implementiert.

### 3.5 Calibration-UI (System Settings → Shaker)

Eine Calibration-Sektion im Shaker-Tab (eigene Datei
`telemffb/SystemSettingsDialog.py` ~640 Zeilen Erweiterung) erlaubt dem
User, ein `ShakerProfile` interaktiv zu tunen, ohne JSON manuell zu
editieren:

- **Resonance Sweep**: Sweepit von z. B. 20 bis 80 Hz, User markiert die
  spürbar lauteste Frequenz mit einem Button. Der Carrier wird auf
  `f_res * (1 + carrier_offset_pct/100)` gesetzt.
- **Single-Pulse Tuner**: Spielt ein einzelnes `trigger_pulse()`, User
  hört den Pulse plus eventuelles Nachklingeln. Per A/B-Replay-Knopf
  vergleicht er mit/ohne Brake oder mit verschiedenen
  `brake_amp_pct`/`brake_delay_ms`-Werten.
- **Live-Waveform-Preview**: Eigenes Widget
  `telemffb/ui/ShakerWaveformWidget.py` zeichnet `build_pulse_envelope()`
  byte-identisch zur Audio-Pipeline. User sieht, was er hört.
- **Save/Save-As/Delete/Reset**: Profile-Verwaltung. Default-Profile
  sind nicht löschbar.

Das aktive Profil wird zusätzlich als QSettings-Key `shakerProfile`
persistiert (`globl_sys_dict`), damit der ShakerChild beim nächsten
Start direkt das gewünschte Profil zieht.

### 3.6 Phase-Locked Impulse Synthesis (Physics Mode)

Klassische Engine-/Rotor-Effekte in `aircraft_base.py` rufen
`effects[name].periodic(freq, mag, dir).start()` auf — der Shaker rendert
einen kontinuierlichen Sinus, dessen Frequenz mit RPM mitläuft. Das
*klingt* nach Rotor, fühlt sich aber synthetisch an, weil die Welle
glatt ist und die Phase nicht wirklich auf das physische Blatt
gelocked ist.

Die Phase-Locked-Iteration ersetzt das durch echte
**Per-Blade-/Per-Cylinder-Impulse**:

```
RPM (Telemetrie, ~30–60 Hz)
       │
       v
  PhaseAccumulator   ω = rpm/60 × divisions_per_rev
       │             phase += ω · dt    (dt = blocksize / samplerate)
       │             crossings = floor(new) − floor(old)
       v
  ImpulseTrainOscillator.render(n)
       │
       │  pro Crossing:
       v
  Oscillator.trigger_pulse(carrier_hz, halfwaves,
                            amplitude = load · gain,
                            attack_ms, release_ms,
                            brake_amp, brake_delay_ms)
       │
       v
   Sample-genauer Audio-Block
```

- **Crossings werden lokal integriert.** Der Phase-Akkumulator wird im
  Audio-Block-Tick (~190 Hz, blocksize 256 @ 48 kHz) inkrementiert; die
  Telemetry-Rate liefert nur die *aktuelle* RPM, nicht die Crossing-
  Timing. Das macht den Effekt unabhängig vom 30–60 Hz-Telemetry-Jitter.
- **Pulse-Onset ist block-quantisiert.** Onset-Jitter ist <= 5.3 ms
  (one block); im Vergleich zur Periode bei z. B. 25 Hz Blade-Pass
  (40 ms) kein hörbares Problem.
- **Auto-Fallback auf Bandpass-Noise oberhalb `max_impulse_rate_hz`.**
  Bei Raten oberhalb der Per-Pulse-Drive-Dauer würden Pulses überlappen
  und zur reinen Tone werden. Per-Voice konfigurierbar (typisch
  80–220 Hz); oberhalb schaltet `ImpulseTrainOscillator` automatisch
  auf einen internen `BandpassNoiseGenerator` mit
  `center_hz = impulse_rate` um. Hysterese: Eintritt in den Noise-Mode
  bei 100% des Caps, Ausstieg erst bei 85% — Flapping bei steady-state
  near-redline ist ausgeschlossen. Bandbreite default 20% des Centers
  (Floor 15 Hz), via `noise_bandwidth_hz` in `SHAKER_PHYSICS_PROFILES`
  überschreibbar. ~80 ms Crossfade auf beiden Voices vermeidet hörbare
  Klicks am Übergang. Tests in
  `tests/test_phase_locked_impulses.py`.
- **Pulse-Shape** kommt aus `SHAKER_PHYSICS_PROFILES[name]` — pro Voice
  getuned (Carrier 28–80 Hz, 1–2 Halbwellen, Brake 0.3–0.6 von Drive).
  Layer-Override via `Layer.attack_ms` / `decay_ms` ist hier *nicht*
  aktiv — die Profile sind self-contained, weil die Voices physikalisch
  motiviert sind und nicht über die Layer-Pipeline gehen.

**Voice-Map**:

| Voice | Divisions | Carrier | Notes |
|-------|-----------|---------|-------|
| `rotor_phys_main` | `rotor_blade_count` | 38 Hz / 1 hw / brake 0.6 | Hubschrauber-Hauptrotor — tiefer, langer Thomp |
| `rotor_phys_tail` | `tailrotor_blade_count` | 70 Hz / 1 hw / brake 0.4 | Tail-Rotor — schneller, schärfer |
| `prop_phys_1..4` | `prop_blade_count` | 60 Hz / 1 hw / brake 0.5 | Propeller-Blade-Pass, pro Engine |
| `cyl_phys_1..4` | `cylinder_count / 2` (4-Takt) | 80 Hz / 1 hw / brake 0.3 | Pro-Zylinder-Zündung; optional |
| `touchdown_vs` | n/a — `fire_impulse(amp)` | 28 Hz / 2 hw / no brake | One-shot, Magnitude = f(VS) |

**Spool-Up emergiert physikalisch.** Bei Engine-Start ramped die
`RotorRPM`-Telemetrie von 0 auf z. B. 250; die Impulse-Rate folgt
(0 → 25 Hz für 5 Blätter), und die Amplitude wird zusätzlich mit
`min(1, RPM/nominal)` (oder Collective, falls vom Sim geliefert)
multipliziert — Spool-Up klingt damit von „langsamer schwerer Chuff"
bis „voller Rotor-Thrum" ohne expliziten State-Machine-Code.

**Touchdown-VS-Voice.** Anders als die Rotor-/Prop-Voices fired
`touchdown_vs` einmalig auf der WoW-Rising-Edge mit Magnitude
`scale_clamp(|VS|, (gentle, hard), (min_amp, max_amp))`. Per
`HapticEffect.fire_impulse(magnitude)` direkt durch die Pulse-Pipeline
(eigene `Oscillator`-Instanz, kein Phase-Accumulator).

**Mode-Switch.** Pro Aircraft-Class via `engine_rumble_mode`
(`"physics"` | `"synthesis"`). Default `"physics"` für den Shaker.
Synthesis ist der Legacy-Pfad mit kontinuierlichen Sinus-Effekten
(`rotor_rpm0-1`, `prop_rpm0-1` etc.) — bleibt wegen Rhino-Stick-
Backwards-Compat erhalten. Auf dem Rhino degradiert `physics()` ohnehin
auf eine `periodic()` mit Carrier-Frequenz `rpm/60 × divisions`.

### 3.7 HapticEffect-Facade

`telemffb/hw/ffb_shaker.py` (≈620 Zeilen) stellt die zum Rhino-Backend
formgleiche `HapticEffect`-Klasse bereit. Methoden:

- `periodic(freq, mag, dir, …, effect_type=, duration=)` — speichert die
  Parameter; Effekt-Type-Konstanten (`EFFECT_SINE`, `_SQUARE`, `_TRIANGLE`,
  …) werden vom Rhino verbatim übernommen, auf dem Shaker als reine
  Sinusquelle gerendert (Effekt-Typ wird also „bewusst ignoriert", die
  Charakteristik kommt aus Profil/Layer).
- `constant(mag, dir)` — mappt auf einen 25 Hz-Carrier.
- `physics(rpm, divisions, load, **shape)` — konfiguriert eine
  Phase-Locked-Impulse-Voice (siehe §3.6). Setzt
  `effect_type = EFFECT_PHYSICS_IMPULSE` (lokales Sentinel, kein FFB-
  Protokoll-Wert) und stash-t die Telemetrie-Inputs; Pulse-Shape kommt
  aus `SHAKER_PHYSICS_PROFILES[name]`, optional via `shape_overrides`
  überschrieben. Auf Rhino: degradiert auf `periodic()` bei
  `rpm/60 × divisions`.
- `fire_impulse(magnitude, **shape)` — feuert einen einzelnen
  `trigger_pulse()` ohne Phase-Akkumulator. Genutzt für Touchdown-VS
  und ähnliche Event-Trigger. Auf Rhino: feuert eine `EFFECT_SQUARE`-
  Periodic mit `duration=80 ms`.
- `spring()`, `damper()`, `friction()`, `inertia()`, `spring_adjuster()`,
  `setCondition()`, `_conditional_effect()` — **chainable No-Ops** mit
  Debug-Log. Sie müssen existieren (sonst crasht `aircraft_base.py`),
  produzieren aber keinen Sound.
- `start()` — entscheidet, **wie** der Effekt gespielt wird (siehe
  Routing-Kette unten).
- `stop()`, `destroy()`, `started` — alle layer-aware und physics-aware
  (`stop()` ruft `osc.stop()` auf dem `ImpulseTrainOscillator`, der den
  inneren Sinus rampt und den Phase-Akkumulator stilllegt).

---

## 4. Routing-Kette in `start()`

Fünf Stufen, in dieser Reihenfolge:

```
┌─────────────────────────────────────────────────────────┐
│ 1. Whitelist-Filter                                     │
│    self.name in SHAKER_EFFECT_WHITELIST?                │
│    nein → drop (debug-Log)                              │
└─────────────────────────────────────────────────────────┘
                        │ ja
                        v
┌─────────────────────────────────────────────────────────┐
│ 2. Physics-Branch                                       │
│    self.effect_type == EFFECT_PHYSICS_IMPULSE?          │
│    ja  → _start_physics() → ImpulseTrainOscillator      │
│         (siehe §3.6; Pulse-Shape aus                    │
│          SHAKER_PHYSICS_PROFILES[name])                 │
└─────────────────────────────────────────────────────────┘
                        │ nein
                        v
┌─────────────────────────────────────────────────────────┐
│ 3. EFFECT_LAYERS-Lookup                                 │
│    self.name in EFFECT_LAYERS?                          │
│    ja  → _start_layered() → Sub-Oszillatoren            │
│         (einer pro Layer, route ∈ {shaker, both})       │
└─────────────────────────────────────────────────────────┘
                        │ nein
                        v
┌─────────────────────────────────────────────────────────┐
│ 4. SHAKER_EFFECT_PROFILES-Lookup                        │
│    Profil definiert kind (transient | continuous),      │
│    freq-Override, gain-Multiplikator, attack/decay/ramp │
│    Transient-Pfad geht durch trigger_pulse() mit dem    │
│    aktiven ShakerProfile (siehe §3.4).                  │
└─────────────────────────────────────────────────────────┘
                        │ kein Profil
                        v
┌─────────────────────────────────────────────────────────┐
│ 5. Heuristik / Default                                  │
│    EFFECT_SQUARE und 0 < duration ≤ 80 ms?              │
│    ja  → Transient (3 ms attack, max(40, dur·2) ms decay)│
│    nein→ Continuous mit 50 ms ramp, gain 1.0            │
└─────────────────────────────────────────────────────────┘
```

### 4.1 Whitelist (Stufe 1)

Hardcoded `SHAKER_EFFECT_WHITELIST` in `ffb_shaker.py`. Aktuell ~59
Einträge in Kategorien:

- **Wheel / runway**: `runway0`, `runway1`, `runway_bump0`, `runway_bump1`,
  `touchdown`, `gearclunk`
- **Weapons / countermeasures**: `gunfire`, `cm`, `payload_rel`
- **Buffeting**: `buffeting`, `buffeting2`, `vrs_buffet`, `gearbuffet`,
  `gearbuffet2`, `spoilerbuffet*`
- **Afterburner / jet**: `ab_rumble_*`, `je_rumble_*`
- **Prop / rotor (Synthesis-Mode, Legacy-Sinus)**: `prop_rpm*`, `rotor_rpm*`
- **Phase-Locked Voices (Physics-Mode, §3.6)**: `rotor_phys_main`,
  `rotor_phys_tail`, `prop_phys_1..4`, `cyl_phys_1..4`, `touchdown_vs`
- **ETL**: `etlX`, `etlY`
- **Surface movements**: `flapsmovement`, `gearmovement`, `gearmovement2`,
  `speedbrakemovement`, `spoilermovement`, `spoilermovement2`,
  `canopymovement`, `hookmovement`
- **Overspeed / AoA / wind**: `overspeedX/Y`, `aoa`, `crit_aoa`, `wnd`
- **Effect-Tester**: `__effect_tester__`

Effekte außerhalb der Whitelist werden mit Debug-Log gedroppt — neu
hinzukommende Effekte aus `aircraft_base.py` machen also nicht
versehentlich Geräusch.

### 4.2 EFFECT_LAYERS — Layered Routing (Polish-Phase)

Der zentrale Mechanismus aus der Polish-Iteration. Jeder Effekt kann als
**Liste von Layern** definiert werden:

```python
@dataclass(frozen=True)
class Layer:
    freq_factor: float = 1.0   # multipliziert die Call-Site-Frequenz
    gain: float = 1.0          # multipliziert die Call-Site-Magnitude
    route: str = "both"        # "shaker" | "stick" | "both"
    osc_type: str = "sine"     # "sine" | "impulse" | "bandpass_noise"
    # Nur relevant, wenn osc_type == "bandpass_noise"
    # (für sine/impulse werden sie ignoriert, dürfen aber im JSON stehen):
    center_hz:    Optional[float] = None  # None → freq_factor · call_freq
    bandwidth_hz: Optional[float] = None  # None → 20.0 Hz
    # Nur relevant, wenn osc_type == "impulse"
    # (für sine/bandpass_noise werden sie ignoriert, dürfen aber im JSON
    # stehen — round-trip-safe, siehe Working-Copy-Verhalten in §6.1):
    attack_ms: Optional[float] = None  # None → Oscillator.trigger-Default 4.0 ms
    decay_ms:  Optional[float] = None  # None → Oscillator.trigger-Default 90.0 ms
```

Ablauf für einen layer-getaggten Effekt (`_start_layered`):

1. Iteration über die Layer-Liste; Layer mit `route="stick"` werden
   übersprungen (Filter `_layer_is_for_shaker`).
2. Für jeden gerouteten Layer wird ein Sub-Oszillator namens
   `f"{effect_name}__layer{idx}"` (Doppel-Underscore) angelegt bzw.
   wiederverwendet — über die Public-API-Accessoren des Synths.
3. Dispatch nach `osc_type`:
   - `"sine"` → `synth.get_oscillator(name).set(freq · freq_factor, mag · gain)`.
   - `"impulse"` → `synth.get_oscillator(name).trigger(freq · freq_factor, mag · gain)`.
     Für `osc_type="impulse"` werden zusätzlich `layer.attack_ms` und
     `layer.decay_ms` (falls nicht `None`) als Keyword-Argumente an
     `Oscillator.trigger()` durchgereicht; bei `None` greifen die
     Built-in-Defaults (4.0 ms / 90.0 ms).
   - `"bandpass_noise"` → `synth.get_noise_oscillator(name).set(center, bw, mag · gain)`,
     wobei `center = layer.center_hz ?? freq · freq_factor` und
     `bw = layer.bandwidth_hz ?? 20.0`.
   - Unbekannter `osc_type` → `logger.warning(...)` + Layer überspringen
     (kein Sub-Oszillator angelegt, kein Eintrag im Duration-Timer).
4. Duration-Timer wird scheduled, wenn mindestens ein **kontinuierlicher**
   Layer (`sine` oder `bandpass_noise`) geroutet ist — Impulse-Layer
   beenden sich über ihren Envelope selbst.
5. `stop()` zerolt alle gerouteten Layer-Generatoren symmetrisch
   (`Oscillator.stop()` und `BandpassNoiseGenerator.stop()` sind
   schnittstellen-kompatibel).
6. `destroy()` entfernt sie aus dem Synth-Dict.

`EFFECT_LAYERS` wird zur Laufzeit aus `_BUILTIN_DEFAULT_LAYERS` (gebündelt
beim Modul-Import) plus der User-JSON `shaker_effects.json` (überlagert
Built-ins effekt-weise) zusammengeführt — Reload via `reload_layers()`
(triggert die UI nach „Save all effects").

### 4.3 SHAKER_EFFECT_PROFILES — Single-Oscillator Tuning

Vor der Layer-Iteration eingebaut, dient Effekten ohne Layer-Eintrag.
Jedes Profil definiert:

```python
{"kind": "transient" | "continuous",
 "freq": <Hz override>,        # optional, sonst Call-Site-Frequenz
 "gain": <multiplier>,
 "attack_ms" / "decay_ms" / "ramp_ms": <Envelope-/Rampen-Parameter>}
```

Aktuelle Einträge (6, nach dem Cleanup in STEP_01): `gearclunk` (55 Hz
Transient, 110 ms Decay), `runway_bump0`, `runway_bump1`, `payload_rel`,
`buffeting2` (Continuous mit 15 ms Snap-Rampe und Gain 1.1) und
`gearbuffet2`. Die früher hier eingetragenen Effekte `touchdown`,
`gunfire`, `cm`, `buffeting`, `vrs_buffet` und `gearbuffet` sind im
Default-Pack als Layer definiert und damit per Routing-Kette ohnehin
nicht mehr durch das Profil getunt — entsprechend wurden ihre PROFILES-
Einträge als Dead Code entfernt.

Wichtig: Profile bleiben für Effekte ohne Layer-Eintrag im Spiel. Wenn
ein User einen heute „nur Profil"-Effekt in seine `shaker_effects.json`
als Layer-Eintrag aufnimmt, gewinnt der Layer zur Laufzeit (Stufe 3 vor
Stufe 4 in der Routing-Kette).

### 4.4 Heuristik / Default (Stufe 5)

Sicherheitsnetz für alles, was weder Layer noch Profil hat aber in der
Whitelist ist:

- `EFFECT_SQUARE` + `duration ∈ (0, 80] ms` → Transient (kurzer Knack).
  Greift z. B. für den Effect-Tester-Dialog.
- Sonst Continuous mit 50 ms Linear-Ramp und Gain 1.0.

---

## 5. Spatial Positioning (Channel-Routing)

`ShakerSynth` hat vier Output-Modi:

| Mode | Verhalten |
|------|-----------|
| `mono` | Einkanal, oder dupliziert auf beide Stereo-Kanäle (Fallback) |
| `left` | Stereo erzwingen, Signal nur auf L, R = 0.0 |
| `right` | Stereo erzwingen, Signal nur auf R, L = 0.0 |
| `pan` | Stereo erzwingen, Equal-Power-Pan zwischen -1 (voll links) und +1 (voll rechts) |

Wenn das Output-Device kein Stereo unterstützt (mono-USB-DAC), fällt der
Synth mit Warning zurück auf `mono` — Channel-Mode/Pan werden in dem
Fall stillschweigend No-Ops.

Konfiguration über System Settings → Shaker-Tab:

- **Output channel**: ComboBox.
- **Pan**: horizontaler Slider mit Live-Label („Center", „L 0.30",
  „R 0.60"); aktiv nur wenn Output channel = `Stereo (pan)`.

Persistiert als `shakerChannelMode` (str) und `shakerPan` (float ∈ [-1, +1])
im `globl_sys_dict` (siehe `telemffb/utils.py`). Beim Restart der
Shaker-Child-Instanz werden beide an `ShakerSynth(...)` durchgereicht
(`main.py:_initialize_device_connection`).

---

## 6. Konfiguration

### 6.1 System Settings → Shaker-Tab

Programmatisch in `_setup_shaker_tab()` und
`_setup_shaker_layers_section()` aufgebaut (kein `.ui`-Editor nötig).
Inhalt:

```
┌─ Shaker tab ───────────────────────────────────────────────────────────────────────┐
│  Output device:    [(System default) | ... soundcard ▼]                            │
│  Master gain:      [QDoubleSpinBox 0.00 .. 2.00]                                   │
│  Output channel:   [Mono / Left / Right / Stereo(pan) ▼]                           │
│  Pan:              [-1 ──◯── +1]   (Center / L .. / R .)                           │
│  [Test]                                                                            │
│                                                                                    │
│  ── Effect layers ─────────────────────────────────────────────────────────────    │
│  Effect:  [ je_rumble_1_1 ▼ ]   ●  (modified marker)                               │
│                                                                                    │
│  ┌──────────────────────────────────────────────────────────────────────────────┐  │
│  │ # Freq× Gain Route     OscType          Remove Center  Bandwidth Attack  Decay│  │
│  │ 0 0.50  0.85 shaker ▼  sine ▼           [-]    —       —         —      —    │  │
│  │ 1 0.40  1.00 shaker ▼  impulse ▼        [-]    —       —         4.0    90.0 │  │
│  │ 2 1.00  0.60 shaker ▼  bandpass_noise ▼ [-]    40.0    20.0      —      —    │  │
│  └──────────────────────────────────────────────────────────────────────────────┘  │
│  [+ Add layer] [Reset effect to default] [Test effect]                             │
│                                                                                    │
│  [Save all effects] [Reload from disk]                                             │
│  [Reset all effects to defaults]                                                   │
└────────────────────────────────────────────────────────────────────────────────────┘
```

Verhalten:

- Effekt-Dropdown: alle 48 Whitelisted Effekte, alphabetisch.
- Tabelle pro Effekt: **10 Spalten** mit echten Widgets (DoubleSpinBox /
  ComboBox / Button), keine reinen Text-Zellen.
- **OscType-ComboBox** akzeptiert drei Werte: `sine`, `impulse`,
  `bandpass_noise`.
- **Center Hz** und **Bandwidth Hz** sind nur für `bandpass_noise`-Layer
  aktiv (range 5.0–200.0 step 0.5 / 1.0–100.0 step 1.0). Für sine- und
  impulse-Layer werden die Spin-Boxen visuell deaktiviert. Beim Wechsel
  von sine/impulse → bandpass_noise werden sinnvolle Defaults gesetzt
  (Center = `freq_factor · 40` auf 1 Dezimalstelle gerundet,
  Bandwidth = `20.0`); beim Wechsel zurück bleiben die Werte in der
  Working-Copy stehen, falls der User später wieder zu Noise wechselt.
- **Attack ms** und **Decay ms** sind nur für `impulse`-Layer aktiv (range
  0.1–50.0 step 0.5 / 5.0–500.0 step 5.0). Beim Wechsel sine/noise →
  impulse werden sinnvolle Defaults gesetzt (Attack = 4.0, Decay = 90.0
  — die Built-in-Werte von `Oscillator.trigger()`); beim Wechsel zurück
  bleiben die Werte in der Working-Copy stehen.
- Letzter Layer kann nicht entfernt werden (Remove-Button disabled, sonst
  würde der Effekt komplett stumm).
- `+ Add layer`: appendet einen `Layer()` mit Defaults (Sine, both, 1.0/1.0).
- **Working Copy** im Dialog: Edits persistieren beim Wechsel zwischen
  Effekten ohne Disk-Write. `●` markiert geänderte Effekte im Dropdown.
- **Save all effects**: schreibt JSON atomisch via `shaker_layers_io.save()`,
  triggert `ffb_shaker.reload_layers()` — laufende Shaker-Child nimmt es
  ohne Restart auf.
- **Test effect**: spielt den ungespeicherten Layer-Stack auf einem
  kurzlebigen `ShakerSynth` (eigener Worker-Thread, ~2 s, mit den
  aktuellen UI-Werten für Device/Gain/Channel/Pan), unabhängig vom
  globalen Synth. Die Sub-Oszillatoren werden über
  `synth.add_oscillator(...)` injiziert; `bandpass_noise`-Layer
  instanziieren `BandpassNoiseGenerator(synth.samplerate)`.
- **Reset effect to default** / **Reset all effects to defaults**: lesen
  aus `_BUILTIN_DEFAULT_LAYERS` (gebündelt bei Modul-Import).

### 6.2 `shaker_effects.json`

Pfad: `<G.userconfig_rootpath>/shaker_effects.json` — das ist im
Production-Build unter Windows `%LOCALAPPDATA%/VPForce-TelemFFB`, im
Dev-Modus das Repo-Root (siehe `main.py:_setup_config_paths`).

Format (aktuell **Schema-Version 3**):

```json
{
  "version": 3,
  "effects": {
    "<effect_name>": {
      "layers": [
        {"freq_factor": 0.5, "gain": 0.85, "route": "shaker", "osc_type": "sine",
         "center_hz": null, "bandwidth_hz": null, "attack_ms": null, "decay_ms": null},
        {"freq_factor": 0.4, "gain": 1.00, "route": "shaker", "osc_type": "impulse",
         "center_hz": null, "bandwidth_hz": null, "attack_ms": 5.0, "decay_ms": 220.0},
        ...
      ]
    },
    ...
  }
}
```

`center_hz` und `bandwidth_hz` sind nur für `osc_type="bandpass_noise"`
relevant; `attack_ms` und `decay_ms` sind nur für `osc_type="impulse"`
relevant. Alle vier Felder dürfen für beliebige Layer-Typen im JSON stehen
(round-trip-safe, damit Toggle-Operationen im Editor die UI-Werte über
Save/Reload hinweg behalten).

**v1 ↔ v2 ↔ v3-Migration:** Loader akzeptiert v1, v2 und v3 silently;
fehlende Felder kommen über die Dataclass-Defaults als `None` rein. Beim
nächsten Save schreibt der Saver die Datei als v3 zurück. Das gebündelte
Default-Pack bleibt absichtlich v1 auf Disk; Noise-Layer und
Envelope-Override-Layer werden vom User ggf. nach der laufenden MSFS-
Validierung der bestehenden Default-Layer hinzugefügt.

Robust gegen:

- Datei fehlt → leere Map zurück (Built-ins greifen).
- Malformed JSON → Exception geloggt, leere Map.
- Versions-Mismatch (≠ 1, ≠ 2, ≠ 3) → Warnung, Best-Effort-Load.
- Einzelner kaputter Layer-Eintrag → Effekt überspringen, Rest laden.

Atomisches Save schreibt nach `<path>.tmp` und benennt um — kein
halbgeschriebener File auch bei Crash mid-write.

### 6.3 Default Pack

`telemffb/data/shaker_effects_default.json` — read-only Resource, im
PyInstaller-Bundle als `datas`-Eintrag eingetragen. 17 kuratierte
Default-Layer-Sets:

| Effekt | Shaker-Layer | Stick-Layer |
|--------|--------------|-------------|
| `je_rumble_1_1` / `_1_2` | 0.5×, 0.85, sine | 1.0× und 2.0× sine |
| `ab_rumble_1_1` | 0.4×, 1.00, sine | 1.0× und 2.5× sine |
| `prop_rpm0-1` | 0.5×, 0.70, sine | 1.0× sine |
| `rotor_rpm0-1` | 0.4×, 0.90, sine | 1.0× sine |
| `runway0` | 0.6×, 0.80, sine | 1.5× sine |
| `touchdown` | 0.4×, 1.00, **impulse** | 2.0× impulse |
| `gunfire` | 0.4×, 0.90, **impulse** | 1.0× sine |
| `cm` | 0.5×, 0.70, **impulse** | 1.0× sine |
| `buffeting`, `vrs_buffet`, `gearbuffet` | 0.5×/0.4×, sine | 1.0× sine |
| `gearmovement` | 0.6×, 0.70, **impulse** | 1.5× impulse |
| `flapsmovement`, `speedbrakemovement` | 0.6–0.7×, sine | 1.0× sine |
| `etlX`, `etlY` | 0.5×, 0.70, sine | 1.0× sine |

Heuristik der Defaults: Sub-25 Hz auf den Shaker (Stuhl-gekoppelter
tief-frequenter Push), 40–90 Hz Mids gesplittet, >80 Hz Cracks auf den
Stick. Impulse-Effekte (Touchdown, Gear-Lock, Gunfire) bekommen den
Body-Thump auf den Shaker und den Haptik-Crack auf den Stick.

Beim ersten Start der Shaker-Child wird die Default-Datei nach
`<userconfig_rootpath>/shaker_effects.json` kopiert, sofern noch nicht
vorhanden. Reset all effects schreibt sie dort wieder herunter.

---

## 7. Wichtige Code-Pfade — Schnell-Referenz

| Aufgabe | Datei | Symbole |
|---------|-------|---------|
| Audio-Synthese | `telemffb/hw/shaker_synth.py` | `Oscillator` (`set` / `trigger` / `trigger_pulse`), `PhaseAccumulator`, `ImpulseTrainOscillator`, `BandpassNoiseGenerator`, `ShakerSynth` (`get_oscillator`, `get_noise_oscillator`, `add_oscillator`, `peek_oscillator`, `list_oscillator_names`, `remove_oscillator`), `_callback`, `build_pulse_envelope`, `--selftest*` CLI |
| HapticEffect-Facade | `telemffb/hw/ffb_shaker.py` | `HapticEffect` (`physics`, `fire_impulse`), `Layer` (mit v2/v3-Feldern `center_hz` / `bandwidth_hz` / `attack_ms` / `decay_ms`), `EFFECT_LAYERS`, `SHAKER_EFFECT_WHITELIST`, `SHAKER_EFFECT_PROFILES`, `SHAKER_PHYSICS_PROFILES`, `EFFECT_PHYSICS_IMPULSE` Sentinel, `ACTIVE_PROFILE` / `set_active_profile`, `_pulse_kwargs`, `_BUILTIN_DEFAULT_LAYERS`, `reload_layers`, `get_builtin_default_for` |
| Shaker-Profil (Kalibrierung) | `telemffb/hw/shaker_profile.py` | `ShakerProfile` Dataclass, `DEFAULT_PROFILE` |
| Profile JSON I/O | `telemffb/hw/shaker_profiles_io.py` | `load`, `save`, `get_active`, `get_profiles_path`, `get_default_profiles_pack_path` |
| Layer JSON I/O | `telemffb/hw/shaker_layers_io.py` | `load`, `save`, `get_shaker_effects_path`, `get_default_pack_path`, `CURRENT_VERSION = 3` |
| Default Layer-Pack | `telemffb/data/shaker_effects_default.json` | 17 Effekte (Schema-v1 auf Disk, lädt unter v3-Loader sauber) |
| Default Profile-Pack | `telemffb/data/shaker_profiles_default.json` | 3 Profile (Generic, Dayton DAEX-25, Buttkicker LFE) |
| System Settings UI | `telemffb/SystemSettingsDialog.py` | `_setup_shaker_tab`, `_setup_shaker_layers_section`, `_make_layer_row_widgets`, Calibration-Sektion (`_setup_shaker_calibration_section`, Resonance-Sweep, Single-Pulse-Tuner, A/B-Replay, Save/Save-As/Reset), `_on_shaker_layer_*` Slots |
| Waveform-Preview | `telemffb/ui/ShakerWaveformWidget.py` | Live-Preview von `build_pulse_envelope()`, byte-identisch zur Audio-Pipeline |
| Settings-Defaults | `telemffb/utils.py` (re-export `PhaseAccumulator`), `telemffb/globals.py` | `globl_sys_dict` (`shakerDevice`, `shakerGain`, `shakerChannelMode`, `shakerPan`, `shakerProfile`) |
| Shaker-Init | `main.py` | `_initialize_device_connection` (Shaker-Branch), Profile-Load + `set_active_profile` nach Layer-Reload |
| Effekt-Definitionen | `telemffb/sim/aircraft_base.py` | `ac_update_*` Methoden, `effects[name].periodic/.constant/.physics/.fire_impulse/.start/.stop`, `engine_rumble_mode` Feld |
| Aircraft-Tunables | `defaults.xml` | `engine_rumble_mode`, `prop_blade_count`, `prop_reduction_ratio`, `cylinder_firing_enabled`, `cylinder_count`, `is_4_stroke`, `tailrotor_*`, `touchdown_vs_*` |
| Tests | `tests/test_phase_locked_impulses.py` | Offline-Tests für `PhaseAccumulator` + `ImpulseTrainOscillator`, läuft via `python -m tests.test_phase_locked_impulses` |
| Iterations-Pläne | `docs/shaker-mvp/`, `docs/shaker-polish-layers/`, `docs/shaker-cleanups-noise/`, `docs/shaker-envelope-override/` | STEP_*-Specs, PLAN_*.md, `SMOKETEST_RESULTS.md` |

---

## 8. Limitationen

### 8.1 Aktuelle Synth-Beschränkungen

- **Sinus + Impuls-Envelope + Bandpass-Rauschen** als Synthese-Primitive.
  Keine FM, keine Multi-Oszillator-Voices pro Layer. Body-Texture, die
  über das hinausgeht (z. B. modulierte Engine-Pitches), muss durch
  mehrere Layer approximiert werden.
- **Bandpass-Noise inner loop** ist eine Python-`for`-Schleife pro
  Sample über das Biquad-Filter. Bei 256-Sample-Blöcken @ 48 kHz
  unproblematisch; falls Profiling Bottlenecks zeigt, ist Vektorisierung
  via `scipy.signal.lfilter` die nächste Stufe (ist heute bewusst
  zurückgehalten, um die scipy-Dependency zu vermeiden).
- **Effect-Type ignoriert**: `EFFECT_SQUARE`, `EFFECT_TRIANGLE`,
  `EFFECT_SAWTOOTH*` werden auf dem Shaker als Sinus gerendert. Die
  Charakteristik kommt aus Profil/Layer-Tuning, nicht aus der Wave-Shape.
- **Block-Size 256 @ 48 kHz** = ~5 ms Latenz im Synth. WASAPI/WDM-Treiber
  legen typisch 10–20 ms drauf. Attack-Werte unter 1 ms sind
  versteckungs-equivalent.

### 8.2 Layer-Modell

- **Stick ist nicht layer-aware.** In dieser Iteration läuft der Stick
  weiter wie bisher und spielt jeden Effekt komplett. Layer-Tagging
  `route="stick"` filtert auf der Shaker-Seite — auf der Stick-Seite ist
  es ohne Wirkung. Das ist beabsichtigt und reversibel; eine spätere
  Iteration kann die Stick-Pipeline auch layer-aware machen.
- **Schema-Version 3.** Migrations-Mechanik (Loader akzeptiert v1, v2 und v3,
  Saver schreibt v3) existiert; weitere Schema-Bumps müssen bei
  künftigen Layer-Feldern wieder durchlaufen werden.
- **Keine Pro-Aircraft-Layer-Overrides.** Layer-Pack ist global.

### 8.3 Routing / Whitelist

- **Whitelist-Drift.** Neue Effekte in `aircraft_base.py` sind erstmal
  stumm auf dem Shaker, bis sie der Whitelist hinzugefügt werden. Das ist
  eher Feature als Bug, sollte aber dokumentiert sein. — Lint-Test
  `tests/test_shaker_whitelist.py` scant die Sim-Module nach
  `effects["..."]`-Triggern und schlägt fehl, wenn ein Name weder in
  `SHAKER_EFFECT_WHITELIST` noch in der `KNOWN_NON_SHAKER_EFFECTS`-
  Allowlist (test-lokal, gruppiert nach Begründung: Force-only,
  Stick-only, Pending-Audit, etc.) steht. Damit wird Drift sichtbar.
  Test läuft headless ohne Audio-Hardware (parst die Whitelist via AST
  aus `ffb_shaker.py`).
- **`SHAKER_EFFECT_PROFILES` enthält nur Sinus-getunte Defaults.** Eine
  künftige Vereinheitlichung könnte alle Profile als 1-Layer-Einträge
  ins Default-Pack migrieren und PROFILES streichen — nach dem
  STEP_01-Cleanup sind nur noch 6 Einträge übrig (`gearclunk`,
  `runway_bump0`, `runway_bump1`, `payload_rel`, `buffeting2`,
  `gearbuffet2`), die nicht von einem Default-Pack-Layer überschattet
  werden.

### 8.4 Spatial Positioning

- **Mono-Only-DAC.** Wenn das Output-Device `max_output_channels=1`
  meldet und Stereo verweigert, fällt der Synth auf Mono zurück und
  loggt eine Warnung. Channel-Mode `left`/`right`/`pan` werden dann
  No-Op. Settings bleiben aber persistiert — beim Wechsel auf ein
  Stereo-Device greifen sie wieder.
- **Equal-Power-Pan ist hardcoded.** Kein lineares Pan, keine konstante
  Lautheit über Phasen-Center hinaus konfigurierbar. Reicht für die
  Hauptanwendung „Shaker hinten-rechts vom Pilot".
- **Nur ein Output-Device.** Es gibt keinen Multi-Device-Mixer; ein
  zweiter Shaker am anderen Output braucht eine zweite Shaker-Child-
  Instanz (was technisch heute nicht vorgesehen ist).

### 8.5 UI

- **Tabelle wächst vertikal** ohne Maximum-Höhe; bei Effekten mit
  vielen Layern könnte der Dialog unhandlich werden. Praktisch sind
  Effekte aktuell auf 2–3 Layer beschränkt. Mit den 10 Spalten ist auch
  die horizontale Breite eines Layer-Rows merklich gewachsen — auf
  schmalen Displays ist horizontales Scrolling möglich.

Mit 10 Spalten ist die horizontale Breite des Layer-Rows merklich
gewachsen — auf schmalen Displays ist horizontales Scrolling
wahrscheinlich. Eine künftige Iteration könnte impulse- und noise-
spezifische Spalten in einen Popover/Expandable-Sub-Row verschieben
(Option B aus dem STEP_07-Brief), wenn die Tabellen-Breite störend
wird.

- **Center/Bandwidth-Werte werden auch für Nicht-Noise-Layer in der
  Working-Copy gespeichert.** Ist beabsichtigt (User behält die Werte
  über Toggle hinweg), bedeutet aber, dass eine sine-Zeile mit zuvor
  gesetzten Noise-Werten im JSON `center_hz: 40.0, bandwidth_hz: 20.0`
  hat. Zur Laufzeit ignoriert; round-trip-safe; aber für Code-Reader
  potentiell verwirrend.

### 8.6 Sim-Coverage

- **Effekt-Definitionen** in `aircraft_base.py` sind nicht in jedem Sim
  gleich vollständig. DCS, MSFS und IL-2 liefern jeweils unterschiedliche
  Telemetrie-Felder (`Damage`, `Hydraulics`, etc.); manche Effekte
  zünden in einem Sim, im anderen nicht. Carry-over aus dem Polish-Brief:
  Damage / Stall break / Catapult / Arrestor wire sind interessante
  Telemetrie-Quellen, die heute nicht oder nur teilweise auf Shaker-
  Effekte abgebildet sind.

---

## 9. Bekannte Issues / Carry-overs aus den Reviews

### 9.1 Erledigt in den aktuellen Iterationen

1. ✅ **`SHAKER_EFFECT_PROFILES` Cleanup** (STEP_01 / `8a89197`):
   6 Dead-Profile-Einträge gelöscht (`touchdown`, `gunfire`, `cm`,
   `buffeting`, `vrs_buffet`, `gearbuffet`), Surviving-Set kommentiert.
2. ✅ **DRY-Refactor `_on_shaker_layer_add` ↔ `_shaker_layer_rebuild_table`**
   (STEP_02 / `2ec3d48`): gemeinsamer Helper
   `_make_layer_row_widgets(row, layer) -> dict`.
3. ✅ **Test-Worker-Kapselung** (STEP_03 / `d542974`): neue Public-API auf
   `ShakerSynth` (`add_oscillator`, `peek_oscillator`,
   `list_oscillator_names`, `get_noise_oscillator`); `_oscillators` /
   `_lock` werden außerhalb `shaker_synth.py` nirgends mehr direkt
   angefasst.
4. ✅ **Logging vs. print** (STEP_04 / `d509a25`): die sechs `print()`
   in `_selftest_layered()` sind jetzt `logger.info(...)` mit lazy
   `%`-Formatting.
5. ✅ **Bandpass-Rauschen als zusätzlicher `osc_type`** (STEP_05–07 /
   `c813ce8`–`fccfb4e`): `BandpassNoiseGenerator` + Layer-Schema-v2 mit
   `center_hz` / `bandwidth_hz` + UI-Spalten + `--selftest-noise` CLI.
6. ✅ **Nebenbei behoben** (`663310b`): `python -m telemffb.hw.ffb_shaker
   --selftest-layered` lief in einen Circular-Import (runpy registrierte
   das Entry-Modul nur als `__main__`, nicht unter dem Dotted-Name);
   `Layer`-Import in `shaker_layers_io.py` jetzt funktion-lokal.
7. ✅ **Schema-v3 Per-Layer-Envelope-Override** (Iteration
   `docs/shaker-envelope-override/`, STEP_01 / `bd387e2`,
   STEP_02 / `8fe77f5`): `Layer.attack_ms` / `Layer.decay_ms` werden
   bei `osc_type="impulse"` per kwargs an `Oscillator.trigger()`
   durchgereicht; UI-Spalten 9/10 mit den gleichen
   Toggle-/Working-Copy-Semantiken wie Center/Bandwidth.
8. ✅ **Calibration-UI mit Single-Pulse + Active Braking**
   (Commit `a48f1b4`): `ShakerProfile`-Dataclass + JSON-I/O,
   `Oscillator.trigger_pulse()` mit gated Halbwellen-Drive und
   phasen-invertierter Brake-Halbwelle, `_pulse_kwargs()` als zentraler
   Kwargs-Composer für Profile + Layer-Override, Calibration-Sektion
   in System Settings (Resonance Sweep, Single-Pulse Tuner mit A/B-
   Replay, Live-Waveform-Preview), 3 gebundelte Default-Profile
   (Generic / Dayton DAEX-25 / Buttkicker LFE), `--selftest-pulse`
   CLI, QSettings-Key `shakerProfile`. Single-Pulse-Effekte
   (`gearclunk`, `runway_bump*`, `payload_rel`, Layer-Impulses)
   gehen jetzt durch `trigger_pulse()` statt `trigger()`.
9. ✅ **Phase-Locked Impulse Synthesis (Physics Mode)**
   (Commit `53f0c5b`): `PhaseAccumulator` integriert
   `rpm × dt` lokal, `ImpulseTrainOscillator` feuert
   `trigger_pulse()` an jedem Blade-Pass-/Zylinder-Zündung-
   Crossing — telemetry-rate-unabhängig. Neue
   `HapticEffect.physics(rpm, divisions, load)` und
   `.fire_impulse(magnitude)` Facade-Methoden plus
   `EFFECT_PHYSICS_IMPULSE`-Sentinel und `SHAKER_PHYSICS_PROFILES`-
   Map (Carrier/Halbwellen/Brake pro Voice). Aircraft-Handler
   refaktoriert: `ac_update_heli_engine_rumble`,
   `ac_update_piston_engine_rumble`, `ac_update_touchdown_effect`
   dispatchen über `engine_rumble_mode` (default `"physics"`).
   Touchdown-Impuls fired auf WoW-Rising-Edge mit Magnitude aus
   smoothed VS. 12 neue `defaults.xml`-Tunables (Rumble-Mode,
   Prop/Cylinder-Specs, Tail-Rotor, Touchdown-VS-Schwellen).
   6 Offline-Tests in `tests/test_phase_locked_impulses.py`,
   `--selftest-phase-locked` CLI.
10. ✅ **Physics-Voice Auto-Fallback auf Bandpass-Noise**:
    `ImpulseTrainOscillator` hostet einen internen
    `BandpassNoiseGenerator` und schaltet automatisch um, sobald
    `impulse_hz ≥ max_impulse_rate_hz` (Hysterese: Eintritt bei 100% des
    Caps, Ausstieg erst bei 85%). Bandpass-Center wird auf die aktuelle
    Impuls-Rate gesetzt; Bandbreite default = 20% des Centers (Floor
    15 Hz), via `noise_bandwidth_hz` in `SHAKER_PHYSICS_PROFILES`
    überschreibbar. ~80 ms Crossfade auf beiden Voices vermeidet Klicks
    am Übergang. 4 neue Offline-Tests (Fallback-Engagement,
    Hysterese-Stabilität, Re-Entry, Crossover ohne Silence-Dip).

### 9.2 Offen

7. **MSFS-Test offen**: `docs/shaker-polish-layers/MSFS_LAYER_TEST.md` ist
   ein Template, das der User auf realer Hardware durchläuft. Defaults
   im Pack sind initial-curated und werden auf Basis der Test-Notes
   nachgetuned. Außerdem: nach erfolgreichem MSFS-Test ist die
   manuelle Verifikations-Checkliste in
   `docs/shaker-cleanups-noise/SMOKETEST_RESULTS.md` für die UI- und
   Bandpass-Noise-Funktionalität abzuarbeiten.
8. **Noise-Defaults im Pack ergänzen**: Default-Pack bleibt absichtlich
   v1 auf Disk; sobald die MSFS-Validierung der bestehenden Layer
   abgeschlossen ist, kann der User per UI Noise-Layer (z. B. für
   `je_rumble_*`, `prop_rpm0-1`, `runway0`) ergänzen und das Resultat
   in `shaker_effects_default.json` zurückspielen.
9. **Profile Live-Push**: das aktive `ShakerProfile` wird beim Start der
   Shaker-Child geladen. Änderungen über die Calibration-UI erfordern
   einen Restart der Child, um zu greifen — IPC-Live-Push ist nicht
   implementiert. Praktisch ist das hinnehmbar (Calibration ist eine
   Once-Per-Hardware-Aufgabe), sollte aber dokumentiert sein.
10. **Physics-Mode Hardware-Validation**: die Phase-Locked-Voices
    (`rotor_phys_*`, `prop_phys_*`, `cyl_phys_*`, `touchdown_vs`)
    sind offline numerisch verifiziert (6 Tests grün) und mit
    Smoke-Audio durchgespielt. Validierung am realen Setup
    (Hubschrauber-Spool-Up in DCS Mi-24, Spitfire Mk IX
    Niedrig-RPM-Taxi, Carrier-Landing für Touchdown-VS) steht aus —
    Tuning der Carrier/Halbwellen/Brake-Werte in
    `SHAKER_PHYSICS_PROFILES` erfolgt auf Basis der Hardware-Notes.
---

## 10. Erweiterungs-Hooks für die Zukunft

Wenn die Layer-Routing-Iteration bewährt ist, sind folgende Erweiterungen
naheliegend:

- **Stick-Side Layer-Awareness**: dasselbe `EFFECT_LAYERS`-Schema im
  Rhino-Backend implementieren. Stick-Layer würden dann symmetrisch
  gefiltert (`route ∈ {stick, both}`). User könnte dann z. B. `touchdown`
  am Stick komplett wegnehmen, falls der Shaker den Body-Thump alleine
  besser liefert.
- **Pro-Aircraft-Packs**: `shaker_effects.<aircraft>.json` overlay über
  dem globalen Pack — analog zur bestehenden Per-Aircraft-Profile-Logik
  in `defaults.xml`.
- **PROFILES → Layer-Migration**: nach Schema v3 sind alle bisher in
  `SHAKER_EFFECT_PROFILES` getunten Werte (`freq`, `gain`, `kind`,
  `attack_ms`/`decay_ms`/`ramp_ms`) als 1-Layer-Default im Pack
  abbildbar. Eine künftige Iteration könnte alle 6 surviving
  Profile-Einträge ins Default-Pack migrieren und die `PROFILES`-Tabelle
  plus die Stufe-4-Routing-Logik komplett streichen.
- **Vektorisierte Bandpass-Noise-Pipeline**: heutige Implementierung
  benutzt eine Python-`for`-Schleife im Biquad-Inner-Loop. Falls
  Profiling auf Hardware das als Hotspot zeigt, ist `scipy.signal.lfilter`
  der nächste Schritt — aktuell zurückgehalten, um die scipy-Dependency
  zu vermeiden.
- **Telemetrie-Ausweitung**: Damage-Hits, Stall-Break, Catapult-Launch,
  Arrestor-Wire — sind in den Sims als Telemetrie verfügbar, haben aber
  noch keine Shaker-Effekt-Definition in `aircraft_base.py`.

---

## 11. Quick Reference — Häufige Aufgaben

**Neue Layer-Defaults für einen Effekt deployen:**
1. In System Settings → Shaker → Effect layers den Effekt wählen,
   tunen, Save.
2. Funktioniert? Den finalen Stand aus
   `<userconfig_rootpath>/shaker_effects.json` kopieren in
   `telemffb/data/shaker_effects_default.json` und committen.

**Neuen Effekt in `aircraft_base.py` shaker-fähig machen:**
1. Effekt-Name zur `SHAKER_EFFECT_WHITELIST` in `ffb_shaker.py` hinzufügen.
2. Optional: einen Eintrag in `SHAKER_EFFECT_PROFILES` für
   single-oscillator-tuning, **oder** im Default-Pack als Layer-Stack.

**Neue Phase-Locked-Voice ergänzen (siehe §3.6):**
1. Voice-Name zur Whitelist hinzufügen (z. B. `gear_door_phys`).
2. Eintrag in `SHAKER_PHYSICS_PROFILES` mit Carrier/Halbwellen/Brake/
   `max_impulse_rate_hz`.
3. In `aircraft_base.py` den passenden `ac_update_*`-Handler:
   `effects["gear_door_phys"].physics(rpm=..., divisions=..., load=...).start()`
   bzw. `.stop()` wenn die Voice silent sein soll.
4. Tunables in `defaults.xml` ergänzen, falls benötigt.

**Audio-Output debuggen ohne Sim:**
- `python -m telemffb.hw.shaker_synth --list-devices`
- `python -m telemffb.hw.shaker_synth --selftest --device <idx>`
- `python -m telemffb.hw.shaker_synth --selftest-transient --device <idx>`
- `python -m telemffb.hw.shaker_synth --selftest-pulse --device <idx>` —
  Single-Pulse mit Active-Brake, primär für Calibration-Sanity-Check.
- `python -m telemffb.hw.shaker_synth --selftest-phase-locked --device <idx>` —
  Hubschrauber-Spool-Up Demo: 0 → 250 RPM × 5 Blätter über 4 s, hold,
  spool down. Demonstriert die Phase-Locked-Impulse-Pipeline.
- `python -m telemffb.hw.shaker_synth --selftest-noise --device <idx> [--center HZ --bandwidth HZ]`
- `python -m telemffb.hw.ffb_shaker --selftest-layered --device <idx>`

**Tests laufen lassen (offline, ohne Audio-Hardware):**
- `python -m tests.test_phase_locked_impulses` — 9 numerische Tests für
  `PhaseAccumulator` und `ImpulseTrainOscillator` (inkl. Auto-Fallback
  auf Bandpass-Noise oberhalb `max_impulse_rate_hz`: Engagement,
  Hysterese-Stabilität, Re-Entry, Crossover-ohne-Silence). Läuft auch
  unter Linux/CI (winreg lazy-import).
- `python -m unittest tests.test_shaker_whitelist` — statische Lint-
  Suite, die Sim-Module gegen `SHAKER_EFFECT_WHITELIST` +
  `KNOWN_NON_SHAKER_EFFECTS` diff't. Failt mit klarer Liste, wenn ein
  neuer Effekt weder whitelisted noch allow-listed ist (siehe §8.3).
  Importiert `ffb_shaker` nicht direkt — parst die Whitelist via AST,
  damit der Test ohne `sounddevice`/PortAudio läuft.

**Shaker-Profil wechseln:**
- System Settings → Shaker → Calibration → Profile-Dropdown → Profil
  wählen → Save → **Restart der Shaker-Child**, damit `set_active_profile`
  vom Bootstrap-Path neu greift (kein IPC-Live-Push, siehe §9.2).

**Settings zurücksetzen:**
- System Settings → Shaker → Effect layers → `Reset all effects to defaults`
  (mit Bestätigungsdialog) → schreibt das Layer-Bundle wieder ins User-File.
- System Settings → Shaker → Calibration → `Reset` → schreibt das
  Profile-Bundle (`shaker_profiles_default.json`) wieder ins User-File.
- Master Reset über Main-Menü → Utilities → Reset User Config (greift
  weiter, nicht nur Shaker).
