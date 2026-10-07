# Changelog

All notable changes to ScholarNova are recorded here. The project follows semantic versioning.

## [Unreleased]

### 1.2.11 source — meaningful relevance and usable paper recommendations

- Preserve Chinese subjects and repair/forecasting tasks during rule translation; normalize bilingual terms and require domain evidence instead of allowing generic `data`/`model` matches to dominate. Apply relevance tiers before quality/diversity ranking.
- Separate relevance and composite sorting. Filter current results by actual abstract availability, show counts and missing-abstract guidance, and reset filters on every new search, including the same query. Match scores are heuristic, not probabilities.
- Add bounded, metadata-only seed recommendations from OpenAlex and Crossref: related research, observable abstract-structure cues and exact-name same-journal references. Show source status and sample-based term/brand counts; do not invent abstracts or journal-wide frequencies.
- Persist recommendation records with DOI identity reuse so the user can open and analyze them. Cancel obsolete frontend requests and prevent late paper details from replacing the current selection.
- Share DOI/Corpus-ID persistence with ordinary search, committing usable local IDs before publishing results; a duplicate from another source no longer invalidates a batch. Prepare source TLS contexts on the existing bounded worker pool, preserving certificate checks and proxy settings without blocking async timeouts.
- Bound optional journal enrichment by an eight-second total deadline, including queueing and TLS initialization. Cached metrics remain immediate, and failed enrichment preserves existing quality fields rather than blocking paper details.

See [validation and manual scenarios](docs/reports/v1.2.11-search-recommendations.zh-CN.md). No new model calls are required for these features; user profiles, Zotero and LLM-Twin are unchanged. Public installers remain those actually attached to a Release.

### 1.2.10 source — real workflow acceptance and responsiveness

- Exercise production business endpoints with authorized live GLM, Qwen and SenseNova calls using an isolated test library. Record 38 text-request attempts (34 completed, 4 HTTP 429), 50,573 reported tokens and two completed image generations; do not equate HTTP success with retrieval quality, complete paper coverage or billing.
- Resolve compound product-help follow-ups without treating them as literature searches. Restore the user's research topic for elliptical follow-up retrieval while retaining the current category and source boundaries; prior model answers never become evidence.
- Clarify that finding a search result does not automatically prepare assistant evidence. Preserve actual visual-reading failures and excerpt limits in paper detail instead of claiming that no figures were extracted or the entire paper was read.
- Load the ordinary HTTPX trust store once per model client on a bounded worker pool. Retain TLS validation, environment CA and proxy behavior; cancelled waits retain capacity until certificate work finishes and never construct orphaned clients.
- Keep online PDF parsing text-first: retain page text and captions without blocking on optional full-page native table detection. Disclose missing table structure, fix explicit extraction to use Table.extract(), and provide nonempty timeout feedback.

See [live results, before/after concurrency samples and remaining quality gaps](docs/reports/v1.2.10-live-business-acceptance.zh-CN.md). Offline regression: 1,087 backend + 163 frontend + 45 desktop + 18 packaging tests passed. These are source changes, not new installed Windows/macOS binaries; existing user data, profiles and LLM-Twin are unchanged.

### 1.2.9 source — search and analysis reliability

- Add automatic, fast and explicit AI planning modes. Short topics skip planning-model calls; AI planning uses one approximately 20-second attempt, no SDK retry or alternate model, and a deterministic fallback. Record actual planning decisions without treating missing usage as free.
- Keep repeated same-query searches and mode changes usable; restore a recent query into the actual search input. Safely render structured API failures in Search, Settings and task-model testing.
- Disclose selected sections, text limits, figure/table excerpts and supplied image-page counts in paper analysis. A full-text source does not mean the model received the entire paper.
- Release read-only database connections before knowledge analysis, recommendations, PDF downloads and route generation. Publish route results with a conditional update so deleted or edited routes cannot be overwritten by stale runs.
- Give each route-generation run independent image files. Validate image downloads even for capability checks; bound response sizes, reject HTTP-compressed bodies before decompression, and atomically save decoded PNG files without replacing prior images on failure. Explicit profiles never borrow another provider's credentials.
- Allow read-only integration probes to select academic sources and skip Zotero entirely.

See [validation and manual scenarios](docs/reports/v1.2.9-search-analysis-reliability.zh-CN.md). No new paid model calls or changes to installed-app data, model profiles or the separate LLM-Twin project.

### 1.2.8 source — usable research workflows

- Paginate both knowledge entries and research routes; search knowledge on the server instead of filtering only the first page. Load linked route sources by their IDs and report missing sources separately.
- Remove misleading category bulk deletion that only covered the loaded page; individual entry deletion remains available.
- Pass user goals into knowledge analysis and architecture review. Keep excerpt boundaries explicit and do not treat saved draft claims as paper evidence.
- Replace the obsolete structured-card save action with explicit full-analysis route saving, retaining source IDs and requirements. Route generation now reads the saved draft instead of silently discarding it.
- Add a per-conversation evidence category, filtering notes and linked PDF chunks before retrieval limits. Specified categories exclude unscoped Zotero results. Changing scope isolates previous model context while retaining readable history.
- Keep ambiguous saves marked as unconfirmed across navigation, fix the saved-route link, and prevent the initial search debounce from resetting a fast page change. Render structured validation failures as text instead of crashing the analysis/assistant page.
- Mark historical assistant citations as previous-turn references rather than reusing current-turn source numbers. Keep asynchronous conversation errors on their originating conversation.
- Test production search and knowledge components, full save/revisit flows, database feature updates, and category isolation with synthetic materials and isolated state.

See [panel purposes, validation and the manual research scenario](docs/reports/v1.2.8-research-workflow.zh-CN.md). No new paid model calls, user-data migration, Zotero setup or local-model loading in this round.

### 1.2.7 source — concurrency and task reliability

- Bound active searches and their waiting queue, enforce a search execution deadline, and reclaim child requests on cancellation/shutdown. Keep completed/failed results terminal.
- Add single-process admission for expensive AI endpoints, explicit 429/Retry-After responses before execution, search queue status, and local health capacity counters. This is not a distributed provider quota.
- Fix Redis initialization and share its connection pool; use bounded TTL/LRU memory fallback on failure. Close shared cache only at application shutdown.
- Run all application MuPDF operations on one bounded worker thread. Cancelled waiters do not prematurely release native work capacity. Close documents and clean temporary downloads on failure.
- Close task-scoped model clients on success as well as failure. Ignore untrusted forwarded headers when identifying rate-limit clients.
- Isolate embedding cache transactions, tolerate duplicate inserts and bounded lock contention without invalidating the user's session or discarding completed embedding usage.
- Commit material preparation before model waiting; serialize source/index changes per paper so an older analysis cannot restore stale features after a PDF replacement. Failed index rebuilds retain the saved PDF and report the incomplete state.
- Cap distinct child retrieval tasks per round and distinguish local capacity truncation from actual provider API calls.
- Guard conversation and paper-analysis writes by request identity; abort obsolete browser requests and preserve per-paper results only within the current search.
- Add offline admission benchmarks, file-backed database race tests, and an official-source comparison of 13 related products/projects. No paid API load test or multi-user capacity claim is made.

See [validation, limits and manual checks](docs/reports/v1.2.7-concurrency.zh-CN.md). Local-model loading remains paused; no LLM-Twin files or user credentials are changed.

## [1.2.6] - 2026-09-20

### Added

- Optional local Qwen text inference using an independently provisioned Python runtime and model copy. Only the Assistant task can select this provider; model weights and GPU dependencies are not bundled in the desktop installer.
- Desktop-only start, stop and status controls for the app-owned local model process. Stopping releases its resources without terminating another application's processes or changing the selected model.
- Authenticated loopback-only chat transport that bypasses environment proxies, refuses redirects, records real token usage and never silently falls back to a cloud model.
- Bounded local evidence and history, explicit token overflow errors, single-request concurrency and cooperative cancellation. Local text inference does not make external search or separately enabled cloud embeddings offline.

### Fixed

- Validate architecture-planner module shapes and label budgets before rendering; invalid plans use the existing explicitly labelled rule fallback without silently truncating modules.
- Remove default instructions to invent internal attention/reward blocks, formulas and feedback arrows for visual complexity. Preserve source-supported formulas alongside internal components.

### Documentation

- Add a plain-language Chinese project and learning handbook covering the actual desktop workflow, RAG, model routing, local Qwen, diagrams, failure handling and hands-on source-reading exercises.
- Add 48 offline diagram-planner regression cases; all 62 planner/pipeline cases pass. Offline tests do not establish live image quality; local-model acceptance is recorded separately.

## [1.2.5] - 2026-09-11

### Fixed

- Shorten the product guide supplied to the configured Assistant model. Bound its model stage to 60 seconds and 320 output tokens; these limits do not control provider queue time or guarantee end-to-end latency. Preserve the model selected in Settings.
- Add one-click retry for the latest failed product-help answer. Reuse the original question without including it twice in history, and replace that answer without rewriting older turns.
- Persist earlier attempts and their reported usage across retries; missing usage remains explicitly unknown. Keep retries user-triggered, prevent duplicate submissions, and retain the originating conversation when switching chats.
- Use direct ZIP-style extraction for the Windows portable executable instead of nested LZMA compression; trade a larger download for less cold-start decompression work.

See the [v1.2.5 validation report](docs/reports/v1.2.5-assistant-retry.zh-CN.md) for test scope, live calls and installation checks.

## [1.2.4] - 2026-09-09

### Fixed

- Give contextual Assistant help one model attempt with a 45-second response budget and a 500-output-token limit. Keep research questions on the evidence-required path.
- Treat the user as already on the Assistant page. Prompt the model to ask one progress-clarification question for short follow-ups or repetition feedback, then continue with step-specific guidance for recognized Chinese stage replies. Model-call status questions do not require progress clarification. A trailing question mark is only a basic output check, not complete semantic repetition detection.
- Handle contextual feedback without repeating the full product guide. Missing configuration, model failures and invalid guidance now return a short local status message; whitespace-normalized copies of the preceding assistant answer are rejected.
- Record model request attempts, received responses and provider usage reports separately. Expose the attempted provider/model, failure type and help details directly in the UI; absent usage is unknown, not proof of no call or no charge. An attempted request does not prove server receipt.
- Ignore input-method confirmation Enter events, guard duplicate sends, and prevent clearing or deleting the conversation while its request is pending. Keep waiting indicators and late replies with the originating conversation and preserve the latest-six-message history boundary.

The v1.2.4 Release is published with Windows x64, Intel Mac and Apple Silicon Mac assets, a portable Windows build, corresponding source and SHA256 checksums. Installer availability is determined by the published Release assets.

## [1.2.3] - 2026-09-09

### Fixed

- Recognize first-person usage questions and bounded help follow-ups in the current conversation; explicit research questions continue to require evidence.
- Generate product guidance with the configured Assistant model, the built-in guide and the latest four bounded history messages. Limit help to one attempt, 12 seconds and 800 output tokens; preserve measured usage on failures and fall back to the guide without configuration or valid model output.
- Distinguish AI guidance and built-in fallback in the UI, without misleading evidence/verification labels; retain conversation isolation.
- Explicitly show and focus the desktop window after loading and on a second launch, while keeping smoke tests hidden.
- Report the shared package version from both health endpoints.

See the [v1.2.3 validation and walkthrough](docs/reports/v1.2.3-contextual-help.zh-CN.md) for live-provider results and limitations. Binary availability is determined by published Release assets, not the source version alone.

## [1.2.2] - 2026-09-09

### Fixed

- Recognize common Chinese/English identity, capability and usage questions before evidence retrieval; return the built-in ScholarNova guide without requiring papers or a model call.
- Match the complete help request to keep conceptual questions (such as “what is an agent?”) and agents discussed in papers on the research path; prior research or help conversation history does not override the current intent.
- Cover product-guide rendering and persistence so guide answers do not show misleading missing-evidence or BM25 warnings.
- Derive the backend's API and startup version from the package version instead of duplicate literals.
- Verify local Zotero saves using an explicit Connector session and full collection path; distinguish metadata sync from unsupported PDF attachment import and warn on unconfirmed writes.
- Retry the configured institutional-library portal directly after a proxy connection failure, retaining URL and redirect safety checks.
- Include the actual research analysis in architecture extraction instead of a literal template placeholder.
- Persist roadmap image links and report interrupted/partial image generation instead of an unconditional success notification.
- Route both diagram planners through the shared model gateway, expose rule-based fallback, and omit formulas absent from the supplied knowledge material.
- Count architecture-extraction tokens in knowledge analysis; expose automatic-polish outcomes and usage instead of treating every saved item as a successful model response.
- Mock the secondary architecture call in offline tests to prevent accidental provider requests.

### Validation

- Run real configured GLM text/vision and SenseNova U1 workflows in an isolated research database, and real Zotero writes in a separate empty profile. See [the live API report](docs/reports/2026-09-08-live-api-validation.zh-CN.md) for evidence and limitations.
- Pass 440 backend and 43 frontend regression tests, 6 desktop security/proxy checks and 18 packaging checks. The 27 opt-in integration tests are excluded from the offline suite, not counted as passing.
- See [the assistant-routing validation report](docs/reports/v1.2.2-assistant-help.zh-CN.md) for scope and desktop verification. Generated images still require manual label and content review. v1.2.1 remains immutable; download availability is determined by actual Release assets.

## [1.2.1] - 2026-09-08

### Fixed

- Restore verifiable citations for every sentence in evidence-only fallback excerpts; expose citation rejection separately from a model outage.
- Limit assistant model routes and citation repair, while preserving provider-reported token usage.
- Ignore late search/detail/analysis responses after changing sessions or selections; keep query shortcuts in the current window, not persistent browser storage.
- Separate textual relevance from composite recommendation scores without changing ranking order.
- Fix cross-provider placeholder credential inheritance and accurately report Zotero's read-only detection versus explicit write requests.
- Fix the macOS minimum icon size and build Intel/Apple Silicon backends on matching native runners.
- Escape untrusted research-route text and image attributes in Word/PDF exports, and disable scripts in export documents.
- Handle interrupted desktop proxy streams without sending duplicate headers or leaving an error dialog on exit.

### Release and documentation

- Upgrade Electron to 44.2.0 and refresh frontend runtime dependencies.
- Add clean-profile packaged-app startup checks, desktop URL/path isolation tests, consistent version checks, and release checksums.
- Prevent missing bundled backends from silently using a developer Python environment; restrict external protocols and cross-origin desktop requests.
- Reorganize the bilingual README with one compact four-panel gallery and separate installation, API-key, and source-development guides.
- See [consumer readiness report](docs/reports/v1.2.1-consumer-readiness.zh-CN.md) for verified results and remaining release boundaries. Windows remains unsigned; macOS uses ad-hoc signing, not Developer ID or notarization.
- Add offline third-party notices and exact-version corresponding-source downloads for the AGPL desktop distribution; retain the project's own MIT license and original authorship.
- Explicitly install Electron 44's runtime before collecting notices; validate source versions and notice checksums before publishing all three native builds.

## [1.2.0] - 2026-09-07

The source tag existed, but its macOS release build failed. The entries below are the historical development log, not new v1.2.1 benchmark results.

### Added

- First-class SiliconFlow chat and embedding profiles with Qwen defaults, task capability hints, credential isolation, and real-probe support.
- Opt-in real task capability probes for text, structured JSON, vision, and image generation, with locally persisted latency and provider-reported Token counts.
- Refresh-safe capability-probe history that excludes API keys, model response bodies, and generated-image URLs.
- Primary/fallback routing for paper text analysis, knowledge polishing, research-direction analysis, route text generation, and recommendation planning.
- Provider, model, fallback-state, and Token metadata on knowledge-analysis and recommendation responses.
- Evidence-bounded deterministic knowledge and recommendation results when both configured text models are unavailable.
- Conservative task-aware capability hints for text, structured output, vision, image generation, and embeddings without a paid model call.
- Shared primary/fallback routing for AI query planning and academic translation, including translation route and Token metadata.
- Optional hybrid retrieval with an independent embedding profile, local vector cache, cosine ranking, and RRF fusion over BM25 results.
- Embedding connection testing for Ollama, OpenAI, Zhipu, Qwen, and custom OpenAI-compatible endpoints.
- Retrieval-mode and embedding-token observability in research-assistant responses and the UI.
- Zero-token answer citation-integrity checks for factual-segment coverage, invalid source IDs, and uncited claims.
- A deterministic, traceable evidence response when the configured answer model is unavailable.
- A four-query bilingual/cross-language retrieval golden set that keeps hybrid Top-1 quality above the BM25 baseline.
- Page-aware PDF sections and figure captions, plus structured section/page/chunk locators for bilingual research-assistant citation cards.
- Versioned `paper_chunks` features for authorized PDF abstracts, sections, tables, and figure captions, indexed into the same local retrieval pipeline as knowledge and Zotero.
- A provider-neutral `RetrievalChunk` contract and dependency-free Chinese/English BM25 retrieval layer for research-assistant evidence.
- The first FTI-style feature pipeline: versioned, content-addressed knowledge chunks with lazy backfill and lifecycle synchronization.
- A ScholarNova-specific FTI architecture map covering collection, feature, evaluation/optimization, inference, observability, and release gates.
- Read-only Zotero Local API detection and collection discovery from the Settings page.
- Explicit import of up to 100 Zotero bibliographic records with DOI-based idempotent updates.
- A zero-network local-library search source so imported Zotero papers participate in normal ScholarNova searches.
- A traceable research-assistant MVP that retrieves evidence from the ScholarNova knowledge base and live local Zotero before invoking the configured model.
- An in-app Zotero setup checklist, detected version display, and a clear Zotero 10+ write-back requirement notice.
- A Chinese client-product roadmap covering model portability, fast/deep search, evidence-grounded RAG, authorized library access, reference-manager adapters, MCP boundaries, rollout phases, and acceptance metrics.

### Security

- Real capability probes inherit saved credentials only from the matching provider and never reuse a primary model key across providers.
- Embedding credentials are isolated from chat credentials, stored only by the local backend, and never returned to browser storage.
- Zotero access is pinned to `127.0.0.1:23119`; users cannot supply an arbitrary integration URL.
- Automatic Zotero detection is read-only. Writing bibliographic items requires an explicit user request through the local Connector; no direct `zotero.sqlite` edits are made.

### Changed

- PDF page images remain isolated to the vision task while parsed paper text can fall back across text models; diagram generation remains an independent image route.
- Recommendation planning no longer asks a model to invent papers without verified academic-API metadata and instead emits search queries for later verification.
- Knowledge and route-analysis UI labels are provider-neutral rather than hard-coded to MiMo.
- Local Ollama HTTP endpoints are accepted only for localhost in debug mode; external HTTP and private-network SSRF protections remain enforced.
- The research assistant now uses BM25 by default, optionally embeds a source-balanced pool of at most 256 candidates, fuses rankings with RRF, and transparently falls back to BM25 on every semantic-service failure.
- Embeddings are cached by provider, model, and normalized input hash so repeated content does not consume additional embedding tokens.
- Research-assistant traces now expose evidence packing, answer generation, and answer verification as distinct inference stages.
- PDF retrieval features now use `pdf-parser-chunker-v2`; analyzing an older imported PDF deterministically refreshes its locators without changing the original file.
- Search, analysis, and research-assistant requests now use independent local rate-limit buckets, so one workflow cannot consume another workflow's allowance.
- PDF upload and full-text analysis synchronize deterministic retrieval features without making an additional LLM call.
- The research assistant now ranks ScholarNova knowledge, parsed PDF, and live Zotero chunks in one candidate pool, limits each document to two evidence chunks, and performs no extra model call for retrieval.
- The research assistant now retrieves diverse, relevant knowledge chunks instead of truncating whole knowledge records into the model context.
- Refreshed the Zhipu GLM selector with current official model IDs, including GLM-5.2 and lower-cost Flash options.
- Zhipu connection probes now disable hidden reasoning and skip retries, preventing an empty tiny probe or an overloaded model from looking like a broken API key.
- Model-test timeouts now return an actionable message instead of an empty error.

### Fixed

- Routed ScholarNova usage questions to the built-in product guide instead of forcing unrelated paper evidence, and stopped knowledge retrieval from falling back to arbitrary recent items when no relevant match exists.
- Scoped the large landing-page hero styles to the home page so they no longer create excessive whitespace or oversized typography on the research-assistant page.
- Kept an empty assistant conversation at the top of the page instead of automatically scrolling to the composer on first load or after clearing.
- Prevented shared card paragraph styles from overriding the user-message contrast in the research assistant.

### Security

- Model credentials are no longer persisted in browser storage or returned by the configuration API; blank saves preserve matching secrets already stored by the local backend.

### Verified

- 320 non-integration backend tests and 21 frontend tests pass; TypeScript checks and the production build are clean. A live SiliconFlow `Qwen/Qwen3-8B` structured-output probe passed in 7.374 seconds using 38 provider-reported Tokens.
- 315 non-integration backend tests and 21 frontend tests pass; TypeScript checks and the production build are clean. A manual Zhipu GLM-5.2 structured-output probe passed in 13.718 seconds using 32 provider-reported Tokens.
- 310 non-integration backend tests and 19 frontend tests pass; TypeScript checks and the production build are clean. The FTI-3D regression suite verifies text routing, vision isolation, Token accounting, deterministic fallback, and non-fabricating recommendation fallback without a live model call.
- 74 focused backend tests and 19 frontend tests cover capability hints, bounded query-planning routing, translation route metadata, model fallback, and local Ollama URL validation without live model calls.
- 288 non-integration backend tests and 18 frontend tests pass; page-aware parsing, hybrid ranking, vector-cache reuse, citation-integrity checks, deterministic model-offline fallback, token accounting, and BM25 fallback are covered without calling a live model.
- The production frontend build succeeds; the four-case retrieval regression fixture improves Top-1 from BM25 3/4 to hybrid 4/4 and is explicitly not presented as a competition benchmark.
- TypeScript checks are clean. Local transactional validation created four PDF features, retrieved two relevant chunks, and rolled the test record back without invoking a model.
- 13 focused Zotero, local-library, and research-assistant tests pass. The offline backend suite remains green; three live Semantic Scholar checks may receive the provider's HTTP 429 rate limit.
- The three remaining full-suite failures are live Semantic Scholar integration checks returning HTTP 429, not local regressions.
- All 16 frontend tests, TypeScript checks, and the production frontend build pass.

## [1.1.1] - 2026-07-19

### Added

- Persistent local PDF import for papers whose publisher blocks automated retrieval.
- Explicit full-text/abstract coverage, retrieval-source, failure-detail, and visual-page fields in analysis results.
- Drag and keyboard resizing for the paper detail panel, with locally remembered width.
- OpenAlex, Semantic Scholar, and Crossref DOI resolvers in the legal OA acquisition chain.

### Changed

- PDF downloads are streamed with a 50 MB ceiling, no longer depend on often-blocked HEAD requests, and avoid retrying the same failed URL through multiple metadata providers.
- Importing a PDF clears the previous abstract-only cache and immediately starts a new analysis.
- Unpaywall is skipped unless a valid contact email is configured, avoiding misleading 422 errors.
- Task-specific model rows now inherit the saved default credentials after restart when they use the same provider; credentials never leak across different providers.
- The UI distinguishes a parsed PDF from a model-completed full-text analysis when the provider is temporarily unavailable.
- PDF figure pages use the task-specific vision model when configured; if that provider rejects image input, analysis retries with the full parsed text and reports zero visual pages instead of claiming the images were read.
- Full-text analysis now returns and displays provider-reported prompt, completion, and total Token usage instead of leaving model cost invisible.

### Verified

- 244 non-integration backend tests and 16 frontend tests pass; production frontend build succeeds.
- Multipart PDF upload, persistent status lookup, structured full-text parsing, invalid-file rejection, and DOI resolver fallback are covered by automated tests.
- The reported Science China DOI was tested live: every resolver points to the publisher endpoint, which returns HTTP 418 to automated clients, so ScholarNova now reports the restriction and offers authorized local PDF import.

## [1.1.0] - 2026-07-17

### Added

- Live search elapsed time and per-call source/API/query/result/latency status.
- Intentional repeat search for the same query and per-paper temporary analysis retention inside one search run.
- Legal open-access PDF acquisition with structured full-text, table, figure-caption, and optional visual-page analysis.
- Title-to-Chinese translation controls on result cards.
- OpenAlex journal indicators plus authorized CSV/JSON import for JCR, historical CAS, and SJR quartiles.
- Institutional-library query handoff and separate real HTTP(S) campus-proxy configuration.
- Portable-build desktop shortcut self-healing.

### Changed

- Query-planning deadline is bounded at 12 seconds and scholarly sources are reported as each parallel call completes.
- JCR/CAS/SJR quartiles are never inferred; provenance and year are shown for imported records.
- Full-text analysis explicitly reports whether it used full text, visual pages, or abstract-only fallback.

### Verified

- 237 non-integration backend tests and 16 frontend tests pass.
- Live Crossref/OpenAlex search returned 12 ranked results in 16.8 seconds with two traceable API calls.
- An open arXiv PDF produced 47,834 characters of section-aware context and two visual pages.

## [1.0.0] - 2026-07-16

### Added

- Windows installer and portable desktop editions with a dedicated ScholarNova icon.
- Automatic startup of the bundled FastAPI backend and local frontend.
- Per-user local database, generated files, model configuration, and logs under AppData.
- GitHub Actions workflow that publishes Windows executables for version tags.
- Chinese Windows desktop release and troubleshooting guide.

### Fixed

- Packaged runtime paths for generated images and research-route diagrams.
- Relative diagram URLs for desktop and self-hosted deployments.
- Lightweight desktop liveness check and pinned Pydantic build dependency compatibility.
- Search creation now returns its `202 pending` response immediately while retaining the asynchronous worker task.
- Model connection checks have a 15-second deadline, and duplicate nested LLM retries were removed.
- Desktop builds explicitly disable electron-builder auto-publish so the release workflow performs one authenticated publish step.

### Security

- Desktop packages contain no maintainer API keys, gated datasets, or local databases.
