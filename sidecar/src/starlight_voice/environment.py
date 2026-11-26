from __future__ import annotations

import importlib.util
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class ToolStatus:
    name: str
    present: bool
    path: str | None
    version: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "present": self.present,
            "path": self.path,
            "version": self.version,
        }


@dataclass(frozen=True)
class PackageStatus:
    name: str
    import_name: str
    present: bool
    purpose: str

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "import_name": self.import_name,
            "present": self.present,
            "purpose": self.purpose,
        }


class EnvironmentDoctor:
    CLI_TOOLS = {
        "python": ["--version"],
        "uv": ["--version"],
        "cargo": ["--version"],
        "node": ["--version"],
        "npm": ["--version"],
        "pwsh": ["--version"],
        "claude": ["--version"],
        "codex": ["--version"],
        "opencode": ["--version"],
        "gemini": ["--version"],
        "arco": ["--version"],
        "cloudcli": ["--version"],
        "grok": ["--version"],
        "dcode": ["--version"],
    }

    OPTIONAL_PACKAGES = [
        PackageStatus("pipecat-ai", "pipecat", False, "Realtime voice and multimodal pipeline."),
        PackageStatus("browser-use", "browser_use", False, "Browser automation agent runtime."),
        PackageStatus("anthropic", "anthropic", False, "Direct Claude SDK path."),
        PackageStatus("openai", "openai", False, "OpenAI/Cerebras-compatible SDK path."),
        PackageStatus("pyaudio", "pyaudio", False, "Local microphone/speaker IO (PortAudio/WASAPI)."),
        PackageStatus("sounddevice", "sounddevice", False, "Alternate local audio IO."),
    ]

    SECRET_KEYS = [
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "ELEVENLABS_API_KEY",
        "ELEVENLABS_AGENT_ID",
        "DEEPGRAM_API_KEY",
        "CARTESIA_API_KEY",
        "GROQ_API_KEY",
        "CEREBRAS_API_KEY",
        "LITELLM_BASE_URL",
        "LITELLM_API_KEY",
        "INFISICAL_PROJECT_ID",
        "INFISICAL_ENV",
    ]

    def report(self) -> dict[str, object]:
        tools = [self._tool_status(name, args).to_dict() for name, args in self.CLI_TOOLS.items()]
        packages = [self._package_status(pkg).to_dict() for pkg in self.OPTIONAL_PACKAGES]
        missing_required = [tool["name"] for tool in tools if tool["name"] in {"python", "cargo", "pwsh"} and not tool["present"]]
        missing_agent_cli = [tool["name"] for tool in tools if tool["name"] in {"claude", "codex", "opencode", "gemini"} and not tool["present"]]
        return {
            "platform": {
                "system": platform.system(),
                "release": platform.release(),
                "python": sys.version.split()[0],
            },
            "tools": tools,
            "optional_packages": packages,
            "secrets": self._secret_status(),
            "readiness": {
                "build_shell": not missing_required,
                "text_sidecar": True,
                "agent_cli_lane": len(missing_agent_cli) < 4,
                "voice_loop": self._package_present("pipecat")
                and (self._package_present("pyaudio") or self._package_present("sounddevice")),
                "browser_live": self._package_present("browser_use"),
                "missing_required": missing_required,
                "missing_agent_cli": missing_agent_cli,
            },
        }

    def _tool_status(self, name: str, version_args: list[str]) -> ToolStatus:
        path = shutil.which(name)
        if path is None:
            return ToolStatus(name=name, present=False, path=None)

        version = None
        try:
            completed = subprocess.run(
                [name, *version_args],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            version = (completed.stdout or completed.stderr).strip().splitlines()[0]
        except Exception:
            version = None

        return ToolStatus(name=name, present=True, path=path, version=version)

    def _package_status(self, package: PackageStatus) -> PackageStatus:
        return PackageStatus(
            name=package.name,
            import_name=package.import_name,
            present=self._package_present(package.import_name),
            purpose=package.purpose,
        )

    @staticmethod
    def _package_present(import_name: str) -> bool:
        return importlib.util.find_spec(import_name) is not None

    def _secret_status(self) -> dict[str, bool]:
        return {key: bool(os.environ.get(key)) for key in self.SECRET_KEYS}
