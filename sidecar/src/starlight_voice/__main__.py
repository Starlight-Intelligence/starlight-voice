from __future__ import annotations

import argparse
import json
import os
import sys

from . import adapters
from .browser import BrowserAutomationAdapter
from .config import Settings, load_local_env
from .environment import EnvironmentDoctor
from .ipc import JsonLineIpcServer
from .pipeline import AgentPipeline


def main(argv: list[str] | None = None) -> int:
    load_local_env()

    parser = argparse.ArgumentParser(prog="starlight-voice-sidecar")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("health", help="Print sidecar health as JSON")
    sub.add_parser("doctor", help="Inspect local machine readiness as JSON")

    say = sub.add_parser("say", help="Run the text-mode cognition path")
    say.add_argument("text", nargs="+")

    browser = sub.add_parser("browser", help="Prepare a browser automation task")
    browser.add_argument("goal", nargs="+")
    browser.add_argument("--live", action="store_true", help="Run live browser automation if optional deps are installed")

    sub.add_parser("serve", help="Run JSON-lines IPC on stdin/stdout")
    voice = sub.add_parser("voice", help="Voice loop: readiness (default), --selftest (assemble graph), --run (live)")
    voice.add_argument("--selftest", action="store_true", help="Construct the cloud graph headlessly (no mic) and report")
    voice.add_argument("--run", action="store_true", help="Open mic/speakers and run the live loop (needs the voice extra)")
    voice.add_argument("--variant", choices=["component", "openai-realtime", "gemini-live", "x-voice"],
                       help="Bake-off lane: which architecture to selftest/run (default: component)")

    listen = sub.add_parser("listen", help="Double-clap opens xAI Grok Voice, routed through the meta harness")
    listen.add_argument("--check", action="store_true", help="Print readiness JSON and exit")
    listen.add_argument("--text", default="", help="One spoken text turn, then exit")

    disp = sub.add_parser("dispatch", help="Route a coding task to the fleet (dry-run packet preview)")
    disp.add_argument("task", nargs="+")
    disp.add_argument("--live", action="store_true", help="Actually spawn the chosen CLI for Tier-A tasks")

    brief = sub.add_parser("brief", help="Scan repos -> ranked morning brief JSON + spoken headline")
    brief.add_argument("paths", nargs="*", help="Repo paths to scan (default: STARLIGHT_BRIEF_REPOS or this repo)")
    brief.add_argument("--speak", action="store_true", help="Synthesize the spoken summary via OpenRouter")

    runs = sub.add_parser("runs", help="Recent dispatch run-ledger records (what the agents are doing)")
    runs.add_argument("--limit", type=int, default=20)

    args = parser.parse_args(argv)
    pipeline = AgentPipeline()

    if args.command == "health":
        print(json.dumps(pipeline.health(), separators=(",", ":")))
        return 0

    if args.command == "doctor":
        print(json.dumps(EnvironmentDoctor().report(), separators=(",", ":")))
        return 0

    if args.command == "say":
        result = pipeline.process_text(" ".join(args.text))
        print(json.dumps(result, separators=(",", ":")))
        return 0

    if args.command == "browser":
        adapter = BrowserAutomationAdapter(live=args.live)
        result = adapter.run(" ".join(args.goal))
        print(json.dumps(result.to_dict(), separators=(",", ":")))
        return 0 if result.ok else 2

    if args.command == "serve":
        server = JsonLineIpcServer(pipeline=AgentPipeline())
        return server.serve(sys.stdin, sys.stdout)

    if args.command == "dispatch":
        from .cognition.dispatch import Dispatcher

        outcome = Dispatcher(live=args.live).dispatch(" ".join(args.task))
        print(json.dumps(outcome, separators=(",", ":")))
        return 0

    if args.command == "runs":
        from .cognition.ledger import read_runs

        print(json.dumps(read_runs(limit=args.limit), separators=(",", ":")))
        return 0

    if args.command == "brief":
        from datetime import date as _date
        from pathlib import Path

        from .config import repo_root
        from .proactive.analyzer import build_brief, synthesize_spoken, write_brief

        if args.paths:
            paths = [Path(p) for p in args.paths]
        else:
            env_repos = os.environ.get("STARLIGHT_BRIEF_REPOS", "")
            paths = [Path(p) for p in env_repos.replace(";", ",").split(",") if p.strip()] or [repo_root()]
        brief = build_brief(paths, _date.today().isoformat())
        out = write_brief(brief, repo_root() / "memory" / "voice")
        spoken = synthesize_spoken(brief) if args.speak else brief.headline()
        print(json.dumps({"brief_file": str(out), "count": len(brief.items), "spoken": spoken}, separators=(",", ":")))
        return 0

    if args.command == "listen":
        from .x_voice import run_listen

        return run_listen(check=args.check, text=args.text)

    if args.command == "voice":
        if args.variant:  # bake-off lane (component / openai-realtime / gemini-live)
            from . import voice_engines

            if args.run:
                import asyncio

                return asyncio.run(voice_engines.run_variant(args.variant, Settings.from_env()))
            print(json.dumps(voice_engines.selftest_variant(args.variant, Settings.from_env()), separators=(",", ":")))
            return 0
        if args.run:
            import asyncio

            from .voice_loop import run as voice_run
            return asyncio.run(voice_run(Settings.from_env()))
        if args.selftest:
            from .voice_loop import selftest
            print(json.dumps(selftest(Settings.from_env()), separators=(",", ":")))
            return 0
        settings = Settings.from_env()
        availability = adapters.availability()
        selected = {
            "stt": settings.stt_engine,
            "llm": "openrouter",
            "tts": settings.tts_engine,
            "framework": "pipecat",
        }
        missing = [eng for eng in selected.values() if not availability.get(eng, False)]
        print(json.dumps({
            "settings": settings.to_dict(),
            "adapter_availability": availability,
            "selected_engines": selected,
            "voice_loop_ready": not missing,
            "missing": missing,
            "install_hint": adapters.INSTALL_HINT,
            "note": "Gated until P1 voice_loop.py is wired; run `python benchmarks/run.py --probe first-audio` for the SLA gate.",
        }, separators=(",", ":")))
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
