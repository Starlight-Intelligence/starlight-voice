import pytest


@pytest.fixture(autouse=True)
def _isolate_side_effects(tmp_path, monkeypatch):
    """Keep tests hermetic: ledger writes off the real repo file, and NO test may make a
    live provider call or hit a running memory gateway just because Frank's shell exports
    a key. Tests that exercise the LLM/memory paths inject a stub or set these explicitly."""
    monkeypatch.setenv("STARLIGHT_RUNS_FILE", str(tmp_path / "runs.jsonl"))
    for var in ("STARLIGHT_GATEWAY_JSON", "STARLIGHT_SIS_ROOT"):
        monkeypatch.delenv(var, raising=False)
