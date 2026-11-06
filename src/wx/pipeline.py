"""Run the whole pipeline, in order, with one command.

    python -m wx.pipeline                  # everything, from downloading the data to the evaluated models
    python -m wx.pipeline --from ml        # resume at a step after fixing a failure
    python -m wx.pipeline --skip-download  # the raw files are already in data/raw

Each step runs as its own `python -m wx.<step>` process (the same commands the README lists), so a
failure stops the run with that step's own error, and memory is released between the heavy steps.
`warehouse` runs twice on purpose: first to build the snowflake schema and materialised view that
EDA and the crop model read, then again so the snowflake also receives the model predictions.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

STEPS = ["download", "etl_weather", "etl_agri", "warehouse", "eda", "ml", "ml_agri", "warehouse"]
SRC = Path(__file__).resolve().parents[1]


def plan(start: str | None = None, skip_download: bool = False) -> list[str]:
    """Steps to run: all of them, or from the first occurrence of `start` onwards."""
    if start is not None and start not in STEPS:
        raise SystemExit(f"unknown step {start!r}; choose one of: {', '.join(dict.fromkeys(STEPS))}")
    steps = STEPS[STEPS.index(start):] if start else list(STEPS)
    return [s for s in steps if not (skip_download and s == "download")]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m wx.pipeline", description=__doc__.split("\n\n")[0])
    ap.add_argument("--from", dest="start", metavar="STEP", help=f"start at this step ({', '.join(dict.fromkeys(STEPS))})")
    ap.add_argument("--skip-download", action="store_true", help="do not contact Open-Meteo; use the files already in data/raw")
    args = ap.parse_args(argv)
    steps = plan(args.start, args.skip_download)
    t_all = time.time()
    for i, step in enumerate(steps, 1):
        print(f"[{i}/{len(steps)}] python -m wx.{step}", flush=True)
        t = time.time()
        rc = subprocess.run([sys.executable, "-m", f"wx.{step}"], cwd=SRC).returncode
        if rc != 0:
            print(f"\nStep '{step}' failed (exit code {rc}). Fix the error above, then resume with:\n"
                  f"    python -m wx.pipeline --from {step}", file=sys.stderr)
            return rc
        print(f"      done in {time.time() - t:.0f} s", flush=True)
    print(f"Pipeline complete in {(time.time() - t_all) / 60:.1f} min. Start the dashboard with:\n"
          "    python -m uvicorn wx.api:app --port 8077   (then open http://localhost:8077)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
