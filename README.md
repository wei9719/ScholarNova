<p align="center"><img src="docs/assets/scholarnova-cover-en.svg" width="800" alt="ScholarNova — AI Research Workspace"></p>
<p align="center"><a href="https://github.com/wei9719/ScholarNova/releases/tag/v1.2.5">Download / 下载</a> · <a href="README.md">English</a> · <a href="README.zh-CN.md">简体中文</a> · <a href="CHANGELOG.md">Changelog</a></p>
<p align="center"><a href="https://github.com/zhangweiguo9719-web/ScholarNova/actions/workflows/ci.yml"><img src="https://github.com/zhangweiguo9719-web/ScholarNova/actions/workflows/ci.yml/badge.svg" alt="CI"></a> <a href="https://github.com/zhangweiguo9719-web/ScholarNova/actions/workflows/desktop-release.yml"><img src="https://github.com/zhangweiguo9719-web/ScholarNova/actions/workflows/desktop-release.yml/badge.svg" alt="Desktop builds"></a> <a href="LICENSE"><img src="https://img.shields.io/badge/Own_source-MIT-d4a84f" alt="Project-owned source: MIT"></a> <a href="docs/DESKTOP_LICENSE_REVIEW.md"><img src="https://img.shields.io/badge/Desktop_combination-AGPLv3-536b82" alt="Desktop combination: AGPLv3"></a></p>

# ScholarNova

Chinese developer learning guide: [Understand ScholarNova step by step](docs/PROJECT_EXPLAINED.zh-CN.md), covering the real workflow, terminology, RAG, local Qwen and controlled diagram generation.

A desktop research workspace for individuals: **find papers → read full text → save evidence → ask grounded questions → plan research**. Chinese / English, light / dark themes, and bring-your-own-key model configuration.

## Download and install

**[v1.2.5 is available](https://github.com/wei9719/ScholarNova/releases/tag/v1.2.5)** for Windows x64, Intel Mac and Apple Silicon Mac, together with matching source and checksums. Retry failed guidance in place without losing earlier usage, use a compact model context, and benefit from improved portable extraction. See the [release and validation report](docs/reports/v1.2.5-assistant-retry.zh-CN.md).

Open **[GitHub Releases](https://github.com/wei9719/ScholarNova/releases/latest)** and choose an existing asset for your computer. v1.2.5 has passed automated native builds and startup checks on all three desktop platforms.

**Community desktop builds follow the selected AGPL open-source distribution route.** Each release must include package-specific notices and matching corresponding source, and pass the automated source checks and Windows / macOS smoke tests before publication. See the [distribution requirements](docs/DESKTOP_LICENSE_REVIEW.md) and each release's assets for available versions; older releases do not automatically contain the latest source changes.

| Computer | Download | Install |
| --- | --- | --- |
| Windows 10/11, 64-bit | `ScholarNova-Setup-VERSION-x64.exe` | Run the installer; desktop and Start menu shortcuts are included |
| Windows portable | `ScholarNova-Portable-VERSION-x64.exe` | Keep it in a permanent folder and launch |
| macOS 13+, Apple Silicon (M-series) | `ScholarNova-VERSION-arm64.dmg` | Drag into Applications |
| macOS 13+, Intel | `ScholarNova-VERSION-x64.dmg` | Drag into Applications |

The desktop app bundles the interface, local backend and SQLite. No Python, Node.js or Docker is required. First launch may take longer while the portable runtime extracts. Quit before upgrading; personal data is stored outside the installation directory.

For everyday Windows use, prefer **Setup**. After changing extraction in v1.2.5, two complete local portable checks passed in 74.50 / 63.47 seconds; the earlier two attempts exceeded 120 seconds. The new download is larger and still needs extraction. These single-PC results do not guarantee fast startup on every device.

This build requires macOS 13 or later, following [Electron 44's platform requirements](https://www.electronjs.org/blog/electron-44-0).

**Signing: commercial code signing and Apple notarization are not configured.** Windows may show SmartScreen; macOS may require approval in System Settings → Privacy & Security. Verify the source and checksum first. Do not disable system-wide protections. Frictionless installation still requires the publisher to complete signing and notarization.

## Your first workflow

1. **Configure a model**: in Settings, select GLM, Qwen, SiliconFlow or another supported provider. Enter your key and an available model ID, test, then save.
2. **Find papers**: enter a research question. Watch elapsed time, actual sources, counts and source status. You can run the same query again.
3. **Read full text**: select a paper and analyze it. If open full text is unavailable, import an authorized PDF. The UI distinguishes abstract, full text and image-page coverage.
4. **Build knowledge**: save findings, then organize research folders and separate Assistant conversations.
5. **Plan research**: analyze your knowledge base, create a research route and generate an architecture diagram. Text, vision and diagram tasks can use separate models.

Basic keyword retrieval does not require an LLM key. Scholarly-source access depends on source policies, credentials, quota and network. AI operations use your configured provider and its billing.

**New to the Assistant?** Ask “How can I use you?”, then “What next?”, and describe the step you are on. The v1.2.5 source uses a compact guide and recent conversation with your selected Assistant model, a 60-second model-stage budget and a 320-token output cap. If the latest guidance fails, choose **Retry AI**: it replaces that failed answer and keeps earlier request and usage records. Missing usage is unknown, not free; research questions still require evidence. Check the actual Release version before downloading—older installers do not update automatically. [Validation report (Chinese)](docs/reports/v1.2.5-assistant-retry.zh-CN.md)

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
| Paper reading | Text, sections, tables, captions and available figure pages; authorized PDF import; Chinese title translation; resizable detail panel |
| Research workspace | Knowledge entries, research routes and structured diagrams; folders and isolated Assistant conversations |
| Evidence-based Q&A | BM25 or optional Embedding + RRF; source/chunk locations, citation checks and tool traces |
| Model portability | Primary, explicit fallback and task models; provider-reported usage, timeouts and bounded retries |
| Reference tools | Local Zotero detection, collection discovery and metadata import; explicit export requests subject to the local interface's write support |

**Evidence limits:** citation checks validate source IDs and sentence coverage, not semantic entailment. Insufficient evidence is reported. Model failures or failed citation repair return labelled source excerpts instead of an unsupported synthesis.

**Session scope:** leaving Search clears results and temporary analyses. Recent queries live only in the current window session. Saved knowledge and Assistant conversations are retained separately.

## Configuration and integrations

- [API application links and setup](docs/API_PROVIDERS.md): GLM, Qwen / Bailian, SiliconFlow, MiMo, SenseNova, OpenAI and Ollama.
- **Zotero**: start Zotero and enable “Allow other applications on this computer to communicate with Zotero” under Settings → Advanced. Detect the connection and select a collection in ScholarNova. Read access does not prove write permission; use bibliographic export if direct writing is unavailable.
- **Current-source Zotero sync** verifies the complete personal-library collection path and reads back the saved destination. Metadata is supported; PDF attachments are not guaranteed. Group libraries, ambiguous paths and unconfirmed writes are reported explicitly—do not blindly retry a possibly completed write.
- **Institutional libraries**: portal handoff and query transfer are available. Users still handle SSO, campus access or their institution's VPN. Automatic login and bulk subscription downloads are not implemented.
- **Journal quartiles**: open citation metrics are available. JCR / CAS quartiles require a licensed, year-labelled dataset; missing values remain unknown.
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

Report reproducible issues with OS, app version and sanitized error details in [Issues](https://github.com/zhangweiguo9719-web/ScholarNova/issues). Do not attach API keys or private papers. See [CONTRIBUTING](CONTRIBUTING.md) and [SECURITY](SECURITY.md).

## Open-source licenses

Project-owned code retains its [MIT License and author notice](LICENSE). The combined desktop distribution including PyMuPDF / MuPDF follows the applicable GNU AGPL v3 terms; independent bundled components keep their own notices. No noncommercial restriction is added. See [third-party notices](THIRD_PARTY_NOTICES.md) and the [distribution record](docs/DESKTOP_LICENSE_REVIEW.md).

Each published desktop version is to provide `ScholarNova-VERSION-corresponding-source.zip` and `SHA256SUMS.txt` alongside its installers, with source provenance in `SOURCE_MANIFEST.json`. Package-specific notices are in the app resources `legal/` folder. Check the actual Release assets for availability; the generic GitHub “Source code” ZIP alone is not the full dependency-source bundle. Models, scholarly data and papers retain their own terms.
