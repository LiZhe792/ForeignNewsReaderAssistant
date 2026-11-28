# ForeignNewsReaderAssistant

**最终完成版 · 2026-11-28**（Git 标签：`final-2026-11-28`）。

**Margin 知刊 · 外刊学习助手**，面向外刊阅读、英语学习与个人知识积累，支持在流畅阅读与深入学习之间切换。

Python + Flask + SQLite，原生 JavaScript 界面。用 PyCharm 直接运行 `app.py`；日常使用无需 Node.js、外部数据库或批处理脚本。项目源码采用 MIT，第三方组件各自遵守其许可证，见 [资源与许可说明](#第三方资源来源与许可证声明)。

## 项目简介

项目以文章为学习起点，把内容获取、阅读查词、生词复习、听说训练、阅读测验、写作输出和学习记录放在同一个学习空间中。文章、词汇与笔记保留来源关联，练习结果进入学习记录，用户可据此选择下一次阅读或复习任务。

主要使用流程：**导入材料 → 流畅阅读 → 精读理解 → 收藏与整理 → 复习和练习 → 查看反馈 → 调整计划与继续学习**。基础阅读与离线词典可在本机使用；AI、在线朗读、模型下载和跨设备服务需按对应条件配置。

- [功能说明](#功能入口与需求对应) · [使用方式](#使用方式)
- [技术栈](#技术栈) · [运行环境与启动步骤](#运行环境与启动步骤)
- [第三方资源来源与许可证声明](#第三方资源来源与许可证声明) · [AI 工具使用声明](#ai-工具使用声明)
- [项目开发文档（Word）](项目开发文档.docx)：完整功能闭环、模块与数据关系、接口、部署和验收说明。
- [演示视频（高清 MP4，约 32 分 49 秒）](https://github.com/LiZhe792/ForeignNewsReaderAssistant/releases/download/submission-2026-11-28/%E6%BC%94%E7%A4%BA%E8%A7%86%E9%A2%91.mp4)：完整项目演示，保留 2558 × 1528 分辨率与 30 fps，下载后播放。

## 技术栈

| 层次 | 技术与职责 |
| --- | --- |
| 服务端 | Python 3.11 / 3.12、Flask 3.1.2；应用工厂与业务 Blueprint 提供页面和 JSON API |
| 前端 | HTML、CSS、原生 JavaScript；模块化交互、响应式阅读、Service Worker 与浏览器本地存储；无需前端编译 |
| 数据 | SQLite；文章、词汇、练习、计划等按账户隔离；FTS5 支持全文检索 |
| 内容处理 | Beautiful Soup、Python-Markdown、charset-normalizer；PyMuPDF 与 RapidOCR 用于 PDF / OCR |
| 学习能力 | 本地词典、SM-2 复习调度、FastEmbed 语义向量、可配置 AI 接口 |
| 音频 | edge-tts 在线朗读；可选 faster-whisper 本机识别；浏览器录音 |
| 导出与桌面 | openpyxl、ReportLab、genanki；可选 pywebview 窗口与 plyer 通知 |
| 验证 | Python unittest、Node.js 原生测试运行器、Playwright Chromium、GitHub Actions |

## 运行环境与启动步骤

### 环境要求

- 推荐 Python 3.11 或 3.12，使用独立虚拟环境。Windows 可直接使用 PyCharm；其他系统可按下列命令启动，系统依赖和可选组件需按平台安装。
- 浏览器推荐较新的 Chrome / Edge；浏览器测试以 Chromium 为主。录音需要麦克风授权，PWA 与完整离线能力需要 localhost 或 HTTPS。
- 首次安装依赖、在线导入、AI 和在线 TTS 需要网络。本机模型需要额外磁盘空间，首次使用前可显式下载。
- Node.js 22 用于开发测试，日常运行不需要。原生桌面入口在 Windows 上需要 WebView2 Runtime。

### 命令行启动

在项目根目录执行。Windows PowerShell 无需激活环境也可运行：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe doctor.py
.\.venv\Scripts\python.exe app.py
```

已有虚拟环境时跳过创建步骤，使用其解释器安装和运行。macOS / Linux 可使用：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python doctor.py
.venv/bin/python app.py
```

启动后访问 <http://127.0.0.1:5000>。关闭服务可在终端按 `Ctrl+C`。端口占用时设置环境变量 `PORT` 后重新启动；PowerShell 示例为 `$env:PORT = '5001'`。

### PyCharm 启动

1. 打开项目，选择 Python 3.11 或 3.12 虚拟环境；已有环境可选择 `.venv/Scripts/python.exe`。
2. 在该解释器的终端执行：

   ```sh
   python -m pip install -r requirements.txt
   ```

3. 右键 `app.py` → Run。浏览器访问 <http://127.0.0.1:5000>。
4. 可先运行 `doctor.py` 检查依赖、磁盘、数据目录与端口。端口占用时，在 PyCharm 运行配置中设置 `PORT=5001`。

`.run/Margin.run.xml` 提供共享运行配置。`.env.example` 是环境变量参考，应用**不会自动读取 `.env`**。不要把密钥写入源码。首次启动会建立学习库及原创示例文章；后续启动保留学习记录。

| 安装项 | 命令 / 作用 |
| --- | --- |
| 基础环境 | `python -m pip install -r requirements-core.txt`：阅读、词典、账户、SQLite、网页与文本导入；不包含 OCR、语义模型、复杂导出等可选库 |
| 常规全部扩展 | `python -m pip install -r requirements.txt`：增加 PDF/OCR、语义搜索、在线 TTS、Excel/PDF/Anki 导出 |
| 本机语音识别 | `python -m pip install -r requirements-speech.txt` |
| 原生桌面窗口 | `python -m pip install -r requirements-desktop.txt`，然后运行 `desktop.py`；Windows 需要 WebView2 Runtime |
| 浏览器关闭后提醒 | `python -m pip install -r requirements-background.txt`，然后另行运行 `background.py` |

以上 `python` 均指当前项目虚拟环境解释器。基础安装适合先体验核心流程；需要扩展功能时安装对应依赖，安装完整依赖也不会自动下载本机 AI 模型。

## 使用方式

1. **获取文章**：在导入窗口粘贴网址或正文、上传文件，先检查预览中的标题、来源与正文，再保存。需要持续更新时添加 RSS / Atom 订阅。
2. **阅读与理解**：打开文章，设置主题、字号和阅读模式。流畅阅读时先暂存待查词，段后集中处理；精读时使用查词、翻译、句法分析和问答。
3. **积累词汇与知识**：把词语连同原句加入生词本，将重点句、批注和主题笔记整理为知识卡或表达库，保留回到原文的位置。
4. **练习与反馈**：完成今日间隔复习；按需进行听写、跟读、阅读测验或写作训练，查看错题、反馈与修订对照，再安排补练。
5. **跟踪学习**：查看学习日历、计划和统计报告，结合错题与遗忘记录调整任务；在学习闭环工具中进行读后回忆、词汇迁移和同主题任务包学习。
6. **保存与迁移**：按需导出学习材料或创建备份。离线使用前下载文章；跨设备使用时连接同一个服务并登录同一账户。

独立写作页面为 `/writing`，支持本地草稿、登录后同步、继续编辑、冲突处理和会话退出。它与文章关联的写作训练用途不同：写作训练保留题目与 AI 反馈，独立写作页面管理通用草稿。

## 功能入口与需求对应

五项基础功能均在主界面：文章导入与阅读、查词、生词本、阅读进度与日历、参考难度。

| 文档编号 | 功能 | 入口 |
| --- | --- | --- |
| 01 | RSS/Atom、媒体/栏目/关键词订阅、更新、批量链接、剪贴板、浏览器收藏扩展 | 订阅页、导入窗口、`browser-extension/` |
| 02 | TXT/MD/HTML/PDF/DOCX/EPUB、图片与扫描 PDF OCR、拖拽、预览校正 | 导入 → 文件 |
| 03 | 分类标签、收藏待读、专题合集、优先级、批量管理、归档回收站、URL/正文去重、来源元数据 | 文章库、账户与工具 → 合集 |
| 04 | 全文/语义搜索、来源/标签/等级/长度/时长/状态组合筛选、搜索片段、相似文章 | 文章库搜索 |
| 05 | 字体字号、宽度行距段距、主题、专注全屏、单双栏、编号目录、快捷键与触屏手势 | 阅读设置与操作帮助 |
| 06 | 流畅/精读、折叠释义、待查词、段后集中处理、提示密度、掌握词弱化、位置保持、阶段提醒 | 阅读工具栏 |
| 07 | 多词典、12,000+ 离线词条、词形/短语、搭配、同反义词、易混词、词根词族、词频等级、音标 | 查词卡片；扩展词汇知识覆盖以词库收录为限 |
| 08–09 | AI 语境释义、多义辨析、语气指代、水平分层、词句段全文翻译、原译对照、直译意译、修订收藏 | 学习工具 → AI 语境 / 翻译 |
| 10–11 | 主干从句/非谓语/特殊句式、指代逻辑、拆句/语法树/简化、摘要结构/论据/背景/修辞/立场/事实观点、多文对比 | 学习工具 → 句法 / 文章分析 |
| 12 | 选区/全文问答、词义句法追问、原文依据、苏格拉底引导、读前/读后问题、历史收藏 | 学习工具 → 文章问答 |
| 13 | 词书标签、批量导入导出、词形合并、多原句多义项、图片/音频卡、笔记模板、短语句型 | 我的生词 |
| 14 | SM-2 调度、每日到期与配额、正反卡/挖空/听音/拼写、遗忘重学、困难词、暂停恢复 | 间隔复习 |
| 15 | 词句段全文朗读、音色口音语速、暂停继续、点击句子、高亮跟随、循环、缓存、播放列表 | 学习工具 → 朗读 |
| 16 | 盲听/原文、听写/填空/差异、精听循环、录音对照、影子跟读、透明反馈与记录 | 听说训练 |
| 17 | 理解选择、判断、段落匹配、完形、词义、排序、主旨、推断、限时、错题错因、阶段测评 | 阅读测验 |
| 18 | 仿写、目标词造句、改写/扩写/简化、互译、摘要观点续写、语法纠错、建议、修改对照 | 学习工具 → 写作训练 |
| 19 | 多色高亮、书签批注、句子/语法收藏、主题笔记标签、双向定位、表达库、知识卡/导图 | 学习工具 → 标注、知识整理 |
| 20 | 词汇覆盖、词汇/句法/背景维度、自测、兴趣/时长推荐、递进路径、同主题不同难度 | 难度与推荐、账户与工具 → 学习闭环 |
| 21–22 | 日周/专题目标、拆分、提醒、连续记录补签成就总结、速度/时长/词汇/查词/复习/遗忘趋势、周月年报告 | 学习计划、学习足迹、学习数据分析 |
| 23 | 文章/词典/音频离线、离线记录同步、备份恢复/历史/定期备份、设备迁移、缓存管理 | 离线与数据管理、离线阅读 |
| 24 | CSV/Excel/JSON/Markdown/PDF、Anki、学习报告、阅读清单、Obsidian/Notion 导入包、只读数据接口 | 导出中心 |
| 25 | 独立账户、同服务多端同步、冲突处理、手机/PWA/桌面、合集分享、小组/教师任务/学生进度/权限 | 账户与工具、`server.py`、`desktop.py` |
| 26 | 键盘/焦点/语义标签、高对比与辅助标记、减少动画、中英文控件、多语言正文、时区日期格式 | 语言与显示、账户与工具 → 日期与语言 |

新增学习闭环：阅读干扰预算建议（可忽略/撤销）；读后回忆 → 三个候选要点的 AI 证据核对 → 补练；已学词的新语境迁移与确认安排复习；按真实错题/遗忘记录选择短练习；三级渐退句子支架；三篇同主题顺序任务包；三表达迁移写作及 AI 用法核对。入口在「拓展学习」和「账户与工具 → 学习闭环」。

## AI、语音与模型

本节介绍应用运行时的服务配置；开发辅助工具声明见文末。

「账户与工具 → AI 与显示」保存 DeepSeek 设置，问答、写作、语境、翻译、句法、测验和学习闭环共用。密钥只存当前本机/账户私有文件，不发给浏览器。显式设置的旧 `MARGIN_AI_*` Responses 接口配置仍优先；界面显示实际配置状态。

只有主动生成才发出 AI 请求，可能计费。引文位置和字段结构会校验，但 AI 判断仍需结合原文核对；不把词频候选要点或自评当成标准答案。离线模式会拦截新联网调用。自动化测试使用受控 AI 响应，不消耗真实付费额度。

安装语音依赖后，运行以下命令显式下载模型：

```sh
python setup_models.py --speech
python setup_models.py --semantic
```

语音模型默认放在 `instance/whisper-tiny.en`（或 `MARGIN_DATA_DIR`）；可通过 `MARGIN_WHISPER_MODEL` 指定。语义模型默认在用户目录 `.cache/margin-models/semantic`，可用 `MARGIN_MODEL_DIR` 更改根目录；首次主动使用语义搜索也会尝试下载。初始化服务不自动下载模型。

跟读词级反馈来自本机 faster-whisper：提供识别文本、时间戳、置信度和错词回听。**不是经声学验证的音节/重音发音评分**；未识别词不会伪造时间点。TTS 使用在线 edge-tts，成功生成后可缓存；真实麦克风需浏览器授权。

英语词典与 Flesch 等级以英语为主。非英语文章可阅读、标注、记录词汇和使用 AI，等级显示 N/A。自动语言仅作脚本识别，拉丁文字默认英语；法语/德语/西语请显式选择。中英文导航和常用控件已支持，部分详细学习说明及 AI 提示仍用中文。

## 账户、多端与离线

未登录时使用本机学习空间。账户注册后拥有独立学习库，旧本机数据不会自动搬入账户；需要时用学习备份迁移。文章、词汇、SRS、笔记、练习均隔离，旧页面在账户变化后会拒绝写入。

跨设备需要**连接同一个运行中的学习服务**并登录同一账户，不是 GitHub 同步数据库。受信任局域网/自有服务器可运行：

```sh
python server.py --host 0.0.0.0 --hostname reader.example.org --tls-cert cert.pem --tls-key key.pem
```

在运行配置设置私有 `MARGIN_SIGNUP_CODE`；设备必须信任 HTTPS 证书并能访问主机。此入口适合小组演示/受控部署；生产公网部署需另配 WSGI 服务、反向代理、证书和运维。未在真实异地设备或所有浏览器上完成验收。

在支持的浏览器中用「安装阅读助手」或浏览器菜单安装 PWA；iOS 可使用“添加到主屏幕”。HTTPS 或 localhost 才有完整离线能力。先在文章末尾「保存到离线阅读」，再从 `/offline` 打开；浏览器完全断网时仍可读已下载文章、查离线词典、记录词和笔记。恢复连接后点击同步。进度冲突需选择本机或服务端；不同账户的离线队列分开保存。私有 API 响应不写入 Service Worker 公共缓存；浏览器存储仍依赖本设备保护。

浏览器扩展：Chrome/Edge → 扩展管理 → 开发者模式 → 加载已解压的扩展 → 选择 `browser-extension/`。点击扩展，把当前网页链接打开到学习助手预览。它不读取账号密码、不自动抓取或上传页面全文，也没有发布到商店。

## 数据、备份与恢复

默认目录为 `instance/`，启动时打印实际数据库路径。可设置 `MARGIN_DATA_DIR` 或 `MARGIN_DATABASE`。数据库、密钥、录音和本机模型均被 Git 忽略。

- 界面的学习备份包含当前学习库所有模块；恢复前校验、创建救援备份，并保护其他标签页不继续提交旧数据。账户身份、小组及独立写作库不属于这份单库备份。
- 整实例迁移使用 `backup.py`。**先停止应用和后台进程**，因为多个 SQLite 库的快照并非跨库原子事务。备份含配置密钥，按私有文件保管。

```sh
python backup.py backup --source instance --output margin-private-backup.zip
python backup.py restore margin-private-backup.zip
python backup.py restore margin-private-backup.zip --into restored-instance
```

不带 `--into` 只校验 SHA-256 与结构；恢复目标必须是新目录。自定义放在 instance 外的数据库或模型目录需要另外备份。数据库升级前自动留存一致快照；失败会恢复旧库。删除生词后提供 10 分钟撤销，撤销也恢复相关卡片与复习安排。

后台提醒：另运行 `background.py`，浏览器关闭后仍可检查提醒和每日学习库备份；进程和电脑需要保持运行。可以 `--database` 指定某个本机账户库，`--once` 便于自行配置系统任务，`--console` 仅输出文字。不会自动安装开机服务或系统计划任务。

### 启动入口速查

| 文件 | 使用场景 |
| --- | --- |
| `app.py` | 本机完整学习服务，默认端口 5000 |
| `demo.py` | 使用独立临时学习目录演示，默认端口 5001 |
| `server.py` | 同一服务的跨设备学习；非本机地址需配置 HTTPS 与允许的主机名 |
| `writing_server.py` | 仅提供账户写作服务，默认端口 5443；不开放本机阅读库 |
| `desktop.py` | 使用原生窗口打开学习助手 |
| `background.py` | 独立运行提醒和定期学习库备份 |
| `doctor.py` / `setup_models.py` | 环境诊断 / 按需下载语音与语义模型 |
| `backup.py` | 整实例备份、校验和恢复到新目录 |

独立写作服务可用 `python writing_server.py` 在本机启动。对外服务同样需要 `--hostname`、`--tls-cert`、`--tls-key`，可用 `MARGIN_WRITING_SIGNUP_CODE` 设置注册口令。它的身份与草稿存储位置可通过 `MARGIN_WRITING_DATABASE` 指定。

## 导入与性能

网页先预览再保存；文件识别后支持纠错。批量导入逐条显示结果，可停止后续链接并单独重试失败项；进行中的链接先完成，成功结果保留。URL 规范化与正文指纹统一去重，同标题不同正文不会被误丢弃。付费、登录和动态加载网页可改用粘贴正文；只导入有权使用的内容。

文章库每页 24 篇、生词本每页 50 条，支持继续加载。全文搜索使用可重建的 SQLite FTS5 旁路索引和增量更新；缺失 FTS5/短查询回退扫描。语义向量按正文块指纹复用，初次建立仍可能较慢。

一万篇约 2.7 KB 样本的本机实测：关键词热缓存中位耗时约 946 → 71 ms，Python 峰值分配约 42 → 0.15 MB。不是全进程内存，也不代表首次索引时间或所有设备。没有引入会破坏选区、朗读和锚点的长文虚拟化；按收益评估优化，不承诺“绝对最优”。

## 验证与演示

```sh
python -m unittest discover -s tests -v
node --test tests/test_frontend.cjs tests/test_speech_player.cjs
npm install --no-save playwright@1.58.2
npx playwright install chromium
node --test --test-concurrency=3 tests/*browser.cjs
```

PowerShell 可用 `$browserTests = Get-ChildItem tests/*browser.cjs | Select-Object -ExpandProperty FullName`，然后 `node --test --test-concurrency=3 @browserTests`。`READING_TEST_PYTHON` 可指定项目解释器，`READING_TEST_CHROMIUM_MODULE` 可指定测试浏览器适配模块。Node.js 仅用于开发测试。CI 在 Linux 上运行 Python、Node 与浏览器检查，不调用真实 AI。

运行 `demo.py` 可创建全新的独立演示目录并在 5001 端口启动，真实学习库保持原样。建议用同一篇文章演示：导入预览 → 流畅阅读暂存词 → 精读查词收藏 → 段后整理/标注 → 今日 SRS → 学习日历 → 离线重载和同步。需要 AI 的部分另配本机密钥并主动生成。

现有验证覆盖受控流程、手机窄屏/200% 字体、OCR 样本、本机语义向量与合成语音识别、桌面窗口加载；历史结果见下方版本与验证记录。真实 AI 解释质量、真人发音有效性、人工标注、阅读对照试验、Safari/Firefox 和跨实体设备仍需人工/外部验收，不能用自动测试通过代替学习效果结论。

架构：`app.py` 负责应用工厂与公共入口，`newsreader/` 为 Flask 模块及 SQLite 业务，`static/` 为独立前端模块，`templates/` 为页面，`data/` 为词典与原创样例，`tests/` 为临时数据库/受控网络测试。`extension_records` 保存带版本的扩展实体，账户库共享登录服务而不共享学习数据。

### 版本与验证记录

- **2026-11-28 最终完成版**（`final-2026-11-28`）：清理重复导入、无引用代码、旧版生词界面与过期材料，统一 AI 配置示例；优化浅色、护眼、深色、高对比度及悬停、禁用状态的按钮配色；调整 CI 的 Chromium 路径。
- 该版本记录的验证结果：473 项 Python 测试通过；23 项 Node 逻辑测试与 180 项浏览器用例首轮共通过 201/203，两项音频超时用例单独复跑通过。76 个页面与状态组合完成 2640 次按钮对比度测量，最低 4.64:1；批量管理与回收站恢复完成验证。
- **2026-11-25 功能整合**：补齐账户隔离、多端、PWA 离线、小组与教师任务、桌面、扩展、独立写作、学习闭环和后台提醒；完善导入、分页、全文索引、撤销与备份恢复。该阶段记录为 473 项 Python、180 项浏览器、24 项 Node 测试；首次完整浏览器回归为 170/177，修复后复验。OCR 四行样本初次提取两行，调整缩放后提取四行。

以上为相应版本的历史验收记录。当前改动应按实际范围重新执行检查，记录环境、失败及复验情况；GitHub CI 结果以对应提交的 Actions 为准。

## 开发与维护

从最新 `main` 建立功能分支，通过 Pull Request 提交完整差异；说明问题、改动后的行为、用户入口、实际验证和数据影响。同步主分支时保留双方有效改动，避免复制旧公共文件覆盖其他功能或强制推送共享分支。

新增学习表须同时检查初始化与迁移、账户隔离、备份恢复、删除引用、旧页冲突和离线行为；新增接口限制输入、使用参数化 SQL，并核对原文引用。AI 待分析材料不能覆盖系统规则。新增依赖更新依赖清单及下方许可声明。

测试使用临时数据库与受控响应，不使用个人密钥或真实学习记录。小修改运行相关检查，跨模块变更运行完整回归；问题反馈提供脱敏复现材料。`instance/`、虚拟环境、IDE 私有文件、密钥、录音、模型与备份不进入 Git。

`data/build_dictionary.py` 用于从本机 ECDICT 检出重建词库，命令为 `python data/build_dictionary.py /path/to/ECDICT`。它保留来源版本和许可，不属于闲置业务文件；运行前应确认上游版本与预期词库一致。

## 第三方资源来源与许可证声明

项目自有源码按 [MIT 许可证](LICENSE) 发布。第三方代码、模型、字体、在线服务和用户导入材料保留各自的权利与使用条件，**不能把整个安装环境统称为 MIT**。

| 资源 | 来源 | 许可证与说明 |
| --- | --- | --- |
| 离线英汉词典 | [ECDICT](https://github.com/skywind3000/ECDICT)，revision `39cfb9672c0906b63569e0453c2e6abfb9ab182a`；12,000 个高频词及学习补充 | MIT；随项目保留 [原始许可](data/ECDICT-LICENSE.txt)；词形与扩展知识不等于完整权威词典 |
| Web 与文本处理 | [Flask 3.1.2 / Werkzeug](https://palletsprojects.com/)、[Beautiful Soup 4.14.2](https://www.crummy.com/software/BeautifulSoup/)、[Python-Markdown 3.10.2](https://python-markdown.github.io/)、[charset-normalizer 3.4.4](https://github.com/jawah/charset_normalizer) | 分别为 BSD-3-Clause、MIT、BSD-3-Clause、MIT |
| PDF 与 OCR | [PyMuPDF 1.26.7](https://pymupdf.readthedocs.io/)、[RapidOCR 1.2.3](https://github.com/RapidAI/RapidOCR) 及自带的 [PaddleOCR PP-OCRv3/v2 模型](https://github.com/PaddlePaddle/PaddleOCR) | PyMuPDF 为 **AGPL-3.0 或商业许可**；RapidOCR 及所用 OCR 模型按 Apache-2.0；OCR 结果需校对 |
| 在线朗读 | [edge-tts 7.3.1](https://github.com/rany2/edge-tts) | LGPL-3.0；联网使用微软语音服务，另遵守服务条款；生成音频不附带重新授权承诺 |
| 表格与学习导出 | [openpyxl 3.1.5](https://openpyxl.readthedocs.io/)、[ReportLab 4.4.5](https://www.reportlab.com/)、[genanki 0.13.1](https://github.com/kerrickstaley/genanki) | MIT、BSD、MIT |
| 语义搜索 | [FastEmbed 0.9.1](https://github.com/qdrant/fastembed)、[BAAI/bge-small-en-v1.5](https://huggingface.co/BAAI/bge-small-en-v1.5) | 工具 Apache-2.0；上游模型 MIT，使用 Qdrant ONNX 量化版本；以英文检索为主，下载时核对模型卡 |
| 本机语音识别 | [faster-whisper 1.2.1](https://github.com/SYSTRAN/faster-whisper)、源自 OpenAI Whisper 的 [tiny.en 模型](https://huggingface.co/Systran/faster-whisper-tiny.en)、[PyAV 11–15](https://pyav.org/) | 识别库及模型 MIT；PyAV BSD，随 wheel 分发的 FFmpeg 依实际构建遵守 LGPL / GPL；识别反馈不等于声学发音评分 |
| 桌面与通知 | [pywebview 5.4–6.x](https://pywebview.flowrl.com/)、[plyer 2.1.0](https://github.com/kivy/plyer) | BSD / MIT；WebView 与系统组件遵守对应条款 |
| 字体与示例素材 | 本机系统字体；项目内示例文章、练习与 SVG 图标 | 字体文件不随项目分发；原创示例及项目图标随源码许可；用户导入内容权利归对应权利人 |

上表集中记录主要资源的来源、版本和使用限制；直接依赖以 `requirements*.txt` 为准，间接依赖以实际发行版元数据为准。对外发布或再分发前需保留相应许可声明。系统字体不随项目分发，PWA PNG 图标由项目 SVG 渲染；外刊导入与小组分享不自动获得文章转载权。

## AI 工具使用声明

**开发辅助工具：豆包。** 辅助范围包括需求梳理、方案讨论、代码与文档整理、问题定位及测试思路梳理。AI 输出属于开发参考，项目维护者负责确认需求、审查实现、运行验证，并对最终代码与文档负责；AI 参与不代表已完成全部人工验收。

开发辅助声明与应用运行时服务配置分别说明：当前应用按用户设置调用 DeepSeek 或兼容 Responses 的服务，语音识别与语义检索使用上表列出的真实模型；这些资源的来源和许可证保持原始名称。

应用只在用户主动生成时发送相关文本。用户需自行配置接口和额度，核对返回内容与原文，不把 AI 反馈视为已验证的事实或教学效果。自动化测试使用受控响应，不冒充真实服务质量评测，也不上传个人密钥。

运行时 AI 可用于语境、翻译、句法、文章分析、问答、测验、写作、支架和学习闭环；离线词典、SM-2、计时统计、去重与分页不依赖生成式 AI。接口按提供方服务条款、额度和收费规则使用。
