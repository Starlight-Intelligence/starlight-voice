"""Exercise failed shell setup without credentials, network or an app."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "load-openrouter-voice.ps1"


@pytest.mark.parametrize("failure", ["retrieval", "invalid_project", "untrusted_domain"])
def test_failed_setup_replaces_stale_key_and_prevents_shared_fallback(failure):
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("PowerShell is required to exercise the shell setup helper")
    # The Infisical stub returns failure before the fixed provider request can run.
    # All credentials below are synthetic sentinels; no credential store is read.
    code = r"""
        $env:STARLIGHT_VOICE_OPENROUTER_API_KEY = 'stale-synthetic-private-value'
        $env:OPENROUTER_API_KEY = 'shared-synthetic-private-value'
        $env:STARLIGHT_VOICE_PROFILE = 'legacy-component'
        $script:retrieved = $false
        function infisical { $script:retrieved = $true; $global:LASTEXITCODE = 1; 'synthetic-provider-private-error' }
        try {
            . $env:SETUP_TEST_SCRIPT -ProjectId $env:SETUP_TEST_PROJECT -InfisicalDomain $env:SETUP_TEST_DOMAIN
            throw 'Unexpected setup success'
        } catch {
            [ordered]@{
                message = $_.Exception.Message
                dedicated = $env:STARLIGHT_VOICE_OPENROUTER_API_KEY
                shared = $env:OPENROUTER_API_KEY
                profile = $env:STARLIGHT_VOICE_PROFILE
                retrieved = $script:retrieved
            } | ConvertTo-Json -Compress
        }
    """
    import os

    environment = dict(os.environ, SETUP_TEST_SCRIPT=str(SCRIPT),
                       SETUP_TEST_PROJECT="11111111-1111-1111-1111-111111111111",
                       SETUP_TEST_DOMAIN="https://eu.infisical.com/api")
    if failure == "invalid_project":
        environment["SETUP_TEST_PROJECT"] = ""
    elif failure == "untrusted_domain":
        environment["SETUP_TEST_DOMAIN"] = "https://untrusted.invalid/api"
    result = subprocess.run([pwsh, "-NoProfile", "-NonInteractive", "-Command", code],
                            env=environment, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["message"] == (
        "Voice credential setup failed. Check Infisical access, key expiry and normal inference role."
    )
    assert payload["dedicated"] == "*not found*"
    assert payload["shared"] == "shared-synthetic-private-value"
    assert payload["profile"] == "grok-openrouter"
    assert payload["retrieved"] is (failure == "retrieval")
    assert "stale-synthetic-private-value" not in result.stdout + result.stderr
    assert "synthetic-provider-private-error" not in result.stdout + result.stderr
