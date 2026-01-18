# TelemFFB Demo Scripts

This directory contains demonstration scripts for VPforce FFB hardware features.

## Prerequisites

1. **Hardware**: VPforce Rhino FFB joystick connected via USB
2. **Python Environment**: Python 3.x with PyQt6 installed
3. **Dependencies**: Install from project root:
   ```bash
   pip install -r requirements.txt
   ```

## Running Demos

**Important**: Demos must be run from the **project root directory** so Python can find the `telemffb` module.

### Using Python Module Syntax

```bash
# From project root
python -m demo.demo_envelope_effect
python -m demo.demo_detent_effect
```

## Available Demos

### 1. Envelope Effect Demo (`demo_envelope_effect.py`)

Demonstrates attack/decay envelope features for force effects.

**Features:**
- Persistent envelope (applied on every start)
- One-time envelope (applied once, cleared on stop)
- Interactive "hold stick" prompts
- Automatic QApplication event loop integration

**Usage:**
```bash
# From project root
python demo/demo_envelope_effect.py
```

**What you'll feel:**
- **First demo**: Periodic effect with fade-in that repeats on every start
- **Second demo**: Constant force with fade-in only on first start

### 2. Detent Effect Demo (`demo_detent_effect.py`)
Interactive demonstration of various detent (notch/groove/latch) effects.

**Features:**
- 5 pre-configured detent scenarios
- Vertical grooves (gear shifter simulation)
- Position latches (throttle gate simulation)
- Center detents (stick neutral position)
- Single-axis grooves (X-only movement)
- Multiple groove patterns (full gearbox simulation)

**Usage:**
```bash
# From project root
# Run specific effect
python demo/demo_detent_effect.py 1    # Vertical groove
python demo/demo_detent_effect.py 2    # Latch detent
python demo/demo_detent_effect.py 3    # Centered detent
python demo/demo_detent_effect.py 4    # Single-axis groove
python demo/demo_detent_effect.py 5    # Multiple grooves

# Print parameter info without hardware
python demo/demo_detent_effect.py all

# Interactive mode (prompts for effect selection)
python demo/demo_detent_effect.py
```

**What you'll feel:**
- Grooves: Magnetic valleys the stick naturally settles into
- Latches: Resistance "gates" requiring force to push through
- Centering: Strong spring toward neutral position