"""Run TelemFFB under a once-a-second dump of every thread's stack.

For an exit that takes longer than it should: the log goes quiet after the
last cleanup line, and this shows what each thread was doing during the
seconds the process lingered.  Run it instead of main.py, from anywhere:

    python demo/exit_probe.py            (set PROBE_OFFSCREEN=1 for no windows)

Then exit TelemFFB the way you normally do and read threads.txt beside this
file - the last few snapshots are the ones that matter.  pid.txt holds the
master's pid, so Task Manager can tell the master from its children.
"""
import faulthandler
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
if os.environ.get("PROBE_OFFSCREEN") == "1":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.chdir(REPO)
sys.path.insert(0, REPO)

open(os.path.join(HERE, "pid.txt"), "w").write(str(os.getpid()))
trace = open(os.path.join(HERE, "threads.txt"), "w", buffering=1)
faulthandler.dump_traceback_later(1.0, repeat=True, file=trace)

sys.argv = ["main.py"]
runpy.run_path("main.py", run_name="__main__")
