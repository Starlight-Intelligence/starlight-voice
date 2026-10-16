import pytest


@pytest.fixture(autouse=True)
def _hold_infisical(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit tests must not call Infisical or read the machine key."""

    monkeypatch.setenv("STARLIGHT_XVOICE_INFISICAL", "0")
