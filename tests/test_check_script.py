from __future__ import annotations

import unittest
from types import SimpleNamespace

from scripts.check import CheckCommand, run_checks


class CheckRunnerTests(unittest.TestCase):
    def test_run_checks_continues_after_failure_and_returns_nonzero(self) -> None:
        commands: list[list[str]] = []
        results = iter((1, 0, 0))

        def fake_runner(command: list[str], check: bool) -> SimpleNamespace:
            self.assertFalse(check)
            commands.append(command)
            return SimpleNamespace(returncode=next(results))

        checks: tuple[CheckCommand, ...] = (
            ("first", ("first",)),
            ("second", ("second",)),
            ("third", ("third",)),
        )

        result = run_checks(checks, runner=fake_runner)

        self.assertEqual(1, result)
        self.assertEqual([["first"], ["second"], ["third"]], commands)

    def test_run_checks_returns_zero_when_all_stages_pass(self) -> None:
        calls = 0

        def fake_runner(command: list[str], check: bool) -> SimpleNamespace:
            nonlocal calls
            calls += 1
            return SimpleNamespace(returncode=0)

        checks: tuple[CheckCommand, ...] = (("only", ("only",)),)

        result = run_checks(checks, runner=fake_runner)

        self.assertEqual(0, result)
        self.assertEqual(1, calls)


if __name__ == "__main__":
    unittest.main()
