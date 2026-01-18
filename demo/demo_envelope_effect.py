#!/usr/bin/env python3
"""
Demonstration of envelope effect feature.

This demo shows how to use the .envelope() method with periodic and constant effects.
The envelope behavior depends on the 'once' parameter:

1. Persistent envelope (once=False, default): Applied on EVERY start
2. One-time envelope (once=True): Applied once, cleared on stop

IMPORTANT: Envelope decay phase only executes when effect duration expires naturally.
           Explicit .stop() calls will stop the effect immediately without decay.

Usage:
    python demo_envelope_effect.py
"""

import sys
import time
import logging
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer, QThread, pyqtSignal
from telemffb.hw.ffb_rhino import HapticEffect, FFBReport_SetEnvelope

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


class DemoThread(QThread):
    """Worker thread for running demos while QApplication processes events."""
    finished = pyqtSignal()
    error = pyqtSignal(str)
    
    def run(self):
        """Execute demos in background thread."""
        try:
            demo_periodic_with_persistent_envelope()
            demo_constant_with_onetime_envelope()
            
            print("\n=== Demo Complete ===")
            print("\nKey Behaviors:")
            print("1. Persistent envelope (once=False, default):")
            print("   - Applied on EVERY start")
            print("   - Persists across multiple start/stop cycles")
            print("\n2. One-time envelope (once=False, default):")
            print("   - Applied only on FIRST start")
            print("   - Cleared on stop, subsequent starts are immediate")
            print("\n3. Envelope phases:")
            print("   - Attack: Always felt when effect starts")
            print("   - Decay: Only felt when effect duration expires naturally")
            print("   - Explicit .stop() = immediate stop, no decay phase")
            
            self.finished.emit()
        except Exception as e:
            logging.exception(f"Error in demo thread: {e}")
            self.error.emit(str(e))

def demo_periodic_with_persistent_envelope():
    """Demonstrate periodic effect with persistent envelope (default behavior)."""
    print("\n=== Periodic Effect with PERSISTENT Envelope ===")
    print("The envelope will apply on EVERY start (default behavior).")
    print("Note: You'll feel the attack phase. Decay only occurs when duration expires naturally.")
    
    input("\n>>> Hold the stick and press ENTER to start the effect...")
    
    # Create effect and chain envelope method (persistent by default)
    effect = HapticEffect()
    effect.name = "Sine with Persistent Envelope"
    
    # Chain .envelope() after .periodic() - persistent by default (once=False)
    effect.periodic(frequency=10, magnitude=0.5, direction=90, duration=2000) \
         .envelope(
             attackFromForce=0,       # Start from zero for noticeable fade-in
             decayToForce=0,          # Fade to zero for noticeable fade-out
             attackTime=500,          # 500ms attack
             decayTime=300            # 300ms decay
         )
    
    print("\nFirst start - feel the 500ms attack ramp-up:")
    effect.start()
    time.sleep(3.0)  # Let it complete naturally to feel the decay
    
    print("Effect completed naturally - you should have felt the 300ms decay fade-out.")
    time.sleep(1.0)
    
    input("\n>>> Hold the stick and press ENTER for second start...")
    print("\nSecond start - envelope still active (persistent):")
    effect.start()
    time.sleep(3.0)  # Let it complete naturally again
    
    effect.destroy()
    print("\nEffect destroyed")


def demo_constant_with_onetime_envelope():
    """Demonstrate constant effect with one-time envelope."""
    print("\n=== Constant Effect with ONE-TIME Envelope ===")
    print("The envelope will apply ONCE, then be cleared on stop.")
    print("Note: Attack happens on start. Decay only if effect duration expires naturally.")
    
    input("\n>>> Hold the stick and press ENTER to start the effect...")
    
    # Create effect with one-time envelope
    effect = HapticEffect()
    effect.name = "Constant with One-Time Envelope"
    
    # Create FFBReport_SetEnvelope instance and mark as once=True
    envelope_params = FFBReport_SetEnvelope(
        attackFromForce=0,       # Start from zero for noticeable fade-in
        decayToForce=0,          # Fade to zero for noticeable fade-out
        attackTime=1000,         # 1 second attack
        decayTime=500            # 500ms decay
    )
    
    # Chain .envelope() with once=True
    effect.constant(magnitude=0.6, direction=180).envelope(envelope=envelope_params, once=True)
    
    print("\nFirst start - feel the 1000ms attack ramp-up, then stop before decay:")
    effect.start()
    time.sleep(2.0)  # Stop before duration expires
    
    print("\nExplicit stop (no decay - immediate stop)...")
    effect.stop(destroy_after=0)
    time.sleep(1.0)
    
    input(">>> Hold the stick and press ENTER for second start...")
    print("\nSecond start - NO envelope (one-time cleared), starts immediately at full force:")
    effect.start()
    time.sleep(2.0)
    
    effect.destroy()
    print("\nEffect destroyed")


def main():
    """Main entry point."""
    # Create QApplication to enable QTimer functionality in FFBRhino
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    
    print("Opening FFB device...")
    try:
        device = HapticEffect.open()
        print(f"Device opened: {device.product}")
        print(f"Serial: {device.serial}")
        
        # Create and start demo thread
        demo_thread = DemoThread()
        demo_thread.finished.connect(app.quit)
        demo_thread.error.connect(lambda err: print(f"Demo error: {err}") or app.quit())
        demo_thread.start()
        
        # Run Qt event loop to process QTimer events
        sys.exit(app.exec())
        
    except Exception as e:
        logging.exception(f"Failed to open device: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
