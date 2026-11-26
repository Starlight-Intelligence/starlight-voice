import starlight_voice.browser as browser_mod
from starlight_voice.browser import BrowserAutomationAdapter, classify_browser_goal


def test_browser_dry_run_accepts_goal() -> None:
    result = BrowserAutomationAdapter().run("open the Starlight docs")

    assert result.ok is True
    assert result.mode == "dry-run"
    assert "Starlight docs" in result.goal


def test_browser_rejects_empty_goal() -> None:
    result = BrowserAutomationAdapter().run("  ")

    assert result.ok is False
    assert result.mode == "validation"


def test_policy_default_deny() -> None:
    assert classify_browser_goal("read the pipecat docs")[0] == "A"
    assert classify_browser_goal("open https://example.com and summarize")[0] == "A"
    assert classify_browser_goal("log in and buy the course")[0] == "D"  # mutating -> held
    assert classify_browser_goal("open the docs and submit the form")[0] == "D"  # mutate wins
    assert classify_browser_goal("frobnicate the widgets")[0] == "D"  # unknown -> held


def test_live_mutating_goal_is_hard_held() -> None:
    r = BrowserAutomationAdapter(live=True).run("log into github and delete the repo")
    assert r.ok is False and r.mode == "held" and r.tier == "D"


def test_live_readonly_without_target_asks_for_url() -> None:
    r = BrowserAutomationAdapter(live=True).run("read the docs")  # no URL/domain
    assert r.ok is False and r.mode == "needs-target"


def test_live_readonly_blocked_by_allowlist(monkeypatch) -> None:
    monkeypatch.setenv("STARLIGHT_BROWSER_ALLOWLIST", "pipecat.ai,docs.starlight.org")
    r = BrowserAutomationAdapter(live=True).run("open https://evil.example.com")
    assert r.ok is False and r.mode == "blocked-domain"


def test_live_readonly_reads_when_allowed(monkeypatch) -> None:
    # exercise the live wiring deterministically — stub the fetch so no network is touched
    monkeypatch.setattr(browser_mod, "_fetch", lambda url, **kw: ("Pipecat Docs", "Pipecat is a framework."))
    r = BrowserAutomationAdapter(live=True).run("open https://pipecat.ai/docs and read it")
    assert r.ok is True and r.mode == "read" and r.tier == "A"
    assert "Pipecat is a framework." in r.extracted
