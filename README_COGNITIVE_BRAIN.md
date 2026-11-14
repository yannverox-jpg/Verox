# Verox CognitiveBrain Core

Independent, executable cognitive core for Verox. The package is intentionally separate from the existing dashboard and the existing server entry point; neither is modified by this implementation.

## Run locally

```bash
python cognitive_brain_main.py
python -m unittest discover -s tests -v
```

The first run creates `data/cognitive_brain.sqlite3`. Set `COGNITIVE_BRAIN_DB` to select another path. The core uses Python's standard library only.

## Implemented capabilities

- **Self-model:** inventories Python/runtime details, selected installed modules, executable availability, CPU count, disk capacity, and presence-only configuration flags without exposing secret values.
- **Objectives and constraints:** defines explicit success criteria, step/cost limits, and external-side-effect permissions.
- **Investigation:** fans out to explicitly registered sync or async evidence providers, records provenance, handles provider failures, and marks incomplete results. No providers are assumed or silently fabricated.
- **Source evaluation:** applies transparent source-type heuristics and labels uncorroborated evidence as unverified. Weights are not proof of truth.
- **Hypotheses and strategy selection:** proposes alternative approaches, incorporates lexical recall of related prior events, and ranks candidates by declared confidence and estimated cost. Ranking is heuristic, not proof.
- **Persistent memory:** SQLite stores events, experiment plans/results, and versioned tool source; simple lexical recall is explicit about its limitations.
- **Tool synthesis:** accepts an injected code-generation provider; validates a narrow pure-Python subset; stores versioned drafts and validation errors.
- **Tool testing:** executes eligible pure functions in a resource-limited child process with restricted built-ins, AST restrictions, a timeout, and JSON-only inputs/outputs. Passing explicit test cases produces measured evidence and can register a capability.
- **Tool registry:** retrieves versioned tool records and exposes only validated/available versions as available candidates.
- **Experiment lifecycle:** blocks plans outside objective constraints, records measured results with timestamp/method provenance, and does not claim that a successful experiment proves causality.
- **Continuous integration:** GitHub Actions runs unit tests and the standalone demonstration.

## Tool generation lifecycle

A tool is not available merely because source code exists. It must pass static validation, then all supplied test cases in the restricted runner. Source hash, test count, pass count, and timing metrics are stored. No code-generation provider is configured by default; in that state the core records a blocked synthesis attempt instead of pretending it generated code.

## Security and autonomy boundaries

The runner is defense in depth, not a hardened OS/container security boundary. Its syntax allowlist blocks imports, attribute access, and non-approved calls; subprocess time and memory limits are applied where supported. Do not use this runner for untrusted, high-risk, networked, or privileged workloads. Such workloads require a separately configured OS/container sandbox with network disabled by default, restricted filesystem, resource limits, and scoped credentials.

Optional code generation supports an OpenAI-compatible endpoint (including local compatible model servers). Set `COGNITIVE_BRAIN_LLM_BASE_URL` and `COGNITIVE_BRAIN_LLM_MODEL`; set `COGNITIVE_BRAIN_LLM_API_KEY` only when the endpoint requires it. No request occurs until tool synthesis is invoked. The provider is not enabled by default.\n\nThe core does not automatically install packages, access credentials, place trades, spend funds, or perform destructive/external actions. Those capabilities require explicit adapters and permissions. Tool discovery/design and external execution are separate concerns.

## Epistemic rules

- A hypothesis is a proposal, not an experimental result.
- A generated script is a draft, not an acquired capability.
- A successful result does not by itself establish causality.
- Source-type weights are heuristics, not proof of truth.
- Capability acquisition requires evidence tied to the exact source hash and successful tests.
- The baseline hypothesis generator is transparent and bounded; it is not presented as general intelligence or a fully autonomous LLM planner.

## Scope

CognitiveBrain's principles are independent of trading, e-commerce, microtasks, mining, and other future application connectors. Such domains can be added downstream through explicit capability contracts. The dashboard and existing application entry point remain untouched.
