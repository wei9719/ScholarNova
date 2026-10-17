<p align="center"><img src="docs/assets/scholarnova-cover-en.svg" width="800" alt="ScholarNova — AI Research Workspace"></p>
<p align="center"><a href="https://github.com/wei9719/ScholarNova/releases/latest">Download / 下载</a> · <a href="README.md">English</a> · <a href="README.zh-CN.md">简体中文</a> · <a href="docs/USER_MANUAL.zh-CN.md">产品与使用手册</a> · <a href="CHANGELOG.md">Changelog</a></p>
<p align="center"><a href="https://github.com/wei9719/ScholarNova/actions/workflows/ci.yml"><img src="https://github.com/wei9719/ScholarNova/actions/workflows/ci.yml/badge.svg" alt="CI"></a> <a href="https://github.com/wei9719/ScholarNova/actions/workflows/desktop-release.yml"><img src="https://github.com/wei9719/ScholarNova/actions/workflows/desktop-release.yml/badge.svg" alt="Desktop builds"></a> <a href="LICENSE"><img src="https://img.shields.io/badge/Own_source-MIT-d4a84f" alt="项目自有源代码：MIT"></a> <a href="docs/DESKTOP_LICENSE_REVIEW.md"><img src="https://img.shields.io/badge/Desktop_combination-AGPLv3-536b82" alt="桌面发行组合：AGPLv3"></a></p>

# ScholarNova

一个面向个人研究者的桌面科研工作台：**找论文 → 读全文 → 存知识 → 基于证据问答 → 规划研究路线**。支持中文 / English、明暗主题和自带模型 API Key（BYOK）。

**研究学习流程（当前源码）**：按全部、今年、近三年或自定义年份检索，在搜索页指定“笔记研究分类”，从论文详情新建或继续阅读笔记；九个章节分别保留论文材料、AI 建议和个人实践，再提炼基础知识、技能卡或导出 Markdown。不另建学习导航，继续使用知识库、智能体和研究路线。[产品与使用手册](docs/USER_MANUAL.zh-CN.md)包含交通流数据修复案例与逐步验收清单。检索时间不是索引更新时间，新近发表也不等于更有创新性；本阶段的复现、绘图和排版栏目提供记录与建议，不自动执行实验或生成原生 Word/PDF。

**跟着真实场景学使用**：[完整研究案例使用手册（Word）](docs/guides/ScholarNova完整研究案例使用手册.docx)以 METR-LA 交通速度缺失修复为研究点，配合实际界面，逐步讲解检索、全文阅读、九章笔记、知识积累、分类问答、研究路线与导出，并提供提问示例、复现检查表和验收方法。该 Word 手册对应 **1.2.12**；新增期刊发现、授权浏览和原文伴读，请看[在线手册第 12 节](docs/USER_MANUAL.zh-CN.md#12-1213期刊发现授权全文和原文伴读)与[1.2.13 使用案例](docs/releases/v1.2.13-guided-reading.zh-CN.md)。

**当前版本 [1.2.13 已正式发布](https://github.com/wei9719/ScholarNova/releases/tag/v1.2.13)**，提供 Windows Setup/便携版，以及 Intel Mac、Apple Silicon Mac 的 DMG/ZIP。三个平台均通过原生构建与应用/后端启动检查，最新下载已指向本版。本轮增加主动查询的相关期刊、独立授权全文窗口，以及智能体中的按页伴读和 2–5 篇本地全文写作样例对照；继续使用同一个知识库保存阅读笔记、基础知识和技能卡。操作见[1.2.13 使用案例](docs/releases/v1.2.13-guided-reading.zh-CN.md)，真实 GLM/Qwen 任务、失败与发行记录见[本轮验收报告](docs/reports/v1.2.13-guided-reading-acceptance.zh-CN.md)。学校订阅登录仍需用户授权与逐站点核验，不能以公开论文测试代替。

### 1.2.13 从哪里开始

**1.2.14 源码调试进展**：指定论文、PDF 页码与表号先定向定位，再检索原文；通用问答不再只取最新导入论文的片段。界面区分“引用标记检查”和“PDF 原文 / 知识笔记”，不把引用编号正确当成事实正确。翻译改为直接译文，写作对照优先抽取摘要与结论，九章笔记按栏目选择正文材料；Zotero 在目标集合内按 DOI / 题名去重。真实功能复测与未通过项见[1.2.14 实测记录](docs/reports/v1.2.14-functional-validation.zh-CN.md)。安装包仍以 Release 实际附件为准。

| 你想做什么 | 操作入口与实际范围 |
| --- | --- |
| 找与关键词相关的期刊 | 搜索结果下点击“查询相关期刊”，按 OpenAlex 类型、Source ID 与 ISSN 核对，查看本次候选命中数、主页和该刊相关论文；不自动增加首次搜索等待 |
| 核对期刊指标 | 每个指标保留名称、来源与年度；OpenAlex 开放指标不是影响因子。正式 JIF 只显示带年份的本机授权导入数据 |
| 取得机构全文 | 在论文详情点击“机构授权获取”，用户自行登录，识别页面 PDF 或点击下载按钮，核对绑定论文和下载信息后确认；不兼容的网站使用外部浏览器下载再手动导入 |
| 逐页读懂原文 | 论文详情点击“原文伴读”，进入智能体中的指定论文工作区；翻译选段、解释段落/公式、读图表、补基础与练习、整理写作样例、阅读拓展 |
| 积累投稿写作方法 | 伴读中选择 2–5 篇已有本地全文的样本进行有界对照，核对页码与版本，再整理为现有技能卡草稿；确认保存后才入库 |

校园窗口使用应用内独立直连会话，公共窗口沿用系统网络，不更改系统 VPN；账号授权仍由用户完成。导入 PDF 只在本地保存与解析，点击 AI 任务后才发送对应页面或选段。扫描件目前不自动 OCR；图表与公式解释需要在设置中明确配置支持图像输入的视觉模型。学习建议和阅读推荐不是掌握程度证明。

历史记录：[阅读笔记 1.2.12](docs/reports/v1.2.12-research-notebook.zh-CN.md)、[检索优化 1.2.11](docs/reports/v1.2.11-search-recommendations.zh-CN.md)、[10 月 3 日真实业务测试](docs/reports/v1.2.10-live-business-acceptance.zh-CN.md)、[并发治理](docs/reports/v1.2.7-concurrency.zh-CN.md)与[同类项目对标](docs/research/peer-landscape-2026-10-02.zh-CN.md)。历史数据不作为本版性能承诺。

## 下载与安装

到 **[v1.2.13 正式下载页](https://github.com/wei9719/ScholarNova/releases/tag/v1.2.13)**，展开 Assets，按你的电脑选择。也可使用[最新版本入口](https://github.com/wei9719/ScholarNova/releases/latest)。本版八项资产已上传，包括两个 Windows 可执行文件、两种 Mac 架构各 DMG/ZIP、同版本对应源码 ZIP 和 `SHA256SUMS.txt`；[三平台发布工作流](https://github.com/wei9719/ScholarNova/actions/runs/38919441705)已成功。

**社区桌面版采用已选择的 AGPL 开源发行路线。** 每个版本须包含逐包许可声明和相应源码，并通过自动源码检查及 Windows / macOS 启动测试后才能发布。详见 [发行要求](docs/DESKTOP_LICENSE_REVIEW.md)；可下载版本以同版本 Release 的实际附件为准，旧安装包不会自动包含最新源码改动。

| 你的电脑 | 选择文件 | 安装方法 |
| --- | --- | --- |
| Windows 10/11，64 位 | [Setup 安装版](https://github.com/wei9719/ScholarNova/releases/download/v1.2.13/ScholarNova-Setup-1.2.13-x64.exe) | 运行安装向导，创建桌面与开始菜单快捷方式 |
| Windows 免安装 | [Portable 便携版](https://github.com/wei9719/ScholarNova/releases/download/v1.2.13/ScholarNova-Portable-1.2.13-x64.exe) | 放在固定目录后双击运行 |
| macOS 13+，Apple Silicon（M 系列） | [DMG](https://github.com/wei9719/ScholarNova/releases/download/v1.2.13/ScholarNova-1.2.13-arm64.dmg) / [ZIP](https://github.com/wei9719/ScholarNova/releases/download/v1.2.13/ScholarNova-1.2.13-arm64.zip) | 将应用拖入“应用程序”；ZIP 先解压 |
| macOS 13+，Intel | [DMG](https://github.com/wei9719/ScholarNova/releases/download/v1.2.13/ScholarNova-1.2.13-x64.dmg) / [ZIP](https://github.com/wei9719/ScholarNova/releases/download/v1.2.13/ScholarNova-1.2.13-x64.zip) | 将应用拖入“应用程序”；ZIP 先解压 |

桌面版自带界面、本地后端和 SQLite，无需安装 Python、Node.js 或 Docker。首次启动可能因解压运行时稍慢。升级前退出应用，安装新版本；个人数据位于用户目录，不在安装目录内。

Windows 日常使用优先选择 **Setup 安装版**。便携版首次启动需要解压，耗时受设备和磁盘影响；自动启动检查不等于所有设备的性能承诺。升级与真实模型验收见[本版报告](docs/reports/v1.2.13-guided-reading-acceptance.zh-CN.md)；早期指导重试记录保留在[1.2.5 报告](docs/reports/v1.2.5-assistant-retry.zh-CN.md)。

本版要求 macOS 13 或更新系统，依据 [Electron 44 官方平台要求](https://www.electronjs.org/blog/electron-44-0)。

**签名状态：当前构建未配置商业代码签名与 Apple 公证。** Windows 可能显示 SmartScreen 提示；macOS 可能需要在“系统设置 → 隐私与安全性”允许打开。仅在确认下载来源和校验值后操作，不需要关闭系统安全保护。正式零干预安装体验仍需要发布者完成签名与公证。

## 第一次使用

1. **设置模型**：打开“设置”，选择 GLM、千问、硅基流动或其他已支持厂商，填写自己的 Key 和该平台可用的模型名称，测试后保存。无需更改代码。
2. **搜索论文**：输入研究问题，选择全部年份、今年至今、近三年至今或自定义年份。日期条件传到检索来源，不只过滤当前列表；各源仍有收录延迟。默认自动规划，短主题直接检索；快速检索不调用规划模型；AI 规划最多尝试一次，约 20 秒模型预算，失败后转规则检索。这不是整个搜索的总时限。同一关键词可换方式、年份或直接再次搜索。
3. **分析全文**：选择论文并分析。找不到开放全文时，可上传自己有权使用的 PDF。取得 PDF 不等于模型读完所有内容：分析会标明章节节选、文字截断及实际提供的图表页，便于回看原文核对。
4. **积累知识**：先指定“笔记研究分类”，在论文详情点击“阅读笔记 / 继续编辑”，按九个章节填写并保存。章节草稿使用“分析”任务模型，仅取本地摘要或缓存全文节选，确认追加后仍需保存；资源链接和公开邮箱使用本地提取。到知识库按类型与分类找回记录、提炼基础知识或技能卡、导出 Markdown，再到智能体选择资料分类提问。对话文件夹只整理聊天，不决定资料范围。
5. **生成路线**：在知识库点击“AI 研究分析”，进入分析页后展开分类并勾选条目，填写研究目标和约束。规划只读取每条正文前 200 字符和最多 3 个研究点，长笔记可先另存简短规划摘要。先查看分析，再点击“保存为研究路线”；分析全文和知识关联一起保存，随后可生成路线和架构图。文本、视觉和出图可分别选用不同模型。

普通关键词检索不要求 LLM Key；学术源是否可用取决于各源政策、Key、额度与网络。需要 AI 的操作会调用你配置的模型，费用由相应提供商计收。

**想少看无摘要结果，或寻找写作参考？** 搜索后点击结果列表的“有摘要”；该筛选只作用于当前返回结果，同词重新搜索也会复位。打开论文详情的“相似论文”，选择“研究内容相近”“摘要结构参考”或“同刊相似论文”。推荐查询 OpenAlex 和 Crossref，不调用生成模型；“展开原始摘要”用于对照写法，“在本页查看与分析”可继续阅读。匹配分不是概率，同刊频次不是全刊统计，摘要缺失也不等于论文没有摘要。

**不知道智能体怎么用？** 普通知识库问答中先问“我该如何使用你”，再说明卡在哪一步。产品指导保留精简说明、最近对话及失败后的“再次调用 AI”，未返回用量表示未知，不等于免费；具体科研问题仍需要真实材料。要逐段读论文，从论文详情点击“原文伴读”，按页选择任务。[本版操作与验收](docs/reports/v1.2.13-guided-reading-acceptance.zh-CN.md)

## 产品预览

截图展示产品工作流；点击可查看原尺寸。中英文与明暗主题均可在应用中切换。

<table>
<tr>
<td width="50%" valign="top"><strong>多源检索与论文详情</strong><br><a href="docs/assets/screenshots/search-results-zh.png"><img src="docs/assets/screenshots/search-results-zh.png" width="100%" alt="论文检索"></a></td>
<td width="50%" valign="top"><strong>研究分析</strong><br><a href="docs/assets/screenshots/knowledge-analysis-zh.png"><img src="docs/assets/screenshots/knowledge-analysis-zh.png" width="100%" alt="知识库分析"></a></td>
</tr>
<tr>
<td width="50%" valign="top"><strong>个人知识库</strong><br><a href="docs/assets/screenshots/knowledge-zh.png"><img src="docs/assets/screenshots/knowledge-zh.png" width="100%" alt="知识条目"></a></td>
<td width="50%" valign="top"><strong>研究路线与架构图</strong><br><a href="docs/assets/screenshots/route-zh.png"><img src="docs/assets/screenshots/route-zh.png" width="100%" alt="研究路线"></a></td>
</tr>
</table>

<details>
<summary>更多界面：主页、模型设置、英文深色模式</summary>

点击查看完整截图，避免长图占用首页空间。

[主页](docs/assets/screenshots/home-zh.png) · [模型设置](docs/assets/screenshots/settings-zh.png) · [English / dark](docs/assets/screenshots/search-results-en-dark.png)

</details>

## 你能用它做什么

| 场景 | 已有能力 |
| --- | --- |
| 复杂文献检索 | 查询分解、多源并行召回、去重、条件过滤与综合排序；相关度和引用影响力分别展示 |
| 论文阅读 | 授权 PDF 导入、指定页与选段伴读、段落翻译、图表/公式解释、基础练习；页码与文件版本回溯；可拖宽的侧栏 |
| 期刊与写作参考 | 主动发现相关期刊、核对指标口径、该刊相关论文；2–5 篇本地全文的有界写作样例对照，确认后保存为现有技能卡 |
| 个人研究积累 | 分类阅读笔记、基础知识与技能卡；九章节、个人进度、按需草稿、Markdown 导出；复用研究路线和结构化架构图 |
| 有来源的问答 | BM25，或可选 Embedding + RRF；提供文献/片段定位、引用检查和工具记录 |
| 多模型使用 | 主模型、备用模型及任务配置；统计供应商返回的 Token，控制超时与重试 |
| 文献工具联动 | 检测本机 Zotero、读取集合和导入元数据；论文页可显式请求导出，写入能力由本机接口实际决定 |

**回答有边界**：引用检查验证来源编号与逐句覆盖，不能证明每个结论都被原文支持。证据不足会提示补充材料；模型失败或引用修订未通过时返回有来源的原文摘录，明确说明原因。

**缓存有边界**：搜索结果、临时分析和本页“笔记研究分类”选择不作为永久搜索历史，离开搜索页时清理；最近检索词只保留在当前窗口会话。已保存笔记、知识条目与智能体对话另行保留；未保存表单不是自动归档，界面清理也不等于删除服务端文件缓存。

## 配置与连接

- [可选本机文字模型配置](docs/local-model.zh-CN.md)：独立运行环境与模型副本，仅用于智能体文字任务；不把权重打进安装包，不等于全流程离线。包含私有配置、资源边界、启停与回滚，真实桌面效果需单独验收。
- [API Key 申请入口与配置说明](docs/API_PROVIDERS.md)：GLM、千问 / 百炼、硅基流动、MiMo、SenseNova、OpenAI、Ollama 等。
- **Zotero**：先启动 Zotero，在“设置 → 高级”打开“允许此计算机上的其他应用程序与 Zotero 通讯”，然后到 ScholarNova 设置中检测并选择集合。读取成功不等于写入授权；写入失败时请按提示使用标准文献导出。
- **当前源码的 Zotero 同步**：个人文库的文件夹通过完整层级确认，写入后回读核验目标。当前只保证论文元数据，不保证 PDF 附件同步；群组库、同路径同名文件夹或未确认的写入会明确报错，请勿盲目重复点击。
- **学校图书馆**：搜索页提供门户与检索词入口；从论文详情进入可绑定论文，在独立直连窗口自行登录、识别 PDF、核对下载身份并确认导入。不会代登录、绕过订阅或批量下载；不兼容的登录、内网地址和网页使用外部浏览器后手动导入。学校认可的 VPN/WebVPN 仍需用户按学校要求准备。
- **期刊指标**：相关期刊中开放指标保留来源且不冒充 JIF；JCR / 中科院分区及正式 JIF 来自有权使用的本地导入文件。JIF CSV 最小示例为 `Journal,JIF,JIF Year,Source`，记录如 `目标期刊名,5.4,2025,本人授权数据来源`；该数字只是格式示例，不能作为真实期刊指标。
- **代理与网络**：不同 API、图书馆和本机 Zotero 的网络需求不同。确保 `localhost`、`127.0.0.1` 不走代理；按实际报错检查地址、模型权限、额度与网络。

## 本地数据与隐私

数据位于 Windows 的 `%APPDATA%/scholarnova-desktop` 或 macOS 的 `~/Library/Application Support/scholarnova-desktop`。迁移或备份前退出应用，并复制完整用户数据目录。Key 保存在本机后端配置，不写入浏览器持久存储、公开仓库或安装包；当前配置文件不是系统钥匙串加密存储，请保护好备份。

调用云模型会把当前操作所需的问题和选中材料发给所选提供商；Key 也会随认证请求发送到所配置的服务地址。使用第三方或自定义地址前应核对来源与隐私政策。

## 开发、架构与验证

React + TypeScript 构建界面，Electron 提供桌面外壳，FastAPI 编排本地服务，SQLite 保存数据。多源搜索、PDF 解析、模型路由和检索增强问答分别实现，便于替换提供商与排查问题。

- [源码部署与开发](docs/DEVELOPMENT.zh-CN.md)
- [项目大白话与开发学习手册](docs/PROJECT_EXPLAINED.zh-CN.md)：从一次实际使用讲清技术、RAG、模型切换、本地千问、出图和源码学习；区分当前实现与后续计划。
- [Windows / macOS 打包、测试和版本发布](docs/desktop-release.zh-CN.md)
- [FTI 流水线架构](docs/FTI_PIPELINE_ARCHITECTURE.zh-CN.md) · [产品路线图](docs/AI_APPLICATION_ROADMAP.zh-CN.md)
- [历史 v1.2.1 改进与验收报告](docs/reports/v1.2.1-consumer-readiness.zh-CN.md) · [版本记录](CHANGELOG.md)
- [历史真实 API 全流程测试（2026-09-08）](docs/reports/2026-09-08-live-api-validation.zh-CN.md)：区分模型完成、规则降级、图像质量与未验证项；后续修复见版本记录，不包含在 v1.2.1 安装包中。

历史 Asta 18 题验证子集 F1 为 **0.341379**；66 题中具备二元标签的 27 题 F1 为 **0.283713**。这不是完整竞赛成绩，也不表示超过其他数据集上的 SPAR。此次产品修复不把历史分数当成新版本测评结果。[原始评测报告](outputs/competition-benchmark-report-2026-07-02.md)

## 反馈与参与

欢迎通过 [Issues](https://github.com/wei9719/ScholarNova/issues) 提交使用问题，附上系统版本、应用版本、操作步骤和脱敏错误信息。不要附上 API Key 或私有论文。开发贡献见 [CONTRIBUTING](CONTRIBUTING.md)，安全反馈见 [SECURITY](SECURITY.md)。

## 开源许可

项目自有代码继续采用 [MIT 许可证并保留原作者声明](LICENSE)。包含 PyMuPDF / MuPDF 的完整桌面发行组合按 GNU AGPL v3 的适用条款提供；其他独立组件保留各自许可声明。不添加“仅非商业使用”限制。详见 [第三方声明](THIRD_PARTY_NOTICES.md) 与 [桌面发行记录](docs/DESKTOP_LICENSE_REVIEW.md)。

每个已发布桌面版本应在安装包旁提供 `ScholarNova-版本-corresponding-source.zip` 与 `SHA256SUMS.txt`，源码归档中的 `SOURCE_MANIFEST.json` 记录来源，应用资源目录 `legal/` 保存逐包许可声明。当前下载情况以 Release 实际附件为准；GitHub 自动生成的“Source code”仓库 ZIP 不等同于完整依赖源码包。模型服务、学术数据和论文仍遵循各自条款。
