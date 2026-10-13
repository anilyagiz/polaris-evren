"""Run the Polaris E2E suite:  python scripts/e2e/run.py [--only a,b] [--list] [-v] [--json out.json]

Prints a PASS/FAIL table; exit code 0 only if every selected scenario passes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.e2e.harness import Stack  # noqa: E402
from scripts.e2e.scenarios import SCENARIOS, Result, run_scenario  # noqa: E402


def _print_table(results: list[Result]) -> None:
    width = max(len(r.name) for r in results) + 2
    print()
    print(f"{'SCENARIO'.ljust(width)}{'RESULT':<8}{'TIME':>7}  DETAIL")
    print("-" * (width + 70))
    for r in results:
        first = (r.failures[0].splitlines()[0] if r.failures else "")[:90]
        print(f"{r.name.ljust(width)}{'PASS' if r.ok else 'FAIL':<8}{r.seconds:>6.1f}s  {first}")
    passed = sum(r.ok for r in results)
    print("-" * (width + 70))
    print(f"{passed}/{len(results)} passed, {len(results) - passed} failed")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma separated scenario names")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every failure + notes")
    ap.add_argument("--json", help="write machine readable results here")
    args = ap.parse_args()

    if args.list:
        for name, (backend, doc, _fn) in SCENARIOS.items():
            print(f"{name:<34}[{backend}] {doc}")
        return 0

    names = list(SCENARIOS)
    if args.only:
        wanted = [n.strip() for n in args.only.split(",") if n.strip()]
        unknown = [n for n in wanted if n not in SCENARIOS]
        if unknown:
            print(f"unknown scenarios: {unknown}", file=sys.stderr)
            return 2
        names = wanted

    stack = Stack()
    results: list[Result] = []

    async def go() -> None:
        for name in names:
            print(f"-> {name} ...", flush=True)
            results.append(await run_scenario(stack, name))
            r = results[-1]
            print(f"   {'PASS' if r.ok else 'FAIL'} ({r.seconds:.1f}s)", flush=True)

    try:
        asyncio.run(go())
    finally:
        stack.stop()

    _print_table(results)
    failed = [r for r in results if not r.ok]
    if failed and (args.verbose or True):
        print("\nFAILURE DETAILS")
        for r in failed:
            print(f"\n[{r.name}]")
            for f in r.failures:
                print("  - " + f.replace("\n", "\n    "))
            if args.verbose:
                for n in r.notes:
                    print("  * " + n.replace("\n", "\n    "))
    if args.json:
        Path(args.json).write_text(
            json.dumps([r.__dict__ for r in results], ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
