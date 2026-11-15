# CognitiveBrain model routing and platform bridge

## Runtime contract

CognitiveBrain uses an OpenAI-compatible inference API; the model itself is supplied by a connected provider. The preferred order is controlled by `COGNITIVE_PROVIDER_ORDER` (default: `lmstudio,pocketpal,gemini`). The provider adapter tries each configured endpoint and falls through on connection or inference errors. `/api/providers` performs a read-only endpoint probe and never returns credentials.

### Environment variables

- `LMSTUDIO_BASE_URL`: base URL ending in `/v1`, normally `http://127.0.0.1:1234/v1` only when the Python server and LM Studio run on the same host.
- `LMSTUDIO_API_KEY`: optional key for LM Studio or the bridge.
- `LMSTUDIO_MODEL`: optional model ID. Otherwise the first model returned by `GET /models` is selected.
- `POCKETPAL_BASE_URL`: PocketPal's OpenAI-compatible API base URL, ending in `/v1`; leave empty until PocketPal's Local API Server is enabled.
- `POCKETPAL_API_KEY`: optional key if configured in PocketPal.
- `POCKETPAL_MODEL`: optional model ID.
- `COGNITIVE_MODEL`: optional model ID override shared by configured providers.
- `COGNITIVE_PROVIDER_ORDER`: comma-separated order, for example `lmstudio,pocketpal,gemini`.
- `COGNITIVE_MODEL_TIMEOUT_SECONDS`: inference timeout.
- `VEROX_STATE_DB`: local SQLite state path.

Do not commit credentials to GitHub. Set Supabase and provider secrets in Render's Environment settings. A Supabase service-role key must never be placed in HTML, browser JavaScript, a public repository, or an Android app.

## Important network boundary

A Render service cannot access the `localhost` of a Windows PC or Android phone. A phone's private LAN address is also not reachable from Render by default. The model must be on the same host, reachable through a private VPN, or connected through an authenticated tunnel/bridge.

`scripts/model_bridge.py` is a small authenticated proxy for only `GET /v1/models` and `POST /v1/chat/completions`. It binds to loopback by default, refuses to start without `MODEL_BRIDGE_API_KEY`, rejects other routes, limits request size, and never executes model-generated tool calls. If a tunnel is used, route only to this loopback port and keep the bearer token secret. Set the Render provider URL to the tunnel's HTTPS base URL ending in `/v1`, and set its API key to the same bridge token. Do not expose the model API unauthenticated to the public internet.

For PocketPal on Android, enable its Local API Server and confirm the app's documented URL/port in its own UI. If the phone and Windows bridge are on the same trusted Wi-Fi, set `MODEL_UPSTREAM_BASE_URL` on the bridge to the phone's reachable URL. Avoid opening the phone's API port to the internet. The dashboard is responsive on narrow screens; responsive UI alone does not create network connectivity.

## Supabase and persistence

If `SUPABASE_URL` or `SUPABASE_SERVICE_ROLE_KEY` is missing, the HTTP server no longer needs to pretend the model is unavailable: direct chat can use a reachable model provider and writes messages to local SQLite. Supabase-backed durable tasks and remote memory remain disabled until those variables are configured. SQLite on an ephemeral cloud filesystem is not durable across redeploys/restarts; for durable operation, configure Supabase or mount a persistent disk and point `VEROX_STATE_DB` at it.

Set `SUPABASE_URL` to the project's URL and `SUPABASE_SERVICE_ROLE_KEY` only in Render's secret environment settings. The repository intentionally contains no secret values. Adding placeholders to `render.yaml` declares the variables but does not fill them with credentials.

## Heretic

[Heretic](https://github.com/p-e-w/heretic) is a separate model-weight modification tool; it is not a runtime switch, an MCP connector, or a permission system. If used, run it offline on a compatible workstation, inspect its license and the base model's license, evaluate the output, then load the resulting model in LM Studio or another supported model host. Set `LMSTUDIO_MODEL` or `POCKETPAL_MODEL` to the model ID actually served by that host. CognitiveBrain cannot truthfully claim Heretic is active merely because the repository mentions it.

Heretic-modified weights do not grant the model execution rights. The provider's system prompt and the execution gateway keep CognitiveBrain in read-only mode: model output is treated as untrusted text, never as executable code or authorization. Tool access, file writes, installation, deletion, transactions and publication remain separate capabilities and are not enabled by changing model weights.

## Read-only reverse-engineering tools

`integrations/readonly_tools.json` records tool names and intended read-only use. `scripts/probe_readonly_tools.py` checks whether executables are present on the host where it runs. A tool is not considered connected or installed just because it appears in this registry. Actual connection requires a local agent/adapter, a tested executable path, and an allowlisted operation. Do not run unknown binaries or execute samples during inventory.
