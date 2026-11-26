"""Browser-use seam — now with a default-DENY policy gate and a real read-only live path.

The repo must stay useful on a fresh laptop, so live execution is gated on optional deps.
Autonomy is expanded WITHOUT expanding blast radius: a goal earns the read-only "run live"
class only on a positive read signal with no mutation marker (mirrors the default-DENY doctrine
in cognition/dispatch.approval_tier). Anything that could change state on a site — login, submit,
buy, post, download — is Tier D (always-ask) and is hard-held, never auto-run. Unknown intent is
also held. The live read-only path drives Playwright headless to navigate + extract text.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from importlib.util import find_spec
from time import perf_counter

# Verbs that signal a read-only goal (Tier A — may run live).
_READ_LEAD = (
    "read", "open", "go to", "navigate", "visit", "search", "find", "look up", "look at",
    "check", "summarize", "summarise", "extract", "get", "show", "what", "fetch", "browse",
    "scan", "tell me",
)
# Verbs that change state on a site (Tier D — always-ask, hard-held). Conservative: a match
# here always wins, so "open X and buy Y" is held.
_MUTATE = (
    "login", "log in", "sign in", "sign up", "register", "submit", "post", "buy", "purchase",
    "checkout", "pay", "order", "book", "reserve", "delete", "remove", "upload", "download",
    "fill", "apply", "subscribe", "send", "tweet", "comment", "reply", "transfer", "confirm",
    "add to cart", "sign out",
)
_URL_RE = re.compile(r"https?://\S+")
_DOMAIN_RE = re.compile(r"\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}\b", re.I)


def classify_browser_goal(goal: str) -> tuple[str, str]:
    """Return (tier, reason). 'A' = read-only, may run live. 'D' = always-ask, hard-held."""
    t = " ".join(goal.lower().split())
    if any(m in t for m in _MUTATE):
        return "D", "could change state on a site (login/submit/buy/post/etc.) — always-ask, held."
    padded = f" {t} "
    if any(t == v or t.startswith(v + " ") or f" {v} " in padded for v in _READ_LEAD):
        return "A", "read-only navigation/extraction."
    return "D", "intent unclear — held (default-deny)."


def _allowlist() -> list[str]:
    raw = os.environ.get("STARLIGHT_BROWSER_ALLOWLIST", "")
    return [d.strip().lower() for d in raw.replace(";", ",").split(",") if d.strip()]


def _target_url(goal: str) -> str | None:
    m = _URL_RE.search(goal)
    if m:
        return m.group(0).rstrip(").,")
    d = _DOMAIN_RE.search(goal)
    return "https://" + d.group(0) if d else None


def _host_of(url: str) -> str:
    return re.sub(r"^https?://", "", url).split("/")[0].lower()


def _domain_allowed(url: str, allow: list[str]) -> bool:
    if not allow:
        return True  # no allowlist configured → read-only navigation is permitted
    host = _host_of(url)
    return any(host == a or host.endswith("." + a) for a in allow)


def _fetch(url: str, *, timeout_ms: int = 15000) -> tuple[str, str]:
    """Headless read-only navigate + text extract via Playwright. Caller guards find_spec."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            return page.title(), page.inner_text("body")
        finally:
            browser.close()


@dataclass(frozen=True)
class BrowserResult:
    ok: bool
    mode: str
    goal: str
    message: str
    elapsed_ms: int
    tier: str = ""
    url: str = ""
    extracted: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "mode": self.mode,
            "goal": self.goal,
            "message": self.message,
            "elapsed_ms": self.elapsed_ms,
            "tier": self.tier,
            "url": self.url,
            "extracted": self.extracted,
        }


class BrowserAutomationAdapter:
    """Dry-run by default; live mode runs ONLY read-only goals, gated by policy + deps."""

    def __init__(self, *, live: bool = False) -> None:
        self.live = live

    def run(self, goal: str) -> BrowserResult:
        started = perf_counter()
        clean = " ".join(goal.split())
        if not clean:
            return BrowserResult(False, "validation", goal, "Browser goal is empty.", 0)

        tier, reason = classify_browser_goal(clean)

        if not self.live:
            return BrowserResult(
                True,
                "dry-run",
                clean,
                f"Accepted (tier {tier}: {reason}). Pass --live to execute read-only goals.",
                self._elapsed(started),
                tier=tier,
            )

        # Live. Mutating / unclear goals are hard-held — they never auto-run.
        if tier == "D":
            return BrowserResult(False, "held", clean, f"Held — {reason}", self._elapsed(started), tier="D")

        url = _target_url(clean)
        if not url:
            return BrowserResult(
                False, "needs-target", clean,
                "Read-only is allowed, but I need a URL or domain to open.", self._elapsed(started), tier="A",
            )
        allow = _allowlist()
        if not _domain_allowed(url, allow):
            return BrowserResult(
                False, "blocked-domain", clean,
                f"{_host_of(url)} is not in STARLIGHT_BROWSER_ALLOWLIST.", self._elapsed(started), tier="A", url=url,
            )
        if find_spec("playwright") is None:
            return BrowserResult(
                False, "missing-dependency", clean,
                "playwright not installed. Run: uv pip install -e .[browser] && playwright install chromium",
                self._elapsed(started), tier="A", url=url,
            )

        try:
            title, text = _fetch(url)
        except Exception as e:  # noqa: BLE001 - surface the failure, never crash the turn
            return BrowserResult(
                False, "error", clean,
                f"Live read failed ({type(e).__name__}): {e}. If first run, try `playwright install chromium`.",
                self._elapsed(started), tier="A", url=url,
            )
        snippet = " ".join(text.split())[:1200]
        return BrowserResult(
            True, "read", clean, f"Read {title or url}", self._elapsed(started),
            tier="A", url=url, extracted=snippet,
        )

    @staticmethod
    def _elapsed(started: float) -> int:
        return int((perf_counter() - started) * 1000)
