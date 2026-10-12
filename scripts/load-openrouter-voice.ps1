# Dot-source to configure the current shell. This never starts an app or requests inference.
param(
  [string] $ProjectId = '',
  [string] $Environment = 'dev',
  [string] $InfisicalDomain = 'https://eu.infisical.com/api'
)

# Keep an explicit invalid dedicated value on failure. Removing the variable
# would permit the runtime to fall back to an existing shared OpenRouter key.
$env:STARLIGHT_VOICE_OPENROUTER_API_KEY = '*not found*'
$env:STARLIGHT_VOICE_PROFILE = 'grok-openrouter'
$voiceKey = $null
$voiceClient = $null
$voiceHandler = $null
$voiceResponse = $null
$voiceRequest = $null
try {
  if ($ProjectId -notmatch '^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$' -or
      $Environment -notmatch '^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$' -or
      $InfisicalDomain -notin @('https://eu.infisical.com/api', 'https://app.infisical.com/api')) {
    throw 'Supply your Infisical project, environment and supported cloud domain'
  }
  $voiceOutput = & infisical secrets get STARLIGHT_VOICE_OPENROUTER_API_KEY `
    "--projectId=$ProjectId" "--env=$Environment" --path=/ `
    "--domain=$InfisicalDomain" --plain --silent `
    --expand=false --include-imports=false --telemetry=false 2>&1
  if ($LASTEXITCODE -ne 0) { throw 'Infisical retrieval failed' }
  $voiceKey = ($voiceOutput -join "`n").Trim()
  if ($voiceKey -notmatch '^sk-or-[A-Za-z0-9_-]+$') { throw 'Voice inference credential unavailable' }
  $voiceOutput = $null

  $voiceHandler = [System.Net.Http.HttpClientHandler]::new()
  $voiceHandler.AllowAutoRedirect = $false
  $voiceHandler.UseProxy = $false
  $voiceClient = [System.Net.Http.HttpClient]::new($voiceHandler)
  $voiceClient.Timeout = [TimeSpan]::FromSeconds(15)
  $voiceRequest = [System.Net.Http.HttpRequestMessage]::new(
    [System.Net.Http.HttpMethod]::Get, 'https://openrouter.ai/api/v1/key')
  $voiceRequest.Headers.Authorization = [System.Net.Http.Headers.AuthenticationHeaderValue]::new('Bearer', $voiceKey)
  $voiceResponse = $voiceClient.SendAsync($voiceRequest,
    [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
  if ([int]$voiceResponse.StatusCode -ne 200) { throw 'Voice key metadata authentication failed' }
  $null = $voiceResponse.Content.LoadIntoBufferAsync(65536).WaitAsync([TimeSpan]::FromSeconds(15)).GetAwaiter().GetResult()
  $voiceMetadata = ($voiceResponse.Content.ReadAsStringAsync().GetAwaiter().GetResult() | ConvertFrom-Json).data
  if ($voiceMetadata.is_management_key -or $voiceMetadata.is_provisioning_key) {
    throw 'Management credentials cannot enter the voice runtime'
  }
  $env:STARLIGHT_VOICE_OPENROUTER_API_KEY = $voiceKey
  $env:STARLIGHT_VOICE_PROFILE = 'grok-openrouter'
  Write-Output "Grok OpenRouter profile loaded for this shell; cap USD $($voiceMetadata.limit), expires $($voiceMetadata.expires_at). No app started."
} catch {
  # Exception/provider messages can contain request metadata. Emit only this fixed message.
  throw 'Voice credential setup failed. Check Infisical access, key expiry and normal inference role.'
} finally {
  if ($voiceResponse) { $voiceResponse.Dispose() }
  if ($voiceRequest) { $voiceRequest.Dispose() }
  if ($voiceClient) { $voiceClient.Dispose() }
  if ($voiceHandler) { $voiceHandler.Dispose() }
  $voiceKey = $null
  $voiceOutput = $null
}
