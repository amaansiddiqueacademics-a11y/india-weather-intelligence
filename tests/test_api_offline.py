"""API behaviour before the warehouse exists (fresh install, server down). No database needed."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from wx import api, config


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(config, "DSN", "host=127.0.0.1 port=1 dbname=wx user=postgres connect_timeout=2")  # nothing listens on port 1
    monkeypatch.setattr(api, "_cache", {})  # answers cached by earlier tests would hide the outage
    return TestClient(api.app)


@pytest.mark.parametrize("path", ["/api/health", "/api/overview", "/api/history?city=BOM"])
def test_missing_warehouse_gives_503_with_instructions(offline, path):
    r = offline.get(path)
    assert r.status_code == 503
    assert r.json()["error"] == "warehouse_unavailable" and "python -m wx.pipeline" in r.json()["detail"]


def test_dashboard_still_loads_without_the_warehouse(offline):
    r = offline.get("/")
    assert r.status_code == 200 and "<html" in r.text.lower()
