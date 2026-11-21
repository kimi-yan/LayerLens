# LayerLens 项目说明（给编码助手）

> 本文件与 `AGENTS.md` 内容必须完全一致。`CLAUDE.md` 给 Claude Code 读，`AGENTS.md` 给 Codex 等其他工具读。修改任何一个时，把同样的改动写进另一个，并在提交前用 `fc.exe CLAUDE.md AGENTS.md`（或 `diff`）确认两者没有差异。

LayerLens（层析镜）把一张扁平图片拆成透明图层，导出 PSD、按层 PNG ZIP 和合成 PNG。Windows、单张 NVIDIA 显卡、本机运行。用户界面和文档使用中文。

## 项目边界

仓库的主体是 Web 工作台和后端。两个推理引擎（Qwen-Image-Layered、See-through）由使用者按 `docs/engines.md` 自行安装，不入库。写代码时要保证：

- 没有任何引擎时，服务能启动，工作台、任务中心、已有结果的预览和导出都正常，只是不能新建该引擎的任务。
- 引擎是否可用一律以 `/api/v1/capabilities` 为准（`ready`、`missing_parts`、`setup`、`worker_alive`），前端不写死某个引擎一定存在。
- 引擎相关的版本号（上游提交、权重 revision、校验值、安装命令、磁盘需求）只写在 `engine/adapter/catalog.py`，其他地方从这里读取。

## 目录

| 路径 | 内容 |
| --- | --- |
| `frontend/` | `index.html` 是工作台页面，`src/studio-*.js` 分别处理状态、上传、任务中心和图层工作台，`api.js` 是请求边界，`workspace.js` 保存浏览器侧的图层调整，`studio.css` 配合 Tailwind |
| `backend/` | `app.py`（FastAPI）、`store.py`（SQLite 队列）、`worker.py`（单 GPU 串行与子进程）、`assets.py`（PSD/PNG/ZIP）、`engines.py` 与 `options.py`（引擎注册与参数 schema）、`test_*.py` |
| `engine/adapter/` | 每个引擎一个启动器（`command.py`、`qwen_command.py`）和运行脚本（`run.py`/`derive.py`、`qwen_run.py`），`runner_io.py` 负责握手与进度，`catalog.py` 是固定版本清单，`requirements-*.txt` 是引擎环境依赖 |
| 根目录 | `LayerLens 控制台.lnk` 双击入口（带 Logo，相对路径，随仓库分发；控制台在它缺失时自动补齐），`使用指南.html` 是面向使用者的图文指南，`Start-LayerLens.ps1` 是命令行启动方式 |
| `assets/` | `LayerLens.ico` 项目图标（控制台窗口与任务栏、自检 / 安装窗口标题栏），几何与工作台头部 SVG 一致 |
| `scripts/` | `control-panel.ps1` 控制台窗口（WinForms，PowerShell 5.1；状态轮询和启停都在后台 runspace，界面线程不做阻塞调用），`setup-engine.ps1` 安装引擎，`doctor.py` 自检，`verify_exports.py` 复验导出，`LayerLens 控制台.cmd` 备用双击入口（首跑自动补齐顶层快捷方式），`New-ConsoleShortcut.ps1` 生成带 Logo 的双击快捷方式，`engines/` 下载权重，`bench/` 测量显存与耗时 |
| `docs/` | `engines.md` 安装，`architecture.md` 架构与显存基线，`api.md` 接口与运维，`handoff.md` 交接记录 |

只在本机存在、不入库的目录：`.local/`（`backend-venv`、`qwen-venv`、`seethrough-venv`，`control-panel/` 是控制台编译缓存的界面 DLL）、`engine/see-through/`（上游代码，固定提交，不要修改）、`data/`（任务、权重、缓存，服务日志在 `data/logs/`）、`output/`、`.tmpfiles/`（临时文件）。`data/jobs/` 和 `data/cache/models/` 是用户数据，不要删除或改写。

## 常用命令

在项目根目录的 PowerShell 中执行：

```powershell
.\Start-LayerLens.ps1                                  # 启动服务 http://127.0.0.1:8765/，首次会建后端环境并构建前端
npm --prefix frontend ci; npm --prefix frontend run build   # 改前端后必须重建 frontend/dist
npm --prefix frontend run dev                          # Vite 热更新，/api 代理到 8765
& .\.local\backend-venv\Scripts\python.exe -m unittest -v backend.test_api backend.test_assets backend.test_qwen_split
python scripts\doctor.py                               # 硬件与引擎状态
.\scripts\setup-engine.ps1 -Engine qwen                # 安装引擎（qwen / seethrough），可重复运行
.local\backend-venv\Scripts\python.exe scripts\verify_exports.py <job_id>   # 复验一个任务的导出
```

后端测试用临时队列和假引擎，不需要权重或显卡。重启服务前先查 `/api/v1/task-center?filter=active`，有运行中的任务时不要重启，否则任务会记为中断。

## 约定

- Python：四空格缩进、`snake_case`；JavaScript：两空格、`camelCase`、ES modules。照着相邻代码的写法和注释密度来。
- API 改动用 `unittest` + FastAPI `TestClient` 覆盖，测试方法放在 `backend/test_*.py`。
- 前端改动要重建并在浏览器里实际操作一遍；可见的界面变化要截图确认。
- 推理或导出相关改动，用一张真实图片跑完整任务，检查进度、图层清单、PSD 和 ZIP，再用 `verify_exports.py` 复验。
- GPU 任务保持串行。See-through 固定使用 Group Offload；Qwen 只开放在 RTX 4070 12 GB 上实测过的参数，图层数 6–8 例外（界面标“不建议”并在提交前确认）。基线数据见 `docs/architecture.md`。
- 界面和文档里不要写“本机”这类只适用于开发机的说法；显存和耗时以“RTX 4070 12 GB 参考基准”表述。
- 文档和界面文案用平实的中文，直接说明做什么、怎么做，不用营销腔和套话。
- PowerShell 脚本要能在 Windows PowerShell 5.1 下运行：含中文时保存为 UTF-8 with BOM，字符串里不要用中文弯引号，原生命令的 stderr 用脚本里现成的 `Invoke-Native` 包装处理。
- 新增或升级引擎依赖时，同步更新 `catalog.py`、`requirements-*.txt` 和 `docs/engines.md`。
- 使用方式、控制台或安装流程有变化时，同步更新 `使用指南.html`；控制台界面变化后重新截图 `docs/images/control-panel.png`。

## 提交

`engine/see-through/` 有自己的历史且已列入 `.gitignore`，不属于本仓库。提交信息用简短的动作描述，例如 `Handle empty head crops during layer inference`；说明行为变化和验证方式，界面改动附截图。
