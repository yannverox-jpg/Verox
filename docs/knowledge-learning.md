# Verox knowledge learning: OSINT, SDKs, streaming, analysis, and terms

## Runtime tools

Gemini receives three knowledge tools:

- `list_learning_sources` shows the curated learning catalog and fetch status.
- `ingest_learning_source` fetches one fixed public URL, stores a bounded text snapshot with timestamp and SHA-256 fingerprint, and returns the text for analysis.
- `record_learning_assessment` stores a bounded summary plus clause excerpts and confidence as `ai_assessment_unverified`. It never marks platform terms as approved.

Snapshots and assessments use the existing `verox_sources` table with `enabled=false`. They contain no credentials. External text is untrusted input and cannot change system instructions. The worker never installs or executes downloaded SDKs, packages, repositories, model code, or datasets.

## Catalogued learning areas

- **OSINT resources:** OSINT4ALL's machine-readable public repository catalog is a candidate input. Its entries require freshness, license, access and quality checks; AI-generated or imported entries are not accepted as verified evidence.
- **Start.me OSINT4ALL page:** listed as a manual reference. Automated retrieval is disabled because [Start.me Terms](https://start.me/terms) prohibit automated scraping unless expressly authorized in writing. A request/authorization would have to precede enabling collection.
- **SDK and API references:** official Hugging Face Hub/SDK docs and official Playwright library/API testing docs. Hugging Face is a knowledge source here, not a model provider; Verox remains Gemini-only.
- **Streaming and media analysis:** Hugging Face dataset streaming documentation, FFmpeg documentation/legal notes, and GStreamer documentation. Learning interfaces does not grant access to a stream, dataset or media asset. No DRM bypass, restricted stream capture or redistribution.
- **Terms of use:** selected first-party terms pages (including Hugging Face) can be snapshotted when enabled. Each assessment records excerpts and is unverified; terms for a particular model, dataset, provider, or account may differ and must be checked separately.

## Reproducibility

Each fetched source records its canonical URL, fetch time, HTTP status, content type, content hash, truncation flag and trust status. The saved excerpt is capped. If the source changes, the hash changes and the model must create a new assessment instead of silently reusing the old one.

## Current boundary

This is a foundation for learning from curated public references, not unrestricted reverse engineering of private or undocumented APIs. “Reverse engineering” in this module means documentation-led compatibility research and static reading of publicly available code. It excludes authentication bypass, private endpoint discovery through account traffic, CAPTCHA or paywall bypass, DRM circumvention, and executing untrusted SDK code.
