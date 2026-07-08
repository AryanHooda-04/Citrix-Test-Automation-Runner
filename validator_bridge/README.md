# Citrix Validator Bridge

Small hosted validation service for Citrix Test Automation Runner.

The desktop runner can call this bridge instead of calling `api.openai.com` directly. This avoids the HCL laptop TLS reset issue and keeps the OpenAI API key only on the hosted backend.

## How It Works

1. Runner captures the screenshot locally.
2. OCR runs first inside the runner.
3. If OCR fails and `ai_validation.enabled` is `true`, the runner sends the screenshot to this bridge.
4. Bridge calls OpenAI Vision using the backend `OPENAI_API_KEY`.
5. Bridge returns only validation fields such as `valid`, `reason`, `version`, `cmd_hostname`, and `fields`.

The bridge does not write screenshots to disk.

## Local Run

```powershell
cd validator_bridge
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
$env:OPENAI_API_KEY="sk-..."
$env:VALIDATOR_TOKEN="long-random-token"
uvicorn app:app --host 0.0.0.0 --port 8080
```

Health check:

```powershell
curl.exe http://localhost:8080/health
```

Full OpenAI backend test:

```powershell
curl.exe -H "Authorization: Bearer long-random-token" http://localhost:8080/test-openai
```

Expected result:

```json
{
  "ok": true,
  "service": "citrix-validator-bridge",
  "openai_reachable": true
}
```

## Runner Config

Update `config/config.json`:

```json
"ai_validation": {
  "enabled": true,
  "mode": "bridge",
  "bridge_url": "https://your-validator-host.example.com",
  "bridge_token": "",
  "bridge_token_env_var": "CITRIX_VALIDATOR_TOKEN",
  "bridge_test_path": "/test-openai"
}
```

Recommended colleague setup:

```powershell
setx CITRIX_VALIDATOR_TOKEN "long-random-token"
```

Then restart the runner.

## Deployment Notes

- Prefer an internal VM or approved internal app hosting if possible.
- If using a public host, keep `VALIDATOR_TOKEN` enabled and rotate it periodically.
- Store `OPENAI_API_KEY` only as a backend environment variable.
- Do not commit real keys or tokens.
- If HCL blocks the public host too, raise an access request for only the bridge domain instead of every colleague requesting `api.openai.com`.

Docker build/run example:

```powershell
docker build -t citrix-validator-bridge .
docker run -p 8080:8080 `
  -e OPENAI_API_KEY="sk-..." `
  -e VALIDATOR_TOKEN="long-random-token" `
  citrix-validator-bridge
```

The runner should use the final reachable HTTPS URL for this service, not `localhost`, unless the bridge is running on the same machine.

## Environment Variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `OPENAI_API_KEY` | Yes | Backend-only OpenAI key |
| `VALIDATOR_TOKEN` | Recommended | Shared bearer token required by `/validate` |
| `OPENAI_MODEL` | No | Defaults to `gpt-4.1-mini` |
| `OPENAI_ENDPOINT` | No | Defaults to Responses API |
| `OPENAI_TIMEOUT_SEC` | No | Defaults to `90` |
| `OPENAI_RETRY_ATTEMPTS` | No | Defaults to `3` |
| `MAX_IMAGE_BYTES` | No | Defaults to `8388608` |
