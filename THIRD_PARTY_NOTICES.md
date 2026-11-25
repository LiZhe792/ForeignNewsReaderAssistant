# 资源、依赖与 AI 使用说明

项目自己的 MIT 许可证不替代第三方许可证。此表记录直接使用的主要资源，安装包的完整依赖及许可证以对应发行版元数据为准；再分发时保留适用声明。

| 资源 / 组件 | 来源与版本 | 许可证 / 使用条件 |
| --- | --- | --- |
| 英汉词典 | [ECDICT](https://github.com/skywind3000/ECDICT)，revision `39cfb9672c0906b63569e0453c2e6abfb9ab182a`，12,000 个高频词及学习补充 | MIT，原声明保留在 `data/ECDICT-LICENSE.txt`；自动词形和扩展知识不是完整权威词典 |
| Flask、Werkzeug | Flask 3.1.2 / 其依赖 | BSD-3-Clause |
| Beautiful Soup | 4.14.2 | MIT |
| Python-Markdown | 3.10.2 | BSD-3-Clause |
| charset-normalizer | 3.4.4 | MIT |
| PyMuPDF | 1.26.7 | **AGPL-3.0 或商业许可证**，不能把带此组件的分发一概标为 MIT；按实际使用/再分发方式满足上游要求 |
| RapidOCR / PaddleOCR 模型 | rapidocr-onnxruntime 1.2.3 自带 PP-OCRv3/v2 模型 | Apache-2.0；OCR 结果需要校对 |
| edge-tts | 7.3.1 | LGPL-3.0；联网使用微软语音服务，遵守服务使用条件；生成音频不附带重新授权承诺 |
| openpyxl / genanki | 3.1.5 / 0.13.1 | MIT |
| ReportLab | 4.4.5 | BSD |
| FastEmbed | 0.9.1 | Apache-2.0 |
| 语义模型 | [BAAI/bge-small-en-v1.5](https://huggingface.co/BAAI/bge-small-en-v1.5)，由 FastEmbed 获取 Qdrant ONNX 量化版本 | 上游模型 MIT，下载时核对模型卡；英文检索为主 |
| faster-whisper / 语音模型 | faster-whisper 1.2.1；[Systran/faster-whisper-tiny.en](https://huggingface.co/Systran/faster-whisper-tiny.en)，源自 OpenAI Whisper | MIT；仅本机识别，不等于声学发音评分 |
| PyAV / FFmpeg | PyAV 11–15 兼容范围 | PyAV BSD；随 wheel 分发的 FFmpeg 组件遵守其 LGPL/GPL 构建许可 |
| pywebview / plyer | 可选桌面与通知依赖 | BSD / MIT，系统 WebView 运行时另按系统提供方条款使用 |
| 字体 | Georgia、Arial、Times New Roman、Consolas 等系统字体 | 仅调用本机字体，不分发字体文件 |
| 示例文章、练习、图标 | 项目内原创学习样例与现有 SVG 图标；PWA PNG 由 SVG 渲染 | 随项目源码；非授权转载的外刊全文 |
| 用户导入文章、录音、笔记 | 用户本地材料 | 权利归相应权利人；导入/小组分享由用户选择，不获得外刊再分发权 |
| DeepSeek / 可选 Responses 服务 | 用户自行配置的接口 | 各服务条款、额度与收费规则；密钥不进仓库，主动请求会发送所选文本 |

AI 使用范围：语境/翻译、句法/文章分析、问答、测验、写作、支架和学习闭环可调用模型。系统校验字段、原文范围、引文与重复请求，但并未完成大样本人工准确率评测。离线词典、SM-2、计时/统计、去重和分页不依赖生成式 AI。

本轮 AI 辅助开发实际参与需求核对、实现、测试与失败修复；未把自动化通过写作“人工已审查”，也未补造用户试验、真实 AI 质量评分或真人发音评分。具体开发变化见 CHANGELOG，人工验收由项目组后续补充。
