"""The one-command runner (wx.pipeline). No database needed."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from wx import pipeline

README = Path(__file__).resolve().parents[1] / "README.md"


def test_full_plan_order():
    steps = pipeline.plan()
    assert steps[0] == "download" and steps[-1] == "warehouse"
    # EDA and the crop model read the materialised view, so warehouse must run before them; it runs again after ML
    assert steps.index("warehouse") < steps.index("eda") < steps.index("ml") < steps.index("ml_agri") < len(steps) - 1


def test_resume_and_skip_download():
    assert pipeline.plan("ml") == ["ml", "ml_agri", "warehouse"]
    assert "download" not in pipeline.plan(skip_download=True)
    assert pipeline.plan(skip_download=True)[0] == "etl_weather"


def test_unknown_step_is_rejected():
    with pytest.raises(SystemExit, match="unknown step"):
        pipeline.plan("nope")


def test_readme_lists_the_same_steps_in_the_same_order():
    readme_steps = re.findall(r"^python -m wx\.(\w+)", README.read_text(encoding="utf-8"), flags=re.M)
    assert [s for s in readme_steps if s != "pipeline"] == pipeline.STEPS
