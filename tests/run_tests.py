"""
Test runner script for TelemFFB.

This script provides a convenient way to run tests with various configurations.
"""
import sys
import subprocess
from pathlib import Path


def run_tests(args=None):
    """Run pytest with the given arguments."""
    if args is None:
        args = []
    
    cmd = [sys.executable, "-m", "pytest"] + args
    
    print(f"Running: {' '.join(cmd)}")
    print("-" * 80)
    
    result = subprocess.run(cmd, cwd=Path(__file__).parent.parent)
    return result.returncode


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Run TelemFFB tests",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Run all tests:
    python run_tests.py
  
  Run with coverage:
    python run_tests.py --coverage
  
  Run specific test file:
    python run_tests.py tests/test_steering_friction_effect.py
  
  Run tests matching pattern:
    python run_tests.py -k "friction"
  
  Run with verbose output:
    python run_tests.py -v
        """
    )
    
    parser.add_argument(
        "tests",
        nargs="*",
        help="Specific test files or directories to run"
    )
    
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose output"
    )
    
    parser.add_argument(
        "-k",
        metavar="EXPRESSION",
        help="Run tests matching the given expression"
    )
    
    parser.add_argument(
        "-m",
        metavar="MARKER",
        help="Run tests with the given marker (unit, msfs, xplane, etc.)"
    )
    
    parser.add_argument(
        "--coverage",
        action="store_true",
        help="Run with coverage report"
    )
    
    parser.add_argument(
        "--html",
        action="store_true",
        help="Generate HTML coverage report (implies --coverage)"
    )
    
    parser.add_argument(
        "--failed",
        action="store_true",
        help="Re-run only failed tests from last run"
    )
    
    parser.add_argument(
        "--pdb",
        action="store_true",
        help="Drop into debugger on failures"
    )
    
    args = parser.parse_args()
    
    # Build pytest arguments
    pytest_args = []
    
    if args.verbose:
        pytest_args.append("-v")
    
    if args.k:
        pytest_args.extend(["-k", args.k])
    
    if args.m:
        pytest_args.extend(["-m", args.m])
    
    if args.coverage or args.html:
        pytest_args.extend(["--cov=telemffb", "--cov-report=term"])
        if args.html:
            pytest_args.append("--cov-report=html")
    
    if args.failed:
        pytest_args.append("--lf")
    
    if args.pdb:
        pytest_args.append("--pdb")
    
    # Add test files/dirs
    if args.tests:
        pytest_args.extend(args.tests)
    
    # Run tests
    return_code = run_tests(pytest_args)
    
    if args.html and return_code == 0:
        print("\n" + "=" * 80)
        print("Coverage report generated: htmlcov/index.html")
        print("=" * 80)
    
    sys.exit(return_code)


if __name__ == "__main__":
    main()
