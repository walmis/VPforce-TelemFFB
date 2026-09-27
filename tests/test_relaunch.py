"""Starting TelemFFB again after a restart: the same command line, a reset
PyInstaller environment, and independence from the process that asked."""
import subprocess
import sys

import pytest

from telemffb.utils import filesystem

pytestmark = [pytest.mark.unit]


@pytest.fixture
def spawned(monkeypatch):
    calls = []

    def popen(args, **kwargs):
        calls.append((args, kwargs))
    monkeypatch.setattr(filesystem.subprocess, 'Popen', popen)
    return calls


def test_from_source_the_script_runs_under_the_same_interpreter(spawned, monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['main.py', '--minimize'])
    monkeypatch.delattr(sys, 'frozen', raising=False)
    filesystem.relaunch()
    args, kwargs = spawned[-1]
    assert args == [sys.executable, 'main.py', '--minimize']
    assert kwargs['env']['PYINSTALLER_RESET_ENVIRONMENT'] == '1'


def test_a_frozen_build_runs_its_own_executable(spawned, monkeypatch):
    monkeypatch.setattr(sys, 'argv', [r'C:\TelemFFB\VPforce-TelemFFB.exe', '--minimize'])
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    filesystem.relaunch()
    assert spawned[-1][0] == [sys.executable, '--minimize']


@pytest.mark.skipif(sys.platform != 'win32', reason="Windows job objects")
def test_a_job_that_refuses_breakaway_still_relaunches(monkeypatch):
    calls = []

    def popen(args, creationflags=0, **kwargs):
        calls.append(creationflags)
        if creationflags & subprocess.CREATE_BREAKAWAY_FROM_JOB:
            raise PermissionError("access denied")
    monkeypatch.setattr(filesystem.subprocess, 'Popen', popen)
    monkeypatch.setattr(sys, 'argv', ['main.py'])
    filesystem.relaunch()
    assert len(calls) == 2
    assert not calls[-1] & subprocess.CREATE_BREAKAWAY_FROM_JOB
