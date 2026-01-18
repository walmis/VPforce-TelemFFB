#!/usr/bin/env python3
"""
Detent Effect API Demo
======================

Interactive demo for testing detent effects on real hardware.

Usage:
    python demo_detent_effect.py [effect_number]
    
    effect_number:
        1 - Vertical groove detent (gear shifter)
        2 - Latch detent with position
        3 - Centered detent (neutral position)
        4 - Single-axis groove (X only)
        5 - Multiple groove detents (full gear shifter pattern)
        all - Print parameter mapping only (no hardware)
"""

import sys
import time
import logging
from telemffb.hw.ffb_rhino import HapticEffect, EFFECT_DETENT

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def initialize_hardware():
    """Initialize FFB hardware device"""
    logger.info("Initializing FFB hardware...")
    try:
        # Open device using HapticEffect class method
        # This sets HapticEffect.device for all effect instances
        device = HapticEffect.open()
        
        if not device:
            logger.error("Failed to connect to FFB device")
            logger.error("Make sure:")
            logger.error("  1. VPforce Rhino device is connected via USB")
            logger.error("  2. No other application is using the device")
            logger.error("  3. Device drivers are installed correctly")
            return False
        
        logger.info(f"✓ Connected to FFB device")
        logger.info(f"  Serial: {device.serial}")
        logger.info(f"  Product: {device.product}")
        
        # Reset all effects on device
        device.reset_effects()
        logger.info("✓ Reset all effects")
        
        return True
    except Exception as e:
        logger.error(f"Hardware initialization failed: {e}")
        return False

def cleanup_hardware():
    """Clean up hardware and stop all effects"""
    if HapticEffect.device:
        logger.info("Cleaning up...")
        # Reset all effects
        HapticEffect.device.reset_effects()
        logger.info("✓ All effects reset")
        # Note: Device will be garbage collected, no explicit close needed

def test_vertical_groove():
    """Test 1: Vertical groove detent (from firmware init_hshifter)"""
    print("\n" + "="*70)
    print("TEST 1: Vertical Groove Detent (Gear Shifter)")
    print("="*70)
    
    # Constants from firmware
    groove_detent_size = 500   # peak
    groove_detent_range = 250  # range
    
    logger.info("Creating vertical groove at x=2000 (first gear position)")
    
    # Device is already set by HapticEffect.open()
    effect = HapticEffect()
    
    # Create vertical groove at x=2000
    effect.detent(
        position_x=2000,
        peak_x=groove_detent_size,  
        range_x=groove_detent_range,
        gate_pos_y=0,            
        gate_neg_y=0,           
        deadband_x=4
    )
    
    logger.info("Starting effect...")
    effect.start()
    
    print(f"\n✓ Vertical groove active at x=2000")
    print(f"  Peak: {groove_detent_size}, Range: {groove_detent_range}")
    print(f"  Gate Y: -10000 to -400")
    print(f"\n→ Move stick left/right to feel the groove at x=2000")
    print("  Press Ctrl+C to stop...")
    
    try:
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        logger.info("Stopping effect...")
        effect.stop()
        logger.info("✓ Effect stopped")

def test_latch_detent():
    """Test 2: Latch detent at specific position"""
    print("\n" + "="*70)
    print("TEST 2: Latch Detent with Position")
    print("="*70)
    
    # Constants from firmware
    latch_detent_size = 1500
    latch_detent_range = 2000
    groove_detent_size = 2500
    groove_detent_range = 3000
    
    logger.info("Creating latch at y=-3000 with groove at x=2000")
    
    effect = HapticEffect()
    
    # Latch at y=-3000, with groove at x=2000
    effect.detent(
        position_y=-3000,              # e->cp.y = -3000
        peak_y=latch_detent_size,      # e->coef.pos.y = 1500
        range_y=latch_detent_range,    # e->coef.neg.y = 2000
        position_x=2000,               # e->cp.x = 2000
        peak_x=groove_detent_size,     # e->coef.pos.x = 2500
        range_x=groove_detent_range,   # e->coef.neg.x = 3000
        gate_pos_y=-400,
        gate_neg_y=-10000
    )
    
    logger.info("Starting effect...")
    effect.start()
    
    print(f"\n✓ Latch detent active")
    print(f"  Latch Y: position=-3000, peak={latch_detent_size}, range={latch_detent_range}")
    print(f"  Groove X: position=2000, peak={groove_detent_size}, range={groove_detent_range}")
    print(f"\n→ Move stick to position (2000, -3000) to feel both effects")
    print("  Press Ctrl+C to stop...")
    
    try:
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        logger.info("Stopping effect...")
        effect.stop()
        logger.info("✓ Effect stopped")

def test_centered_detent():
    """Test 3: Centered detent (neutral position)"""
    print("\n" + "="*70)
    print("TEST 3: Centered Detent (Neutral Position)")
    print("="*70)
    
    logger.info("Creating centered detent at (0, 0)")
    
    effect = HapticEffect()
    
    # Center detent at (0, 0)
    effect.detent(
        position_x=0,       # e->cp.x = 0
        position_y=0,       # e->cp.y = 0
        peak_x=400,         # e->coef.pos.x = 400
        peak_y=400,         # e->coef.pos.y = 400
        range_x=500,        # e->coef.neg.x = 500
        range_y=500,        # e->coef.neg.y = 500
        gate_pos_x=2000,    # e->saturation.pos.x = 2000
        gate_neg_x=-2000,   # e->saturation.neg.x = -2000
        gate_pos_y=2000,    # e->saturation.pos.y = 2000
        gate_neg_y=-2000    # e->saturation.neg.y = -2000
    )
    
    logger.info("Starting effect...")
    effect.start()
    
    print(f"\n✓ Centered detent active at (0, 0)")
    print(f"  Peak: 400, Range: 500 on both axes")
    print(f"  Gates: -2000 to +2000 on both axes")
    print(f"\n→ Release stick to feel it center at neutral position")
    print("  Press Ctrl+C to stop...")
    
    try:
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        logger.info("Stopping effect...")
        effect.stop()
        logger.info("✓ Effect stopped")

def test_single_axis_groove():
    """Test 4: Single-axis groove (X only)"""
    print("\n" + "="*70)
    print("TEST 4: Single-Axis Groove (X-axis only)")
    print("="*70)
    
    groove_detent_size = 2500
    groove_detent_range = 3000
    
    logger.info("Creating X-axis groove at x=-2000")
    
    effect = HapticEffect()
    
    # Groove at x=-2000, no Y detent
    effect.detent(
        position_x=-2000,
        peak_x=groove_detent_size,
        range_x=groove_detent_range,
        gate_pos_y=10000,   # e->saturation.pos.y = 10000 (wide open)
        gate_neg_y=400      # e->saturation.neg.y = 400
    )
    
    logger.info("Starting effect...")
    effect.start()
    
    print(f"\n✓ X-axis groove active at x=-2000")
    print(f"  Peak: {groove_detent_size}, Range: {groove_detent_range}")
    print(f"  Gate Y: 400 to 10000 (wide open)")
    print(f"\n→ Move stick left/right to feel groove, Y-axis free")
    print("  Press Ctrl+C to stop...")
    
    try:
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        logger.info("Stopping effect...")
        effect.stop()
        logger.info("✓ Effect stopped")

def test_multiple_grooves():
    """Test 5: Multiple groove detents (full gear shifter pattern)"""
    print("\n" + "="*70)
    print("TEST 5: Multiple Groove Detents (Full H-Pattern Shifter)")
    print("="*70)
    
    groove_detent_size = 2500
    groove_detent_range = 3000
    
    # From firmware: 4 grooves for a typical H-pattern shifter
    grooves = [
        {"x": 2000, "gate_pos_y": -400, "gate_neg_y": -10000, "name": "1st/2nd gear groove"},
        {"x": -2000, "gate_pos_y": -400, "gate_neg_y": -10000, "name": "3rd/4th gear groove"},
        {"x": -2000, "gate_pos_y": 10000, "gate_neg_y": 400, "name": "5th/6th gear groove"},
        {"x": 2000, "gate_pos_y": 10000, "gate_neg_y": 400, "name": "7th/Reverse groove"}
    ]
    
    effects = []
    
    logger.info(f"Creating {len(grooves)} groove detents for H-pattern shifter")
    
    for i, groove in enumerate(grooves, 1):
        effect = HapticEffect()
        
        effect.detent(
            position_x=groove["x"],
            peak_x=groove_detent_size,
            range_x=groove_detent_range,
            gate_pos_y=groove["gate_pos_y"],
            gate_neg_y=groove["gate_neg_y"]
        )
        
        effect.start()
        effects.append(effect)
        logger.info(f"  Groove {i}: x={groove['x']:>5}, gates=({groove['gate_neg_y']}, {groove['gate_pos_y']})")
    
    print(f"\n✓ All {len(effects)} grooves active!")
    print("\nPattern:")
    for i, groove in enumerate(grooves, 1):
        print(f"  {i}. x={groove['x']:>5} → {groove['name']}")
    
    print(f"\n→ Move stick through gear positions to feel complete H-pattern")
    print("  Press Ctrl+C to stop...")
    
    try:
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        logger.info("Stopping all effects...")
        for effect in effects:
            effect.stop()
        logger.info(f"✓ All {len(effects)} effects stopped")

def print_parameter_mapping():
    """Print parameter mapping reference"""
    print("\n")
    print("#" * 70)
    print("#" + " " * 68 + "#")
    print("#  VPforce Rhino FFB - Detent Effect Parameter Reference" + " " * 10 + "#")
    print("#" + " " * 68 + "#")
    print("#" * 70)
    print()
    print("="*70)
    print("PARAMETER MAPPING: Python API ←→ Firmware Code")
    print("="*70)
    print()
    print("Python Parameter        | Firmware Parameter     | Description")
    print("-" * 70)
    print("position_x, position_y  | e->cp.x, e->cp.y       | Center position")
    print("peak_x, peak_y          | e->coef.pos.x, .pos.y  | Detent size/strength")
    print("range_x, range_y        | e->coef.neg.x, .neg.y  | Detent range/width")
    print("gate_pos_x, gate_pos_y  | e->saturation.pos.x/y  | Upper gate/bound")
    print("gate_neg_x, gate_neg_y  | e->saturation.neg.x/y  | Lower gate/bound")
    print()
    print("Firmware Constants (from init_hshifter):")
    print("  groove_detent_size  = 2500  (peak for gear grooves)")
    print("  groove_detent_range = 3000  (range for gear grooves)")
    print("  latch_detent_size   = 1500  (peak for latch positions)")
    print("  latch_detent_range  = 2000  (range for latch positions)")
    print()
    print("Automatic Firmware Features:")
    print("  - 400-unit smoothing range at gate edges")
    print("  - -1000 damping coefficient when force is active")
    print("  - Cross-axis gain modulation within smoothing range")
    print()

def print_usage():
    """Print usage information"""
    print("\n" + "="*70)
    print("AVAILABLE TESTS:")
    print("="*70)
    print()
    print("  1 - Vertical groove detent (gear shifter)")
    print("      Single vertical groove at x=2000")
    print()
    print("  2 - Latch detent with position")
    print("      Latch at y=-3000 with groove at x=2000")
    print()
    print("  3 - Centered detent (neutral position)")
    print("      Centering force at (0, 0)")
    print()
    print("  4 - Single-axis groove (X only)")
    print("      Horizontal groove at x=-2000, Y-axis free")
    print()
    print("  5 - Multiple groove detents (full gear shifter)")
    print("      Complete H-pattern with 4 grooves")
    print()
    print("  all - Print parameter mapping only (no hardware)")
    print()
    print("Usage: python demo_detent_effect.py [test_number]")
    print()

def main():
    """Main entry point"""
    if len(sys.argv) < 2:
        print_parameter_mapping()
        print_usage()
        sys.exit(1)
    
    test_num = sys.argv[1].lower()
    
    # Special case: just print reference
    if test_num == "all":
        print_parameter_mapping()
        return
    
    # Validate test number
    try:
        test_num = int(test_num)
        if test_num < 1 or test_num > 5:
            logger.error(f"Invalid test number: {test_num} (must be 1-5 or 'all')")
            print_usage()
            sys.exit(1)
    except ValueError:
        logger.error(f"Invalid test number: {sys.argv[1]} (must be 1-5 or 'all')")
        print_usage()
        sys.exit(1)
    
    # Initialize hardware
    if not initialize_hardware():
        sys.exit(1)
    
    try:
        # Run selected test
        if test_num == 1:
            test_vertical_groove()
        elif test_num == 2:
            test_latch_detent()
        elif test_num == 3:
            test_centered_detent()
        elif test_num == 4:
            test_single_axis_groove()
        elif test_num == 5:
            test_multiple_grooves()
    
    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
    
    finally:
        cleanup_hardware()

if __name__ == "__main__":
    main()


