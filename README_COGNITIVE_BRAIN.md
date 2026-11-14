# Verox CognitiveBrain Core

This package adds an independent, testable cognitive core without modifying the existing dashboard or its entry point.

## Run

```bash
python cognitive_brain_main.py
python -m unittest discover -s tests -v
```

The first run creates `data/cognitive_brain.sqlite3`. Set `COGNITIVE_BRAIN_DB` to use another path.

## Lifecycle and guarantees

- Runtime inventory records evidence for Python, selected modules, and executable availability.
- A cognitive cycle records an objective, observation provenance, a conservative source assessment, and explicit candidate hypotheses.
- SQLite persists events, experiments, and versioned tool source.
- Tool synthesis requires an injected code-generation provider. Without one, it records a blocked attempt instead of pretending code was generated.
- Static validation does not execute generated code and does not mark a capability available.
- A separate validation caller must supply measured evidence before `mark_available` can register a capability.

## Security boundary

AST checks are defensive filters, not a secure sandbox. Never run generated or untrusted source in the main application process. Before enabling runtime execution, configure an OS/container sandbox with restricted filesystem, network disabled by default, resource/time limits, and scoped credentials. Financial, destructive, or externally visible actions require separate authorization.

## Epistemic rules

- A proposed hypothesis is not an experimental result.
- A successful result does not by itself establish causality.
- Source type weights are heuristics, not proof of truth.
- Capabilities are not considered acquired until validation evidence is recorded.
- The initial hypothesis generator is a transparent baseline, not general intelligence or an autonomous LLM planner.

## Scope

The core is independent of trading, e-commerce, microtasks, mining, and UI code. Those connectors can be attached later through explicit capability contracts. The existing dashboard and server entry point are intentionally untouched.
