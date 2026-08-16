"""
Scan all .py files in the repository for syntax and indentation errors.

Python only raises SyntaxError at import time. If a module is never imported
by any test, its syntax errors remain undetected. This test walks the entire
repository and compiles every .py file to catch such issues early.
"""
import os
from pathlib import Path

import pytest

# Directories to exclude from scanning
_EXCLUDE_DIRS = {
    ".venv",
    "__pycache__",
    ".git",
    "build",
    "dist",
    "*.egg-info",
}


def _get_all_py_files() -> list[Path]:
    """Return all .py files in the repo, excluding virtual envs and build dirs."""
    root = Path(__file__).resolve().parent.parent
    result: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune excluded directories in-place so os.walk won't descend
        dirnames[:] = [
            d for d in dirnames
            if d not in _EXCLUDE_DIRS and not d.endswith(".egg-info")
        ]
        for fn in filenames:
            if fn.endswith(".py"):
                result.append(Path(dirpath) / fn)
    return result


@pytest.mark.unit
def test_no_syntax_errors_in_source():
    """Every .py file in the repo must compile without SyntaxError.

    Catches indentation errors (dedented lines, mixed tabs/spaces on the
    same logical block level) as well as structural problems like missing
    except/finally blocks.  These errors would otherwise be invisible to
    the test suite because Python only raises SyntaxError at import time,
    and many modules are never imported by any existing test.
    """
    files = _get_all_py_files()
    assert files, "No Python files found in the repository"

    errors: list[str] = []
    for f in files:
        try:
            source = f.read_text(encoding="utf-8", errors="replace")
            compile(source, str(f), "exec")
        except SyntaxError as exc:
            rel = f.relative_to(Path(__file__).resolve().parent.parent)
            errors.append(
                f"{rel}:{exc.lineno}: {exc.msg} (offset {exc.offset})"
            )

    if errors:
        pytest.fail(
            f"Syntax/indentation error(s) in {len(errors)} file(s):\n"
            + "\n".join(errors)
        )