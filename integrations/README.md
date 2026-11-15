# Verox extension runtime

## Registry and dashboard

- `catalog.json` is the version-controlled extension registry and the source used by the sidebar.
- The dashboard displays labels only; it does not navigate users to third-party sites or store API secrets.
- `main.py` loads registered extension manifests into CognitiveBrain at startup.
- An entry in the registry is not proof that an external application is installed or connected. Only protocol-level checks can establish that.

## Execution modes

- `render_backend` / `server_backend`: server-side APIs or plugins that can run with the deployed backend.
- `remote_service`: a provider API that requires a real supported protocol and, where needed, authentication.
- `local_device_or_agent`, `local_gpu_machine`, `mobile_device`: software running outside Render. A secure local agent or reachable, authenticated endpoint is required; Render's localhost is not the user's computer.
- `reference_catalog`: documentation/catalogues, not executable applications.

## Current concrete connections

- Gemini fallback: server-side Google Gemini `generateContent` REST API using the Render secret `GEMINI_API_KEY`. Startup performs a short availability probe; ordinary tasks use Gemini only if native connectors produce no usable evidence. Gemini is an API fallback, not a downloaded local model.
- Hugging Face Hub discovery: public model metadata search through its API for queries about models, local LLMs, or Hugging Face. Public metadata lookup does not download model weights or guarantee free inference.
- Existing native CognitiveBrain connectors remain responsible for their supported academic, OSINT, and browser workflows.

## Local applications

LM Studio, PocketPal AI, Pinokio, Heretic, and GrapheneOS cannot be installed as functioning applications inside the Render web service simply by adding their names to Git. Their local/device-specific integrations require the relevant runtime and a secured local agent or supported endpoint. Heretic is an optional model-transformation CLI, not an inference model or universal connector.

## Revenue and measurements

CognitiveBrain prioritizes legal, low-cost, testable revenue opportunities, but it must not fabricate earnings. The dashboard's revenue metric stays `Non mesuré` until a trusted transaction or finance integration supplies verified amounts. Automated trades, bets, purchases, account creation, and public posting require explicit authorization and appropriate platform credentials.
