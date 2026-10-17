import pytest


@pytest.fixture(autouse=True)
def _isolate_side_effects(tmp_path, monkeypatch):
    """Keep dispatch and voice ledgers off the real repo files."""
    monkeypatch.setenv("STARLIGHT_RUNS_FILE", str(tmp_path / "runs.jsonl"))
    monkeypatch.setenv("STARLIGHT_VOICE_STATE_DIR", str(tmp_path / "voice-state"))


@pytest.fixture(autouse=True)
def _hold_infisical(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit tests must not call Infisical, xAI, or read the machine key."""

    monkeypatch.setenv("STARLIGHT_XVOICE_INFISICAL", "0")
    monkeypatch.setenv("XAI_API_KEY", "")
    monkeypatch.setenv("XAI_MANAGEMENT_KEY", "")
    monkeypatch.setenv("XAI_TEAM_ID", "")
