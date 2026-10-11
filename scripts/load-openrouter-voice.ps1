# Dot-source to configure the current shell. This never starts an app or requests inference.
param(
  [string] $ProjectId = '5578966b-0c74-4253-9a3c-bace21d97b24',
  [ValidateSet('dev', 'staging', 'prod')] [string] $Environment = 'dev'
)

$voiceKey = $null
$voiceClient = $null
$voiceHandler = $null
$voiceResponse = $null
$voiceRequest = $null
try {
  $voiceOutput = & infisical secrets get STARLIGHT_VOICE_OPENROUTER_API_KEY `
    "--projectId=$ProjectId" "--env=$Environment" --path=/ `
    --domain=https://eu.infisical.com/api --plain --silent `
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
