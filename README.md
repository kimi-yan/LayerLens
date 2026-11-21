# LayerLens · 层析镜

把一张扁平图片拆成可编辑的透明图层，导出 PSD、按层 PNG 和合成图。面向 Windows 和单张 NVIDIA 显卡，在本机运行，图片不会上传到外部服务。

拆层由两个可选的推理引擎完成：

- **通用拆层**（Qwen-Image-Layered）：海报、商品图、插画、场景。按画面元素拆成 3–8 个图层，自动补全被遮挡的背景，可以对单个图层再细拆。
- **二次元角色拆层**（See-through V3）：角色立绘。按头发、五官、服饰等部位拆成约 17–23 层，附带深度 PSD，支持左右拆分和前后拆分。

## 仓库里有什么

本仓库只包含 Web 工作台、后端和引擎适配层。模型权重、推理环境和 See-through 上游代码体积大、和硬件相关，由安装脚本在每台机器上单独准备，不入库。

```
frontend/            Web 工作台（Vite + 原生 JS + Tailwind）
backend/             FastAPI 服务：任务队列（SQLite）、单 GPU 串行执行、PSD/ZIP 导出
engine/adapter/      引擎适配层：启动器、运行脚本、固定版本清单 catalog.py、依赖清单
scripts/
  control-panel.ps1  控制台窗口（WinForms，由顶层快捷方式或 scripts\LayerLens 控制台.cmd 打开）
  New-ConsoleShortcut.ps1  生成带 Logo 的顶层双击快捷方式
  LayerLens 控制台.cmd  备用双击入口（顶层快捷方式缺失时用它启动一次即自动补齐）
  setup-engine.ps1   安装一个推理引擎
  doctor.py          检查硬件和引擎状态
  verify_exports.py  从运行中的服务下载结果并校验
  engines/           权重下载与 NF4 转换（由 setup-engine.ps1 调用）
  bench/             显存与耗时测量脚本
docs/                安装、架构、API 和交接文档
LayerLens 控制台.lnk  顶层双击入口（带 Logo，随仓库分发）
Start-LayerLens.ps1  启动服务（命令行方式）
使用指南.html         面向使用者的图文指南

以下目录只在本机生成，不入库：
.local/              Python 环境（后端与各引擎）
engine/see-through/  See-through 上游代码
data/                任务、模型权重、下载缓存和服务日志（data/logs）
output/              测量与审阅输出
```

## 快速开始

不熟悉命令行的话，双击项目顶层的 **`LayerLens 控制台.lnk`**（带项目 Logo 的快捷方式，随仓库自带，项目放在哪个文件夹都能用；新克隆时它显示的可能是 PowerShell 图标，双击打开一次控制台后就会换成 Logo；丢失时双击一次 `scripts\LayerLens 控制台.cmd` 会自动补回来）：在窗口里点「启动服务」，没装的引擎旁边点「安装」，再点「打开工作台」。完整说明见 [使用指南.html](使用指南.html)。

下面是命令行方式。需要 Windows 10/11、Python 3.11 或更高版本（后端用，测试环境 3.13）和 Node.js 20.19 或更高版本（构建前端用）。

```powershell
.\Start-LayerLens.ps1
```

第一次运行会创建 `.local/backend-venv`、安装后端依赖并构建前端，然后在 http://127.0.0.1:8765/ 提供工作台，API 文档在 `/docs`。换端口用 `-Port 9000`。

这时还没有推理引擎：工作台可以打开，新建任务里两个引擎都显示“未安装”，并给出安装命令。按需安装一个或两个：

```powershell
.\scripts\setup-engine.ps1 -Engine qwen         # 通用拆层，下载约 28 GB
.\scripts\setup-engine.ps1 -Engine seethrough   # 二次元角色拆层，下载约 13 GB
```

装好后刷新页面即可。硬件要求、手动安装步骤、下载源和常见问题见[推理引擎安装](docs/engines.md)。随时可以运行 `python scripts/doctor.py` 查看缺什么。

## 开发

```powershell
npm --prefix frontend ci
npm --prefix frontend run build      # 重建 frontend/dist，由后端直接提供
npm --prefix frontend run dev        # Vite 热更新，/api 代理到 8765 端口的后端
& .\.local\backend-venv\Scripts\python.exe -m unittest -v backend.test_api backend.test_assets backend.test_qwen_split
```

后端测试使用临时队列和假引擎，不需要权重或显卡。测试依赖 `httpx2`（starlette 1.10 的 TestClient 需要它），手动重建后端环境时执行：

```powershell
python -m venv .local\backend-venv
.local\backend-venv\Scripts\python.exe -m pip install -r backend\requirements.txt httpx2==2.16.2
```

改了推理或导出相关的代码，用真实图片跑一次任务，再用 `scripts/verify_exports.py <job_id>` 检查导出文件。

## 文档

- [使用指南](使用指南.html)：图文版的上手步骤、控制台、引擎安装和工作台用法，附可直接交给 Agent 的提示词。用浏览器打开即可。
- [推理引擎安装](docs/engines.md)：硬件要求、一键安装、手动步骤、下载源、常见问题。
- [架构](docs/architecture.md)：模块边界、引擎适配契约、两个模型的运行方案与显存基线。
- [API 与运维](docs/api.md)：接口、引擎参数、任务生命周期、文件位置和排查。
- [开发交接](docs/handoff.md)：验收记录和已知限制。
- [CLAUDE.md](CLAUDE.md)：给 Claude Code、Codex 等编码助手的项目说明，与 `AGENTS.md` 内容相同。

## 许可

本项目采用 [Apache-2.0](LICENSE) 许可。

## 上游项目

- [Qwen-Image-Layered](https://huggingface.co/Qwen/Qwen-Image-Layered)（Apache-2.0）及 [unsloth 的 GGUF 量化版](https://huggingface.co/unsloth/Qwen-Image-Layered-GGUF)（Apache-2.0）。
- [See-through](https://github.com/shitagaki-lab/see-through)，代码为 Apache-2.0，权重的许可以其 Hugging Face 页面为准。

使用和分发时请同时遵守各上游项目的许可。
