"""Run safe, local quality checks without starting application services."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
import shutil
import subprocess
import sys


CheckCommand = tuple[str, Sequence[str]]
Runner = Callable[..., subprocess.CompletedProcess[bytes]]
ROOT = Path(__file__).resolve().parents[1]


def _python(*arguments: str) -> tuple[str, ...]:
    return (sys.executable, *arguments)


def _npm_command(script: str) -> tuple[str, ...]:
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if npm is None:
        return _python(
            "-c",
            (
                "raise SystemExit("
                "'npm was not found on PATH; install Node.js before running the frontend check'"
                ")"
            ),
        )
    return (npm, "--prefix", str(ROOT / "frontend"), "run", script)


DEFAULT_CHECKS: tuple[CheckCommand, ...] = (
    ("dependencies", _python("-m", "pip", "check")),
    (
        "documentation tests",
        _python("-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"),
    ),
    ("backend syntax", _python("-m", "compileall", "-q", "backend")),
    ("frontend lint", _npm_command("lint")),
    ("frontend build", _npm_command("build")),
    ("git diff check", ("git", "diff", "--check")),
)


def run_checks(
    checks: Sequence[CheckCommand],
    *,
    runner: Runner = subprocess.run,
) -> int:
    """Run every check and return a failing exit code if any stage fails."""
    failures: list[str] = []

    print(
        "backend tests: SKIPPED (an isolated test database is not configured; "
        "see docs/known-risks.md)",
        flush=True,
    )

    for name, command in checks:
        print(f"\n== {name} ==", flush=True)
        result = runner(list(command), check=False)
        if result.returncode == 0:
            print(f"{name}: PASS", flush=True)
        else:
            failures.append(name)
            print(f"{name}: FAIL (exit code {result.returncode})", flush=True)

    if failures:
        print(f"\nFailed checks: {', '.join(failures)}", flush=True)
        return 1

    print("\nAll safe checks passed. Backend Django tests were skipped.", flush=True)
    return 0


def main() -> int:
    """Run the project's safe local verification suite."""
    return run_checks(DEFAULT_CHECKS)


if __name__ == "__main__":
    raise SystemExit(main())
