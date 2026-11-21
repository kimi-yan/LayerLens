# LayerLens 开发交接

## 接手基线

- 从 [README](../README.md) 启动或构建，从 [CLAUDE.md](../CLAUDE.md)（与 AGENTS.md 相同）了解协作约定，引擎安装见 [engines.md](engines.md)。`engine/see-through/` 由安装脚本克隆、已列入 `.gitignore`，有自己的 Git 历史，不属于本仓库。
- 界面设计的落地源码在 `frontend/index.html` 和 `frontend/src/studio*.js`，改动界面时保持现有设计语言。
- See-through 与 Qwen-Image-Layered 均已在 RTX 4070 12 GB 参考环境上实机验收，Web 端提交、排队、推理、预览、导出与递归细拆全链路可用。

## 2026-11-21 控制台重做

- 原因：旧版在界面线程里调 HTTP、WMI 和 `taskkill`，点按钮后窗口会卡几秒；按钮偏小，等待时没有反馈。
- 结构：状态轮询放在后台 runspace（启动中 0.7 秒一次，其余 1.5 秒），结果写进同步哈希表，界面计时器只读快照；启动、停止、重启也在后台 runspace 执行，用代号（Gen）丢弃过期快照。找端口占用进程改用 `netstat`（新 runspace 里加载 CIM 要好几秒），停止从约 8 秒降到约 1 秒。只有端口上应答的是 LayerLens 时才结束占用进程，否则提示「无法停止」。
- 界面：自绘控件（圆角按钮、卡片、状态点、标签、进度条）用 C# 写在脚本里，`Add-Type` 编译后缓存到 `.local/control-panel/LayerLensUi-<哈希>.dll`，源码变化时重新编译并删除旧 DLL。所有过渡状态（正在检查、正在启动、正在停止、引擎安装中、正在推理）显示转圈；按钮忙碌时不响应点击。窗口按 DPI 缩放，小屏幕会缩短日志区。
- 修复：服务 stderr 日志是 CLIXML，现在解析成纯文本；引擎运行时文字过长换行，改成单行省略并在悬停时显示全文；再次双击会把已打开的窗口切到前面；安装窗口开着时再点「安装」会切过去；非默认端口的控制台标题带端口号，日志写到 `data/logs/server-<端口>.log`，不再和 8765 的日志混在一起。
- 验证（PowerShell 5.1，RTX 4070）：正在检查、已停止、正在启动、运行中、正在停止、端口被占用（用 `python -m http.server 8799` 占端口，`-Port 8799` 打开控制台）都截图确认；点「启动服务」后 1.6 秒服务可用，点「停止服务」0.9 秒完成；服务运行时关闭窗口弹出三选一，选「否」后窗口关闭、服务继续运行，重新打开控制台能识别运行中的服务；第二次启动同端口控制台会退出并激活已有窗口。「启动失败」「运行异常」和引擎安装中的转圈没有实际触发。

## 2026-11-21 控制台与使用指南

- 新增 `LayerLens 控制台.cmd`（根目录，双击）和 `scripts/control-panel.ps1`（WinForms 窗口）：启动 / 停止服务，显示状态（已停止、正在启动、运行中、启动失败、运行异常、端口被占用）、工作台地址、进行中任务、显卡显存、两个引擎是否就绪；未就绪的引擎可一键打开安装窗口；另有打开日志、环境自检、打开项目文件夹。服务以隐藏的 PowerShell 运行 `Start-LayerLens.ps1`，输出写到 `data/logs/server.log`；停止时用 `taskkill /T` 结束本项目的 uvicorn 进程树（含引擎子进程），有未完成任务时先确认；关闭窗口时可选择停止服务或保持后台运行；同一端口只允许一个控制台。
- 面向使用者的指南移到根目录 `使用指南.html`（原 `docs/guide.html` 删除），新增“三分钟上手”和控制台说明，截图在 `docs/images/control-panel.png`。
- 验证：在 PowerShell 5.1 下双击打开控制台，读取运行中服务的状态；通过窗口按钮停止服务（端口释放）和再次启动（十几秒内恢复运行中，日志正确显示）；修复了停止时 `taskkill` 对已结束子进程报错导致弹窗的问题，以及引擎状态行的取值错误。“启动失败”“端口被占用”两种状态和首次启动（无后端环境）没有实际触发验证。

## 2026-11-21 前后端与推理引擎分离

目标是让仓库只包含 Web 工作台、后端和适配层，引擎由使用者按文档自行安装，没装引擎时界面也能正常使用。

- 新增 `engine/adapter/catalog.py`，集中记录上游提交、权重 revision、LaMa 校验值、安装命令、下载量与磁盘占用。适配器、下载脚本、`scripts/doctor.py` 和 `/capabilities` 都从这里读取，升级版本只改这一处。
- 原来放在 `.local/`（不入库）的脚本移到 `scripts/`：`setup-engine.ps1`（新增，一条命令装好一个引擎，可重复运行）、`doctor.py`（新增，检查硬件与引擎）、`verify_exports.py`、`engines/`（下载与 NF4 转换，公共下载逻辑合并到 `hub.py`，See-through 下载同时取 LaMa）、`bench/`（测量脚本）。
- See-through 环境从 `.local/engine-venv` 改名为 `.local/seethrough-venv`，与 `qwen-venv` 对应；新增 `engine/adapter/requirements-seethrough.txt`（cu126 PyTorch + 上游 requirements）。`engine/see-through/` 改为安装脚本克隆、`.gitignore` 排除。
- `/capabilities` 每个引擎新增 `missing_parts` 和 `setup`。前端：新建任务里未安装的引擎标“未安装”，选中时隐藏参数区，显示缺什么、安装命令（可复制）、下载量和文档位置；只装了一个引擎时默认选它。顶栏状态显示“未安装推理引擎”或“任务进程已停止”，浮窗说明已有结果不受影响。空工作台、任务中心的重试、工作台的二次拆分与“原参数重跑”在引擎不可用时禁用并说明原因。
- 启动脚本在没有任何引擎时打印提示；README 重写，新增 [engines.md](engines.md)，新增 `CLAUDE.md` 并与 `AGENTS.md` 保持一致。
- 验证：后端测试 25 项通过（新增未安装引擎的分组与安装信息）；`setup-engine.ps1` 在 PowerShell 5.1 下对两个引擎各跑一遍（已装环境全部跳过，See-through 权重重新逐个校验通过）；两个下载脚本对现有 40 GB 权重校验通过；`doctor.py` 在完整与空工作区下输出正确；浏览器中模拟两个引擎都未安装，检查了顶栏、浮窗、新建任务、空工作台和任务中心。

## 2026-11-21 双模型接入与验收

**改动**

- 能力接口按引擎上报就绪状态、缺失文件、分辨率、参数 schema、导出格式、运行方案和二次拆分动作，顶层旧字段只描述默认引擎 See-through。前端的参数界面、GPU 浮窗、二次拆分按钮和导出卡片全部依据这些字段渲染。
- 运行脚本拆成 `runner_io.py`（握手与进度）、See-through 的 `run.py`/`derive.py` 和 Qwen 的 `qwen_run.py`，各自校验产物。Qwen 产物允许原图比例画布，不含深度 PSD。
- Qwen 参数界面去掉了无效的“背景补全”和“量化模式”控件（模型总会补全被遮挡背景，没有开关；本机只部署了 Q4_K_M），改为层数、步数、画面描述，以及高级参数中的种子、CFG、图注语言、负向描述和 CFG 归一化，每个控件都会传入管线。
- 界面以能力命名引擎：Qwen 显示为“通用拆层”（蓝色，排在左侧并作为新建任务的默认引擎），See-through 显示为“二次元角色拆层”（玫紫色），模型名只作副标题和悬停提示（`studio-state.js` 的 `engineName` / `engineModel`）。新建任务的上传区改为带主按钮的明显上传入口。全局用一个样式化浮层（`studio.js` 的 `app-tip`）显示所有 `title` 以及被截断的 `.truncate` / `.clamp-2` 文本。
- 新建任务弹窗每次打开都恢复 `index.html` 中写明的默认值（引擎、层数 5、步数 30、画面描述留空），不再保存参数草稿；只有“用当前参数新建”会带入所选任务的参数。界面文案不再写“本机”，显存与耗时统一以“RTX 4070 12 GB 参考基准”表述。
- Qwen 图层显示为“第 n 层”“第 3 层 › 子层 2”。参数快照展示画布尺寸、权重版本、实际图注与重组误差。
- 交互修复：重试、二次拆分与下载防重复点击；下载前先检查响应，结果缺失时提示而不是保存错误页；新建任务的参数草稿在刷新后保留（图片无法恢复）；项目下拉支持 Esc 关闭并维护 `aria-expanded`；GPU 浮窗可键盘聚焦；窄屏（≤480 px）顶栏不再遮挡“任务中心”。
- `Start-LayerLens.ps1` 修复：Windows PowerShell 5.1 下重定向输出时，uvicorn 写到 stderr 的日志不再触发终止错误。
- 后端测试改用 `httpx2`，消除 starlette 弃用警告；测试从 14 项增加到 21 项，覆盖多引擎能力、Qwen 参数边界、无深度导出、递归细拆与失败、Qwen 产物契约和崩溃后的启动恢复。
- 新增许可明确的样图集 `data/samples/`（`manifest.json` 记录来源、许可与 sha256）：See-through 上游角色图 5 张、Qwen 官方测试图 13 张（均 Apache-2.0），以及 3 张为本项目生成的商品测试图。

**实机验收（全部通过浏览器在 8765 端口完成）**

| 项目 | 结果 |
| --- | --- |
| See-through 1024 px、30 步 | Web 提交，5 分 55 秒完成，17 层；`verify_exports.py` 通过（PSD、深度 PSD、ZIP 18 个文件，重组差值 0） |
| Qwen 640 px、4 层、30 步（咖啡海报） | Web 提交并在 See-through 之后串行执行，7 分 25 秒完成，736×544 画布，显存峰值 9.47 GiB；导出校验通过，深度视图与深度 PSD 入口隐藏 |
| Qwen 递归细拆（L3，3 子层） | 从工作台提交，6 分 34 秒完成，得到 `L3.1`、`L3.2`，空子层省略；导出校验通过 |
| 排队取消、运行中取消、重试 | 排队中的 Qwen 任务从任务中心取消；运行中的 See-through 任务取消后子进程退出、显存释放、`work/` 被清理；从界面重试（双击只产生一个新任务），5 分 52 秒完成 19 层，导出校验通过 |
| 刷新重连 | 运行中刷新页面，任务进度、GPU 浮窗中的当前任务与引擎状态正确恢复 |
| 工作台交互 | 图层选择、显隐、独显、透明度、原图对比、蒙版、多选上限提示；前端调整不影响下载的 PSD |
| 图层移动 | 仅「分层合成」视图。移动工具(V)按 Photoshop 自动选择 / Figma 逻辑：按在当前层包围盒内拖当前层，否则点选光标下最上层不透明图层并拖动；Ctrl/⌘ 按住只拖当前层；铺满画布的底层不参与点选（需在列表选中）。悬停蓝框预览将选中的层。平移：H、按住空格、中键、普通滚轮；Ctrl+滚轮按光标缩放，Ctrl+0 复位。拖拽中 Esc 取消；Shift 锁轴；方向键 1/10px；Ctrl+Z / Ctrl+Shift+Z(Y)。偏移存 localStorage（`offsetX/offsetY`），合成 PNG 含位移，PSD/ZIP 保持原坐标 |
| 项目切换与任务中心 | 顶栏下拉列出 `/api/v1/jobs` 返回的全部已完成任务（后端上限 80 条），按日期分组、显示完整时间，可搜索文件名/日期，↑↓ Enter 选择；更早的任务从任务中心打开。任务中心时间为 `YYYY-MM-DD HH:mm:ss`，运行中行有进度条与百分比，排队显示位次 |
| 回收站与永久删除 | 对测试中取消的任务执行回收、二次确认后永久删除 |
| 下载失败 | 不存在的结果显示提示，不下载错误页 |
| 窄屏与浅色主题 | 420 px 宽度下单栏布局无横向溢出 |

压测数据和效果观察见[架构文档](architecture.md#qwen-image-layered-参考基线rtx-4070-12-gb)。

## 2026-11-21 Qwen 复核

- 递归细拆：咖啡海报 L4 是整幅 19% 不透明的色调层，模型把它画成不透明纸纹，重组误差升到 107。新增子层还原校验与色调层跳过（`fit_children`、`is_overlay`），失败时任务记为 `SPLIT_REJECTED`、父结果不变；参数快照显示每个细拆层的还原误差。复测：咖啡 L4 被拒绝，Robot L5 拆成标题、副标题、绿机甲（误差 2.63 / 5.51，整图 7.11），竖版咖啡海报 L3 拆出杯子与纸袋 Logo（4.24 / 5.54）。
- 量化、步数、层数与耗时对照见[架构文档](architecture.md#qwen-image-layered-参考基线rtx-4070-12-gb)；结论是维持 Q4_K_M、640 px，默认 5 层；6–8 层允许用户自选，界面标注不建议并在提交前确认。Q6_K 与 Q8_0 已下载到 `data/cache/models/qwen-image-layered-gguf/`（约 37 GiB），没有收益，可删除。
- 工作台在 901–1180 px 隐藏顶栏引擎标签与画布 HUD 尺寸信息，≤900 px 改为单栏，1024 px 下的画布 HUD 不再被裁切。

## 2026-11-21 控制台对话框改绘

- 控制台原来用系统 `MessageBox` 做二次确认（关闭控制台、停止有任务在跑的服务、安装引擎、普通提示），样式和主界面不一致。现在统一走 `Show-Dialog`：`control-panel.ps1` 的 C# 库新增 `DialogForm`（无边框白卡、细描边、系统投影、整面可拖动），标题、正文和圆形语气图标自绘，按钮复用 `RoundButton`。语气分 info / question / warn / error 四种底色。
- 关闭控制台的三个选项改为「取消」「停止服务并退出」「仅关闭控制台」，默认回车落在「仅关闭控制台」，Esc 和 Alt+F4 落在取消；停止确认的按钮是「取消」「仍要停止服务 / 仍要重新启动服务」，默认回车是取消。按钮行过宽时对话框自动加宽。
- 验证方式：8799 端口起一个只返回 capabilities / task-center / system 的假后端，开测试控制台，用 `PrintWindow` 逐种弹窗截图核对；Esc 取消、安装确认取消、提示确认、「停止服务并退出 → 仍要停止服务」全链路（假后端被结束、端口释放、窗口退出）都实测通过。主界面未改动，`docs/images/control-panel.png` 不需要重截；新增 `docs/images/control-panel-dialog.png` 并在指南「停止和关闭」一节引用。
- 重复双击控制台时的提示仍是系统 `MessageBox`：它发生在界面库编译之前，保持原样。

## 2026-11-21 项目图标与任务栏标识

- 项目图标在 `assets/LayerLens.ico`（16–256 多尺寸）：墨色圆角底（#22241F）+ 柠檬绿（#C8F169）线式三层堆叠，几何与工作台头部 SVG（`frontend/index.html` 的 polygon + 两条 opacity 0.65 / 0.35 折线，stroke 1.8 圆头）完全一致。生成脚本 `.tmpfiles/make-logo.py`（Pillow）；`control-panel.ps1` 的 `Draw-Logo` 用同一套 24 viewBox 坐标自绘，改 Logo 时三处一起改。最初放过根目录、造型为纸层加镜头，均已替换。
- 控制台窗口与任务栏按钮使用该图标。进程启动时调用 `SetCurrentProcessExplicitAppUserModelID("LayerLens.ControlPanel.<端口>")` 建立独立任务栏分组——不设的话 Explorer 按 powershell.exe 分组并沿用进程图标，窗口图标再对也没用。头部标记与 ico 同几何，ico 缺失时回退到自绘位图。
- 启动时隐藏只属于本进程的黑控制台（`GetConsoleProcessList` 计数为 1 才隐藏；在共享终端里手动跑脚本不会隐藏用户的终端），任务栏不再出现 PowerShell 按钮。
- 自检、安装窗口在窗口出现后由 UI 定时器轮询授予 `WM_SETICON`，标题栏带 Logo。这两个控制台窗口的任务栏按钮仍是系统控制台图标：Windows 控制台宿主的任务栏图标取自进程映像，实测 `WM_SETICON`、向窗口属性 store 写 AppUserModelID、隐藏再显示都改不了，属系统限制。
- 修了一个真实 bug：`New-Object` 参数模式里 `$Size - 1` 这类裸减法不算算术（抛 op_Subtraction，头部标记画成红叉并弹未处理异常框），`Draw-Logo` 的计算实参全部加括号。
- `docs/images/control-panel.png` 用真实服务重截（双引擎就绪、默认端口）；指南正文未改。
- `.cmd` 的 Explorer 图标按文件类型统一分配，单文件改不了（改注册表会波及全机所有 .cmd，不采用）。按用户选择新增 `scripts/New-ConsoleShortcut.ps1`：生成「LayerLens 控制台.lnk」，`IconLocation` 指向 `assets\LayerLens.ico`，参数与 .cmd 一致（含 `-WindowStyle Hidden`），`-Desktop` 可加放桌面；搬家后重跑修复绝对路径。实测双击约 1 秒出窗口、Explorer 显示 Logo。随后按用户要求把 `.cmd` 移入 `scripts/`（`%~dp0` 改同目录），顶层只留 `.lnk` 作双击入口；控制台启动时检测顶层缺快捷方式就自动调用该脚本补齐，新克隆首跑内层 .cmd 也能恢复入口。指南「停止和关闭」一节与 README 有说明。

## 2026-11-21 顶层快捷方式随仓库分发

- 顶层 `LayerLens 控制台.lnk` 改为相对路径并提交入库：目标仍是系统 `powershell.exe`，`-File` 参数、工作目录（`.`）和图标（`assets\LayerLens.ico`）都相对 `.lnk` 所在目录解析，克隆到任何位置双击可用。`.gitignore` 的 `*.lnk` 规则加例外放行顶层这一个。
- `scripts/New-ConsoleShortcut.ps1` 分两种模式：项目根目录内的快捷方式生成相对路径版（控制台检测到缺失时自动补齐也走这条，重复生成不产生仓库差异）；`-Desktop` 桌面快捷方式在项目目录之外、相对路径解析不到，仍记录绝对路径，项目文件夹搬家后重跑修复。
- 验证：删除顶层 `.lnk` 后运行脚本重新生成，读回属性确认目标、参数、工作目录和图标均为相对；双击后控制台窗口正常打开（服务运行中、双引擎就绪），随后关闭窗口、后台服务不受影响。
- 文档同步：README 仓库结构与快速开始、使用指南「三分钟上手」「停止和关闭」「文件清单」、CLAUDE.md / AGENTS.md 目录表。

## 2026-11-21 快捷方式图标丢失修复

- 根因：上一轮把 `.lnk` 的 `IconLocation` 改成相对路径 `assets\LayerLens.ico`。资源管理器解析快捷方式图标时按它自己的进程工作目录（`C:\Windows\System32`）解析相对路径，不是按 `.lnk` 所在目录，找不到文件就显示空白/默认图标。对照实验：同一个纯红测试 ico，绝对路径版快捷方式显示红色，相对路径版不显示。启动参数和工作目录相对写法不受影响（启动时 Shell 按 `.lnk` 目录解析），所以“能双击启动但没图标”。
- 方案：仓库里继续分发相对版（`New-ConsoleShortcut.ps1 -ForRepo` 生成），保证克隆后双击可用；控制台每次启动读取快捷方式的 `IconLocation`，与当前项目的 `assets\LayerLens.ico` 绝对路径不一致（首次克隆、项目搬家、文件缺失）时调用脚本重建为绝对图标路径，并对该文件设 `git update-index --skip-worktree`，本机路径不进仓库差异；脚本末尾调用 `SHChangeNotify` 刷新资源管理器图标缓存。没有采用自带 .exe 启动器：未签名的小 exe 拉起 `powershell -ExecutionPolicy Bypass` 容易被杀毒软件误报。
- 维护者要更新仓库里的 `.lnk` 时运行 `-ForRepo`（会取消 skip-worktree）再提交，之后启动控制台会自动改回本机绝对路径。
- 验证：重建后通过 `SHGetFileInfo` 取到的图标为项目 Logo；`git clone` 到临时目录模拟新克隆，自愈前检测为需要修正、自愈后图标为该目录的绝对路径，`git status` 不出现 `.lnk`。

## 2026-11-21 快捷方式双击无反应修复

- 根因：前两节“启动参数和工作目录相对写法不受影响”“克隆到任何位置双击可用”的结论是错的。双击快捷方式时资源管理器按它自己的工作目录（System32）解析相对路径，不是按 `.lnk` 所在目录：相对的 `-File` 参数找不到脚本，PowerShell 静默退出，表现为双击没反应；相对图标不显示是同一原理。探针快捷方式对照确认：相对 `-File`（空工作目录）和相对 `.cmd` 目标都拉不起进程。仓库分发的占位版和当时本机自动重建的版本启动路径都是相对的，所以双击全部失效；上一轮的自愈只修图标不修启动路径，而且自愈要等控制台启动后才跑，坏快捷方式又拉不起控制台，形成死循环。
- 修复：`New-ConsoleShortcut.ps1` 本机模式改为启动参数、工作目录、图标全写绝对路径；`-ForRepo` 仍生成相对占位版供仓库分发（占位版双击无效，首次启动后改写）。新增 `-IfNeeded` 开关：读回现有快捷方式，启动参数、工作目录、图标三项都指向当前项目就直接跳过，否则重建并设 skip-worktree。`Start-LayerLens.ps1` 启动时内联调用 `-IfNeeded`，`control-panel.ps1` 启动时用隐藏子进程调用（内层 `.cmd` 经由控制台走同一条路），所以新克隆或搬家后通过内层 `.cmd` 或命令行启动一次即修复顶层入口。没有改分发方案为 exe 启动器，理由同上一节（杀毒误报）。
- 验证：本机重建后经 explorer 真实打开快捷方式，控制台窗口正常出现（标题「LayerLens 控制台」，进程命令行含绝对 `-File` 路径）；`git clone` 到临时目录模拟新克隆，占位版被 `-IfNeeded` 改写为指向克隆目录的全绝对路径，重复调用不重写文件，`git status` 不出现 `.lnk`；三个脚本 PowerShell 5.1 语法解析零错误。
- 文档同步：README 快速开始与仓库结构、使用指南「三分钟上手」「停止和关闭」、故障排查、文件清单、CLAUDE.md / AGENTS.md 目录表。

## 已知限制

- Qwen 单张 640 px、4 层、30 步约 7 分钟。去噪阶段 GPU 利用率约 96%，瓶颈在 20B 模型的计算量，不在显存交换；1024 px 每步约 45 秒，未开放。
- Qwen 任务运行时进程常驻内存约 24–25 GiB（已默认启用 `low_cpu_mem`），可用物理内存低于约 28 GiB 时不建议提交。
- Qwen 的分层按“视觉层”而不是语义部位划分：紧密排版的海报元素常在同一层，递归细拆对其效果有限；角色立绘会被拆成线稿和上色两层，应使用 See-through。模型会轻度重绘，重组误差约 2–6 / 255，小字可辨认但不保证逐像素一致。
- Qwen 递归细拆一次只处理 1 个图层，子层数 2–4，沿用父任务的步数与种子，并为该图层重新生成图注。
- 批次 ZIP 和单任务导出对两个模型通用；批次中的 Qwen 任务没有 `depth.psd`。
- 只测过 GGUF Q4_K_M。如需 Q5_K_M，执行 `scripts/engines/download_qwen.py Q5_K_M` 并把 `QwenEngine.quant` 改为 `Q5_K_M`，需重新压测后再开放。
- 浏览器下载由 Chrome 的下载设置决定保存位置；验收时用 `verify_exports.py` 直接从服务下载并检查文件。

## 接手后的检查顺序

1. 运行 `python scripts/doctor.py`，再查看 [API 与运维](api.md)，确认服务、任务队列和 `/api/v1/capabilities` 中两个引擎的 `ready` 与 `missing`。开发时把当前任务和模型目录视为用户数据。
2. 运行 [README](../README.md) 中的构建和测试命令，判断故障出在页面、API、SQLite 还是推理适配器。
3. 涉及界面时用浏览器验证上传、任务中心和图层工作台，维持原型设计。涉及推理时 See-through 先用 1024 px 上游样图，Qwen 先用 640 px、4 层、20–30 步，检查真实进度、图层清单、PSD 与 ZIP，并用 `scripts/verify_exports.py` 复验导出。

## 数据目录与迁移

`data/jobs/` 保存工作台任务，`data/cache/` 保存模型和依赖，`data/samples/` 保存测试样图（由 `scripts/bench/prepare_samples.py` 收集，未入库），`.local/` 保存 Python 环境。`data/m1/` 是 See-through 历史 GPU 验收记录，`output/qwen-bench/` 是 Qwen 压测记录（`review/` 中有递归细拆与步数对比的审阅图）。迁移到另一台机器时，用 `scripts/setup-engine.ps1` 重新准备两个推理环境和权重，或直接复制下列目录：

| 路径 | 内容 | 大小 |
| --- | --- | --- |
| `.local/seethrough-venv` | See-through 推理环境 | 7.3 GiB |
| `.local/qwen-venv` | Qwen 推理环境 | 6.7 GiB |
| `data/cache/models/layerdiff`、`marigold` | See-through 权重 | 12.6 GiB |
| `data/cache/models/qwen-image-layered` | Qwen 管线文件（无 BF16 transformer） | 约 16 GiB |
| `data/cache/models/qwen-image-layered-te-nf4` | NF4 文本编码器 | 5.5 GiB |
| `data/cache/models/qwen-image-layered-gguf` | Q4_K_M transformer | 12.0 GiB |

- 位置偏移：X/Y 输入框右侧是单图层“还原”图标按钮（无偏移时置灰）；有图层被移动时，标题行显示“已移动 N 层”，下方出现“全部图层还原到原位”。被截断的文字（任务状态说明、参数快照等）悬停可看全文，`studio.js` 里有全局兜底，失败原因最多显示两行。
