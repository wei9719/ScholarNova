<p align="center"><img src="docs/assets/scholarnova-cover-en.svg" width="800" alt="ScholarNova — AI Research Workspace"></p>
<p align="center"><a href="https://github.com/wei9719/ScholarNova/releases/tag/v1.2.5">Download / 下载</a> · <a href="README.md">English</a> · <a href="README.zh-CN.md">简体中文</a> · <a href="CHANGELOG.md">Changelog</a></p>
<p align="center"><a href="https://github.com/zhangweiguo9719-web/ScholarNova/actions/workflows/ci.yml"><img src="https://github.com/zhangweiguo9719-web/ScholarNova/actions/workflows/ci.yml/badge.svg" alt="CI"></a> <a href="https://github.com/zhangweiguo9719-web/ScholarNova/actions/workflows/desktop-release.yml"><img src="https://github.com/zhangweiguo9719-web/ScholarNova/actions/workflows/desktop-release.yml/badge.svg" alt="Desktop builds"></a> <a href="LICENSE"><img src="https://img.shields.io/badge/Own_source-MIT-d4a84f" alt="项目自有源代码：MIT"></a> <a href="docs/DESKTOP_LICENSE_REVIEW.md"><img src="https://img.shields.io/badge/Desktop_combination-AGPLv3-536b82" alt="桌面发行组合：AGPLv3"></a></p>

# ScholarNova

一个面向个人研究者的桌面科研工作台：**找论文 → 读全文 → 存知识 → 基于证据问答 → 规划研究路线**。支持中文 / English、明暗主题和自带模型 API Key（BYOK）。

## 下载与安装

**[v1.2.5 已发布](https://github.com/wei9719/ScholarNova/releases/tag/v1.2.5)**：提供 Windows x64、Intel Mac、Apple Silicon Mac 安装包、Windows 便携版，以及同版本源码和 SHA256 校验文件。新增失败指导原位重试，保留历史用量，精简模型上下文，并改善便携版解压启动。详见 [发布与验收报告](docs/reports/v1.2.5-assistant-retry.zh-CN.md)。

到 **[GitHub Releases 下载已发布安装包](https://github.com/wei9719/ScholarNova/releases/latest)**，展开 Assets，按你的电脑选择。v1.2.5 已通过三个桌面平台的自动构建和启动检查。

**社区桌面版采用已选择的 AGPL 开源发行路线。** 每个版本须包含逐包许可声明和相应源码，并通过自动源码检查及 Windows / macOS 启动测试后才能发布。详见 [发行要求](docs/DESKTOP_LICENSE_REVIEW.md)；可下载版本以同版本 Release 的实际附件为准，旧安装包不会自动包含最新源码改动。

| 你的电脑 | 选择文件 | 安装方法 |
| --- | --- | --- |
| Windows 10/11，64 位 | `ScholarNova-Setup-版本-x64.exe` | 运行安装向导，创建桌面与开始菜单快捷方式 |
| Windows 免安装 | `ScholarNova-Portable-版本-x64.exe` | 放在固定目录后双击运行 |
| macOS 13+，Apple Silicon（M 系列） | `ScholarNova-版本-arm64.dmg` | 拖入“应用程序” |
| macOS 13+，Intel | `ScholarNova-版本-x64.dmg` | 拖入“应用程序” |

桌面版自带界面、本地后端和 SQLite，无需安装 Python、Node.js 或 Docker。首次启动可能因解压运行时稍慢。升级前退出应用，安装新版本；个人数据位于用户目录，不在安装目录内。

Windows 日常使用优先选择 **Setup 安装版**。v1.2.5 便携版调整解压方式后，本机完整启动检查两次为 74.50 / 63.47 秒，均通过；此前两次超过 120 秒。新包下载体积较大，且便携版仍需解压，不能保证所有电脑快速启动。

本版要求 macOS 13 或更新系统，依据 [Electron 44 官方平台要求](https://www.electronjs.org/blog/electron-44-0)。

**签名状态：当前构建未配置商业代码签名与 Apple 公证。** Windows 可能显示 SmartScreen 提示；macOS 可能需要在“系统设置 → 隐私与安全性”允许打开。仅在确认下载来源和校验值后操作，不需要关闭系统安全保护。正式零干预安装体验仍需要发布者完成签名与公证。

## 第一次使用

1. **设置模型**：打开“设置”，选择 GLM、千问、硅基流动或其他已支持厂商，填写自己的 Key 和该平台可用的模型名称，测试后保存。无需更改代码。
2. **搜索论文**：输入研究问题。界面显示检索耗时、真实数据源、结果数量和来源状态；同一关键词可以再次搜索。
3. **分析全文**：选择论文并分析。找不到开放全文时，可上传自己有权使用的 PDF；界面会区分全文、摘要及实际读取的图表页。
4. **积累知识**：将分析整理保存为知识条目，再在“智能体”中按研究方向建立文件夹与独立对话。
5. **生成路线**：从知识库进行研究分析，再生成研究路线和架构图。文本、视觉和出图可分别选用不同模型。

普通关键词检索不要求 LLM Key；学术源是否可用取决于各源政策、Key、额度与网络。需要 AI 的操作会调用你配置的模型，费用由相应提供商计收。

**不知道智能体怎么用？** 先问“我该如何使用你”，再追问“那下一步呢”，并告诉它你卡在哪一步。v1.2.5 源码使用精简产品说明与最近对话调用你选定的智能体模型，模型阶段最多等待 60 秒、输出上限 320 Token。最新指导失败时可点击“再次调用 AI”：只替换失败回答，保留前次请求与用量记录。未返回用量表示未知，不等于免费；具体科研问题仍需要真实材料。下载请查看 Release 的实际版本，旧安装包不会自动更新。[本轮验收报告](docs/reports/v1.2.5-assistant-retry.zh-CN.md)

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
| 论文阅读 | 正文、章节、表格、图注和可用图表页；本地 PDF 导入；标题中文翻译；可拖宽的侧栏 |
| 个人研究积累 | 知识条目、研究路线、结构化架构图；按研究方向组织智能体对话 |
| 有来源的问答 | BM25，或可选 Embedding + RRF；提供文献/片段定位、引用检查和工具记录 |
| 多模型使用 | 主模型、备用模型及任务配置；统计供应商返回的 Token，控制超时与重试 |
| 文献工具联动 | 检测本机 Zotero、读取集合和导入元数据；论文页可显式请求导出，写入能力由本机接口实际决定 |

**回答有边界**：引用检查验证来源编号与逐句覆盖，不能证明每个结论都被原文支持。证据不足会提示补充材料；模型失败或引用修订未通过时返回有来源的原文摘录，明确说明原因。

**缓存有边界**：搜索结果和临时分析在离开搜索页时清理，最近检索词只保留在当前窗口会话；知识条目与智能体对话属于用户保存的资料。

## 配置与连接

- [可选本机文字模型配置](docs/local-model.zh-CN.md)：独立运行环境与模型副本，仅用于智能体文字任务；不把权重打进安装包，不等于全流程离线。包含私有配置、资源边界、启停与回滚，真实桌面效果需单独验收。
- [API Key 申请入口与配置说明](docs/API_PROVIDERS.md)：GLM、千问 / 百炼、硅基流动、MiMo、SenseNova、OpenAI、Ollama 等。
- **Zotero**：先启动 Zotero，在“设置 → 高级”打开“允许此计算机上的其他应用程序与 Zotero 通讯”，然后到 ScholarNova 设置中检测并选择集合。读取成功不等于写入授权；写入失败时请按提示使用标准文献导出。
- **当前源码的 Zotero 同步**：个人文库的文件夹通过完整层级确认，写入后回读核验目标。当前只保证论文元数据，不保证 PDF 附件同步；群组库、同路径同名文件夹或未确认的写入会明确报错，请勿盲目重复点击。
- **学校图书馆**：提供门户跳转和检索词传递。统一身份认证、校园网或学校认可的 VPN 仍由用户完成，当前不自动代登录或批量下载订阅全文。
- **期刊分区**：开放引用指标可直接展示；JCR / 中科院分区需要导入有授权且注明年份的数据。缺失显示未知，不能用引用次数猜分区。
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

欢迎通过 [Issues](https://github.com/zhangweiguo9719-web/ScholarNova/issues) 提交使用问题，附上系统版本、应用版本、操作步骤和脱敏错误信息。不要附上 API Key 或私有论文。开发贡献见 [CONTRIBUTING](CONTRIBUTING.md)，安全反馈见 [SECURITY](SECURITY.md)。

## 开源许可

项目自有代码继续采用 [MIT 许可证并保留原作者声明](LICENSE)。包含 PyMuPDF / MuPDF 的完整桌面发行组合按 GNU AGPL v3 的适用条款提供；其他独立组件保留各自许可声明。不添加“仅非商业使用”限制。详见 [第三方声明](THIRD_PARTY_NOTICES.md) 与 [桌面发行记录](docs/DESKTOP_LICENSE_REVIEW.md)。

每个已发布桌面版本应在安装包旁提供 `ScholarNova-版本-corresponding-source.zip` 与 `SHA256SUMS.txt`，源码归档中的 `SOURCE_MANIFEST.json` 记录来源，应用资源目录 `legal/` 保存逐包许可声明。当前下载情况以 Release 实际附件为准；GitHub 自动生成的“Source code”仓库 ZIP 不等同于完整依赖源码包。模型服务、学术数据和论文仍遵循各自条款。
