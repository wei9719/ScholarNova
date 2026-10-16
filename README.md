<p align="center"><img src="docs/assets/scholarnova-cover-en.svg" width="800" alt="ScholarNova — AI Research Workspace"></p>
<p align="center"><a href="https://github.com/wei9719/ScholarNova/releases/latest">Download / 下载</a> · <a href="README.md">English</a> · <a href="README.zh-CN.md">简体中文</a> · <a href="docs/USER_MANUAL.zh-CN.md">User manual (中文)</a> · <a href="CHANGELOG.md">Changelog</a></p>
<p align="center"><a href="https://github.com/wei9719/ScholarNova/actions/workflows/ci.yml"><img src="https://github.com/wei9719/ScholarNova/actions/workflows/ci.yml/badge.svg" alt="CI"></a> <a href="https://github.com/wei9719/ScholarNova/actions/workflows/desktop-release.yml"><img src="https://github.com/wei9719/ScholarNova/actions/workflows/desktop-release.yml/badge.svg" alt="Desktop builds"></a> <a href="LICENSE"><img src="https://img.shields.io/badge/Own_source-MIT-d4a84f" alt="Project-owned source: MIT"></a> <a href="docs/DESKTOP_LICENSE_REVIEW.md"><img src="https://img.shields.io/badge/Desktop_combination-AGPLv3-536b82" alt="Desktop combination: AGPLv3"></a></p>

# ScholarNova

Chinese developer learning guide: [Understand ScholarNova step by step](docs/PROJECT_EXPLAINED.zh-CN.md), covering the real workflow, terminology, RAG, local Qwen and controlled diagram generation.

A desktop research workspace for individuals: **find papers → read full text → save evidence → ask grounded questions → plan research**. Chinese / English, light / dark themes, and bring-your-own-key model configuration.

**Research learning workflow (current source):** search all years, year to date, the last three calendar years, or a custom year range. Choose a note research category, then create or continue a reading note from paper detail. Nine sections separate paper material, AI suggestions and personal work; derive foundation/skill cards or export Markdown using the existing Knowledge, Assistant and research-route pages. The [product and user manual (中文)](docs/USER_MANUAL.zh-CN.md) includes a traffic-data-imputation example and acceptance checklist. Search time is not index-update time, and recency does not establish novelty. This stage records reproduction, figure and formatting guidance; it does not execute experiments or add native Word/PDF export.

**Learn through a complete research case:** the [illustrated walkthrough (Word, 中文)](docs/guides/ScholarNova完整研究案例使用手册.docx) follows METR-LA traffic-speed imputation through search, full-text reading, nine-section notes, reusable knowledge, scoped Q&A, research planning and export. It includes real interface screenshots, sample prompts, reproduction checklists and acceptance steps. This Word guide targets **1.2.12**; journal discovery, authorized access and guided reading are covered by [the updated online manual](docs/USER_MANUAL.zh-CN.md#12-1213期刊发现授权全文和原文伴读) and [1.2.13 walkthrough](docs/releases/v1.2.13-guided-reading.zh-CN.md).

**[1.2.13 is officially released](https://github.com/wei9719/ScholarNova/releases/tag/v1.2.13)** with Windows Setup/Portable and Intel/Apple Silicon Mac DMG/ZIP assets. All three platforms passed native builds and application/backend startup checks; latest downloads point to this release. New features include user-triggered journal discovery, isolated authorized access, page-anchored reading assistance and bounded comparison of 2–5 local writing samples, reusing the existing knowledge library. See [the walkthrough](docs/releases/v1.2.13-guided-reading.zh-CN.md) and [live GLM/Qwen acceptance and release record](docs/reports/v1.2.13-guided-reading-acceptance.zh-CN.md). Institutional subscriptions still require user authorization and site-specific checks; published-paper tests are not campus-login acceptance.

### Start with 1.2.13

| Goal | Entry and actual scope |
| --- | --- |
| Discover journals | In search results, choose **Find related journals**. Check journal type, Source ID and ISSN, candidate sample counts, homepage and related papers; the initial paper search does not wait for this query |
| Verify metrics | Keep the metric name, source and reporting year. OpenAlex indicators are not JIF; official JIF values are shown only from locally imported, licensed data with a year |
| Obtain authorized full text | Open **Institutional full text** from paper detail to bind a paper. Sign in yourself, detect a PDF or use the site's download button, then review download identity and confirm. Use external download/manual import for incompatible sites |
| Read with an assistant | Open **Guided reading** from paper detail. Translate passages, explain text/equations/figures, learn prerequisites with exercises, examine writing samples and plan further reading on a specified page |
| Build writing references | Compare 2–5 papers with local PDFs using bounded excerpts, verify page/version sources, then review and save a skill-card draft in the existing knowledge library |

Institutional windows use isolated direct sessions; public windows use system networking without changing the system VPN. PDF import saves and parses locally, without automatically calling a cloud model. AI tasks send the selected passage/page after an explicit request. Scans have no automatic OCR; equation/figure interpretation requires an explicitly configured image-capable Vision task. Reading suggestions and recommendations are not proof of mastery.

Historical records: [reading notes 1.2.12](docs/reports/v1.2.12-research-notebook.zh-CN.md), [search improvements 1.2.11](docs/reports/v1.2.11-search-recommendations.zh-CN.md), [October 3 live workflows](docs/reports/v1.2.10-live-business-acceptance.zh-CN.md), [concurrency work](docs/reports/v1.2.7-concurrency.zh-CN.md) and [related-project comparison](docs/research/peer-landscape-2026-10-02.zh-CN.md). Historical measurements are not performance guarantees for this version.

## Download and install

Open the **[v1.2.13 download page](https://github.com/wei9719/ScholarNova/releases/tag/v1.2.13)** or [latest release](https://github.com/wei9719/ScholarNova/releases/latest), expand Assets and choose your platform. All eight assets are uploaded: Windows Setup/Portable, DMG/ZIP for both Mac architectures, matching corresponding source and `SHA256SUMS.txt`. The [three-platform release workflow](https://github.com/wei9719/ScholarNova/actions/runs/38919441705) completed successfully.

**Community desktop builds follow the selected AGPL open-source distribution route.** Each release must include package-specific notices and matching corresponding source, and pass the automated source checks and Windows / macOS smoke tests before publication. See the [distribution requirements](docs/DESKTOP_LICENSE_REVIEW.md) and each release's assets for available versions; older releases do not automatically contain the latest source changes.

| Computer | Download | Install |
| --- | --- | --- |
| Windows 10/11, 64-bit | [Setup installer](https://github.com/wei9719/ScholarNova/releases/download/v1.2.13/ScholarNova-Setup-1.2.13-x64.exe) | Run the installer; desktop and Start menu shortcuts are included |
| Windows portable | [Portable executable](https://github.com/wei9719/ScholarNova/releases/download/v1.2.13/ScholarNova-Portable-1.2.13-x64.exe) | Keep it in a permanent folder and launch |
| macOS 13+, Apple Silicon (M-series) | [DMG](https://github.com/wei9719/ScholarNova/releases/download/v1.2.13/ScholarNova-1.2.13-arm64.dmg) / [ZIP](https://github.com/wei9719/ScholarNova/releases/download/v1.2.13/ScholarNova-1.2.13-arm64.zip) | Drag the app into Applications; extract ZIP first |
| macOS 13+, Intel | [DMG](https://github.com/wei9719/ScholarNova/releases/download/v1.2.13/ScholarNova-1.2.13-x64.dmg) / [ZIP](https://github.com/wei9719/ScholarNova/releases/download/v1.2.13/ScholarNova-1.2.13-x64.zip) | Drag the app into Applications; extract ZIP first |

The desktop app bundles the interface, local backend and SQLite. No Python, Node.js or Docker is required. First launch may take longer while the portable runtime extracts. Quit before upgrading; personal data is stored outside the installation directory.

For everyday Windows use, prefer **Setup**. Portable startup requires extraction and depends on the device and storage; automated smoke checks are not a universal performance guarantee. See [this release's live acceptance](docs/reports/v1.2.13-guided-reading-acceptance.zh-CN.md); the earlier guidance-retry record remains in the [1.2.5 report](docs/reports/v1.2.5-assistant-retry.zh-CN.md).

This build requires macOS 13 or later, following [Electron 44's platform requirements](https://www.electronjs.org/blog/electron-44-0).

**Signing: commercial code signing and Apple notarization are not configured.** Windows may show SmartScreen; macOS may require approval in System Settings → Privacy & Security. Verify the source and checksum first. Do not disable system-wide protections. Frictionless installation still requires the publisher to complete signing and notarization.

## Your first workflow

1. **Configure a model**: in Settings, select GLM, Qwen, SiliconFlow or another supported provider. Enter your key and an available model ID, test, then save.
2. **Find papers**: choose all years, year to date, the last three calendar years or a custom range. Dates constrain source retrieval, not just the current list; indexing can still lag. Automatic planning sends short topics directly to search; fast mode skips the planning model; AI mode makes one attempt with an approximately 20-second model-stage budget and then falls back to rules. This is not a total search deadline. Repeat a query with or without changing its mode or dates.
3. **Read full text**: select a paper and analyze it. If open full text is unavailable, import an authorized PDF. Obtaining a PDF does not mean the model read it all: analysis discloses selected sections, truncated text and the image pages actually supplied.
4. **Build knowledge**: choose a note research category, open **Reading note / continue editing** in paper detail, and save your nine-section record. Optional section drafts use the Analysis task model with local abstracts or cached full-text excerpts; review, append, then save. Resource links and public emails are extracted locally. In Knowledge, filter by type/category, derive foundation or skill cards, and export Markdown. Choose an evidence category in Assistant before asking research questions; chat folders organize conversations rather than setting evidence scope.
5. **Plan research**: open **AI Research Analysis** from Knowledge, expand a category and select entries on the analysis page. Enter your research goals and constraints. Planning reads only the first 200 characters and up to three research points per entry; create a concise planning summary when working from long notes. Review the analysis, then explicitly save its full text and knowledge links as a research route before generating diagrams. Text, vision and diagram tasks can use separate models.

Basic keyword retrieval does not require an LLM key. Scholarly-source access depends on source policies, credentials, quota and network. AI operations use your configured provider and its billing.

**New to the Assistant?** In ordinary knowledge chat, ask “How can I use you?” and explain the step you are on. Product guidance retains its compact guide, recent conversation and **Retry AI** recovery. Missing usage remains unknown, not free; research questions still require material. To read a paper passage by passage, choose **Guided reading** from paper detail and select a page/task. [Current acceptance report (中文)](docs/reports/v1.2.13-guided-reading-acceptance.zh-CN.md)

## Product preview

Screenshots illustrate the workflow; click to enlarge. Language and theme can be changed in the app.

<table>
<tr>
<td width="50%" valign="top"><strong>Multi-source search</strong><br><a href="docs/assets/screenshots/search-results-zh.png"><img src="docs/assets/screenshots/search-results-zh.png" width="100%" alt="Paper search"></a></td>
<td width="50%" valign="top"><strong>Research analysis</strong><br><a href="docs/assets/screenshots/knowledge-analysis-zh.png"><img src="docs/assets/screenshots/knowledge-analysis-zh.png" width="100%" alt="Knowledge analysis"></a></td>
</tr>
<tr>
<td width="50%" valign="top"><strong>Personal knowledge base</strong><br><a href="docs/assets/screenshots/knowledge-zh.png"><img src="docs/assets/screenshots/knowledge-zh.png" width="100%" alt="Knowledge entries"></a></td>
<td width="50%" valign="top"><strong>Research routes and diagrams</strong><br><a href="docs/assets/screenshots/route-zh.png"><img src="docs/assets/screenshots/route-zh.png" width="100%" alt="Research route"></a></td>
</tr>
</table>

<details>
<summary>More: home, settings and English dark mode</summary>

Open full-size screenshots without expanding long images on this page.

[Home](docs/assets/screenshots/home-zh.png) · [Settings](docs/assets/screenshots/settings-zh.png) · [English / dark](docs/assets/screenshots/search-results-en-dark.png)

</details>

## Capabilities

| Workflow | Available capability |
| --- | --- |
| Complex discovery | Query decomposition, parallel sources, deduplication, constraints and combined ranking; relevance separated from citation impact |
| Paper reading | Authorized PDF import, selected-page/passage assistance, translation, equation/figure interpretation and prerequisite exercises; page/version provenance and a resizable detail panel |
| Journals and writing | User-triggered journal discovery, named/year-labelled metric sources and related papers; bounded comparison of 2–5 local PDFs, reviewed before saving to existing skill cards |
| Research workspace | Categorized reading notes, foundation and skill cards; nine sections, personal progress, on-demand drafts and Markdown export; existing research routes and structured diagrams |
| Evidence-based Q&A | BM25 or optional Embedding + RRF; source/chunk locations, citation checks and tool traces |
| Model portability | Primary, explicit fallback and task models; provider-reported usage, timeouts and bounded retries |
| Reference tools | Local Zotero detection, collection discovery and metadata import; explicit export requests subject to the local interface's write support |

**Evidence limits:** citation checks validate source IDs and sentence coverage, not semantic entailment. Insufficient evidence is reported. Model failures or failed citation repair return labelled source excerpts instead of an unsupported synthesis.

**Session scope:** leaving Search clears its results, temporary analyses and note-category selection rather than keeping permanent search history. Recent queries live only in the current window session. Saved notes, knowledge and Assistant conversations are retained separately; unsaved forms are not automatically archived, and clearing UI state does not delete server-side file caches.

## Configuration and integrations

- [Optional local text model setup (中文)](docs/local-model.zh-CN.md): an isolated runtime and model copy for Assistant only, not bundled weights or a fully offline workflow. Includes private configuration, resource limits, start/stop controls and rollback; real desktop acceptance must be verified separately.
- [API application links and setup](docs/API_PROVIDERS.md): GLM, Qwen / Bailian, SiliconFlow, MiMo, SenseNova, OpenAI and Ollama.
- **Zotero**: start Zotero and enable “Allow other applications on this computer to communicate with Zotero” under Settings → Advanced. Detect the connection and select a collection in ScholarNova. Read access does not prove write permission; use bibliographic export if direct writing is unavailable.
- **Current-source Zotero sync** verifies the complete personal-library collection path and reads back the saved destination. Metadata is supported; PDF attachments are not guaranteed. Group libraries, ambiguous paths and unconfirmed writes are reported explicitly—do not blindly retry a possibly completed write.
- **Institutional libraries**: Search opens the portal with a query; paper detail opens a bound, isolated direct-access window. Users perform SSO and download authorization, review detected PDF identity and confirm import. There is no login automation, subscription bypass or bulk download. Incompatible logins, internal addresses and pages use external download/manual import; institution-approved VPN/WebVPN requirements still apply.
- **Journal metrics**: open indicators retain their source and are not JIF. JCR / CAS quartiles and official JIF require licensed local imports. A minimal JIF CSV uses `Journal,JIF,JIF Year,Source`, for example `Target journal,5.4,2025,My licensed source`; this row illustrates the format, not a real journal's score.
- **Network**: API services, libraries and local Zotero can have different routing needs. Bypass proxies for `localhost` and `127.0.0.1`; check the endpoint, model access, quota and network when diagnosing failures.

## Data and privacy

Data lives in `%APPDATA%/scholarnova-desktop` on Windows or `~/Library/Application Support/scholarnova-desktop` on macOS. Quit before copying the entire user-data directory for backup or migration.

Keys remain in the local backend configuration and are excluded from browser persistence, the public repository and installers. Configuration is not currently encrypted with an OS keychain; protect backups. Cloud calls send the current query and relevant selected material to your chosen provider, along with authentication credentials to the configured endpoint. Review third-party endpoint policies before use.

## Development and verification

React / TypeScript provides the interface; Electron hosts the desktop app; FastAPI runs local services; SQLite stores data. Search, PDF parsing, model routing and RAG are separate modules.

- [Source deployment and development](docs/DEVELOPMENT.md)
- [Desktop builds, smoke tests and release process (中文)](docs/desktop-release.zh-CN.md)
- [FTI architecture (中文)](docs/FTI_PIPELINE_ARCHITECTURE.zh-CN.md) · [Product roadmap (中文)](docs/AI_APPLICATION_ROADMAP.zh-CN.md)
- [Historical v1.2.1 acceptance report (中文)](docs/reports/v1.2.1-consumer-readiness.zh-CN.md) · [Changelog](CHANGELOG.md)
- [Historical real-provider workflow validation, 2026-09-08 (中文)](docs/reports/2026-09-08-live-api-validation.zh-CN.md): model completion, deterministic fallbacks, image quality and unverified items are distinguished. Later fixes are recorded in the Changelog and are not included in the v1.2.1 installers.

Historical Asta results: **F1 0.341379** on an 18-query validation subset; **F1 0.283713** on the 27 binary-labelled queries within a 66-query file. These are not full competition scores or a like-for-like SPAR comparison. They have not been rerun for these product fixes. [Original evaluation report](outputs/competition-benchmark-report-2026-07-02.md)

## Feedback

Report reproducible issues with OS, app version and sanitized error details in [Issues](https://github.com/wei9719/ScholarNova/issues). Do not attach API keys or private papers. See [CONTRIBUTING](CONTRIBUTING.md) and [SECURITY](SECURITY.md).

## Open-source licenses

Project-owned code retains its [MIT License and author notice](LICENSE). The combined desktop distribution including PyMuPDF / MuPDF follows the applicable GNU AGPL v3 terms; independent bundled components keep their own notices. No noncommercial restriction is added. See [third-party notices](THIRD_PARTY_NOTICES.md) and the [distribution record](docs/DESKTOP_LICENSE_REVIEW.md).

Each published desktop version is to provide `ScholarNova-VERSION-corresponding-source.zip` and `SHA256SUMS.txt` alongside its installers, with source provenance in `SOURCE_MANIFEST.json`. Package-specific notices are in the app resources `legal/` folder. Check the actual Release assets for availability; the generic GitHub “Source code” ZIP alone is not the full dependency-source bundle. Models, scholarly data and papers retain their own terms.
