"""Brain-lane baseline for Harness Cup v2: the shipped gate must pass every case.

A challenger that changes routing or the gate is scored by this same corpus
plus the judge's holdout set.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from starlight_voice import approval
from starlight_voice.meta_harness import resolve_class

_CORPUS = json.loads(
    (Path(__file__).resolve().parents[2] / "contracts" / "voice-cases.v1.json").read_text(encoding="utf-8")
)


@pytest.fixture()
def estate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repos = tmp_path / "repos"
    for name in _CORPUS["repos"]:
        (repos / name / ".git").mkdir(parents=True)
    monkeypatch.setenv("STARLIGHT_REPOS_ROOT", str(repos))
    monkeypatch.setenv("STARLIGHT_QUEEN_DIR", str(tmp_path / "queen"))
    monkeypatch.setenv("STARLIGHT_VOICE_PENDING_DIR", str(tmp_path / "pending"))
    allow = tmp_path / "repos.allow"
    allow.write_text("\n".join(_CORPUS["allowlist"]) + "\n", encoding="utf-8")
    monkeypatch.setenv("STARLIGHT_VOICE_REPO_ALLOWLIST", str(allow))
    return repos


def outcome(case: dict) -> tuple[str, dict]:
    packet = {
        "ok": True,
        "refuse": False,
        "job": case["job"],
        "primary": case["primary"],
        "data_class": resolve_class(case["utterance"]) or "unknown",
        "utterance": case["utterance"],
    }
    result = approval.propose(packet)
    if result["proposal"]:
        return "propose", result
    if result["reasons"] == ["repo-not-named"]:
        return "ask-repo", result
    return "hold", result


@pytest.mark.parametrize("case", _CORPUS["cases"], ids=lambda c: c["id"])
def test_shipped_gate_meets_every_case(estate: Path, case: dict) -> None:
    got, result = outcome(case)
    assert got == case["expect"], case.get("why", "")
    if got == "propose":
        record = json.loads((approval.pending_dir() / f"{result['proposal']}.json").read_text(encoding="utf-8"))
        assert record["agent"] == case["agent"]
        assert Path(record["repo"]).name == case["repo"]
