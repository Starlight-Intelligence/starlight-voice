"""system_status() — the live operator state the cockpit renders.

Deliberately LIGHT: reads settings, adapter availability, bake-off variant metadata, recent
dispatch runs, and gateway liveness — WITHOUT constructing services (no network, sub-ms), so a
status poll never stalls or costs a provider call.
"""

from __future__ import annotations

import os


def system_status() -> dict:
    from . import adapters
    from .cognition.ledger import read_runs
    from .config import Settings
    from .memory import discover_gateway
    from .session_store import read_live, read_sessions
    from .voice_engines import VARIANTS

    settings = Settings.from_env()
    return {
        "settings": settings.to_dict(),
        "adapters": adapters.availability(),
        "variants": [
            {
                "key": v.key,
                "label": v.label,
                "engine": v.engine,
                "key_live": bool(os.environ.get(v.key_required)),
                "latency_hypothesis": v.latency_hypothesis,
            }
            for v in VARIANTS.values()
        ],
        "first_audio_budget_ms": settings.first_audio_p50_budget_ms,
        "gateway": "running" if discover_gateway() else "not running",
        "runs": read_runs(8),
        "live": read_live(),
        "sessions": read_sessions(8),
    }
