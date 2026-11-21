# 架构与模型接入

## 代码边界

| 模块 | 职责 |
| --- | --- |
| `frontend/index.html`、`frontend/src/studio.css` | 按设计原型落地。三栏工作台、任务表、新建弹窗、深浅主题与响应式布局。Vite、Tailwind 和本地字体共同构建。 |
| `frontend/src/studio*.js` | 页面状态、图层交互、任务和上传。`api.js` 是请求边界，`workspace.js` 保存浏览器侧图层调整并导出 PNG。引擎差异（参数、二次拆分动作、导出格式、运行方案）和引擎是否已安装全部来自 `/capabilities`，未安装时显示安装命令。 |
| `backend/app.py`、`store.py`、`worker.py` | FastAPI、SQLite 任务生命周期、单 GPU 串行执行与子进程管理。 |
| `backend/assets.py` | 从 PSD 生成真实图层清单、合成、蒙版和导出包，深度 PSD 可选。 |
| `backend/engines.py`、`options.py` | 模型注册、可用性、参数 schema、分辨率、导出格式、二次拆分动作与上限。任务记录保存 `engine_id` 和 `engine_version`。 |
| `engine/adapter/` | 每个引擎一个启动器（`command.py` 为 See-through，`qwen_command.py` 为 Qwen）和一个运行脚本（`run.py`/`derive.py`，`qwen_run.py`）。`runner_io.py` 提供进程握手与进度上报。产物校验在各运行脚本中实现。`catalog.py` 集中记录上游提交、权重 revision、安装命令和磁盘需求，`requirements-*.txt` 是各引擎环境的依赖。 |
| `engine/see-through/` | 安装脚本克隆的上游代码，固定在 `catalog.py` 的提交，不入库，不在此修改。 |
| `scripts/` | `setup-engine.ps1` 安装引擎，`doctor.py` 自检，`engines/` 下载权重，`bench/` 测量显存与耗时，`verify_exports.py` 复验导出。 |

## 执行与产物契约

适配器用 `missing()` 报告缺失文件，`catalog.missing_parts` 按路径前缀把它们归为推理环境、上游代码或模型权重，`/capabilities` 同时返回适配器的 `setup`（安装命令、文档位置、下载量）。引擎可以不安装，后端和工作台照常运行，只拒绝该引擎的新任务。`launch(source, work, options)` 返回命令参数、工作目录与环境变量。有二次拆分能力时实现 `launch_derivation(parent, work, operation, options)`，`options` 是父任务的生效参数。子进程启动后先读 stdin 的握手字节，父进程失联时退出（`runner_io.guard_owner`）。子进程在 `progress.ndjson` 写实际 `stage`、`step`、`total_steps` 和 `pass_index`。worker 以进程成功退出并收到 `complete` 为发布条件。

工作目录至少写出 `input.psd`、含 `frame_size`（宽、高）的 `input.psd.json` 和与图层画布对齐的 `input/src_img.png`。`input_depth.psd` 可选，存在时应与彩色 PSD 的画布尺寸、图层名称、次序和边界一致。预览清单按从底到顶排列，`bbox_xyxy` 为像素边界。PSD 与原始图层 ZIP 包含模型完整结果，合成 PNG 使用工作台当前可见性与透明度，浏览器侧调整不写回模型结果。

各引擎在运行脚本里校验自己的输出后才报告 `complete`：

- See-through（`run.verify`）：彩色与深度 PSD 都存在，画布为请求分辨率的正方形，两者图层名称、边界与 sidecar 的 `parts` 一致。
- Qwen（`qwen_run.verify`）：画布可为任意比例，不得有深度 PSD；图层名称唯一且符合 `L<n>(.<n>)*`，次序与边界和 sidecar 一致，位于画布内，像素为 RGBA 且不为空；`src_img.png` 与画布等大。

## See-through 参考基线（RTX 4070 12 GB）

- 上游提交为 `fc4440d`，推理环境为 `.local/seethrough-venv`（Python 3.12.16、PyTorch 2.8.0+cu126）。模型目录为 `data/cache/models/layerdiff` 和 `data/cache/models/marigold`。前后拆分依赖 `data/cache/torch/hub/checkpoints/lama_large_512px.ckpt`。
- 运行使用本地权重，`HF_HUB_OFFLINE=1`。安装步骤见[推理引擎安装](engines.md)。最早的下载与显存测量记录在 `data/m1/`，测量脚本为 `scripts/bench/run_m1.ps1`。
- 默认 1280、深度 768、推理 30 步，强制启用 Group Offload。图像分辨率支持 1024 和 1280，深度支持 512、640、768。历史 RTX 4070 12 GB 测试中，进程专用显存峰值约为 1024 的 10.16 GiB 和 1280 的 11.06–11.18 GiB。1280 余量较小，初次验证优先用 1024。报告见 `data/m1/status.txt`。
- 智能左右拆分（TBLR）默认开启：2026-11-21 二轮审查实测，同一立绘 1024/30 步开启后 387 s（+9%），输出 23 层（handwear、eyebrow、ears、eyelash、eyewhite、irides 拆出左右独立层），角色区域 MAE 14.12、p90 27.7、背景残留散点与关闭时完全一致——拆左右部位没有保真代价，且与“23+ 部位”的口径一致。关闭后回到约 17 层的粗部位集合。
- 2026-11-21 复核测量（同一立绘 928×1232，种子 42）：1024/30 步端到端 355 s、1024/50 步 557 s、1280/30 步 486 s。See-through 不含背景层，对整幅源图的重组误差（约 211）混入背景、不可比；有效口径是角色区域（合成 alpha>8）：1024/30 MAE 14.12（p90 27.7）、1024/50 MAE 14.29（p90 28.7）、1280/30 MAE 12.42（p90 20.0）。结论：30→50 步无可测收益，维持默认 30；1280 边缘保真明显更好（p90 -28%）、耗时 +37%，维持默认 1280，显存紧张或快速预览用 1024。白背景上约 0.2% 极淡残留散点（alpha 中位数约 20）为模型固有 matting 残留，纯黑底较明显，常规底色不影响使用。

**2026-11-21 复核测量**（同一角色立绘 928×1232，种子 42）：1024/30 步端到端 355 s，1024/50 步 557 s，1280/30 步 486 s。See-through 只输出角色部件、不含背景层，对整幅源图算重组误差会把背景混进去（约 211），没有可比性；有意义的口径是角色区域（合成 alpha>8 的像素）：1024/30 MAE 14.12（p90 27.7），1024/50 MAE 14.29（p90 28.7），1280/30 MAE 12.42（p90 20.0）。结论：步数 30 加到 50 没有可测量收益，维持默认 30；1280 的边缘保真明显好于 1024（p90 低 28%），耗时多 37%，维持默认 1280，显存紧张或快速预览用 1024。三次运行都是 17 层、结构一致；白背景上约 0.2% 的极淡残留散点（alpha 中位数约 20）是模型 matting 的固有残留，纯黑底上较明显，常规底色不影响使用。新建任务弹窗的 See-through 参考耗时按 355/486 s 随分辨率与步数换算。

## Qwen-Image-Layered 接入

### 来源与许可

- 管线文件来自 [Qwen/Qwen-Image-Layered](https://huggingface.co/Qwen/Qwen-Image-Layered)（Apache-2.0，revision `8f0ca708`），只下载 text_encoder、vae、tokenizer、processor、scheduler 与各配置，不下载 41 GB 的 BF16 transformer。
- transformer 使用 [unsloth/Qwen-Image-Layered-GGUF](https://huggingface.co/unsloth/Qwen-Image-Layered-GGUF)（Apache-2.0，revision `4bb0a560`）的 `qwen-image-layered-Q4_K_M.gguf`，12,843,358,848 字节，sha256 `6eedc0c9a08f91f1f39959b9ac991d643d351baf45d1248267c4fdd61645dfd0`。
- `scripts/engines/download_qwen.py` 按固定 revision 下载，依次尝试 hf-mirror、huggingface.co 与 ModelScope，支持断点续传并按 LFS sha256 校验。
- `scripts/engines/quantize_qwen_text_encoder.py` 把 Qwen2.5-VL-7B 文本编码器一次性转成 bitsandbytes NF4，保存到 `data/cache/models/qwen-image-layered-te-nf4`（5.49 GiB）。

### 运行环境

- 独立环境 `.local/qwen-venv`（Python 3.12.16），依赖固定在 `engine/adapter/requirements-qwen.txt`：PyTorch 2.8.0+cu126、diffusers 0.37.0（已含 `QwenImageLayeredPipeline` 与 GGUF 单文件加载）、transformers 5.0.0、bitsandbytes 0.48.2、gguf 0.17.1、psd-tools 1.14.2。与 See-through 的 `.local/seethrough-venv` 互不共享。
- 安装与重建：`.\scripts\setup-engine.ps1 -Engine qwen`，手动步骤见[推理引擎安装](engines.md#qwen-image-layered通用拆层)。

### 12 GB 显存方案

`qwen_run.py` 在一个子进程里分阶段运行：

1. 加载预量化 NF4 文本编码器（约 5.6 GB），提示词为空时用 Qwen2.5-VL 生成图注，编码正向与负向提示词后释放编码器。直接在加载时量化 BF16 编码器会让显存峰值达到约 14.8 GB 并溢出到共享内存，因此使用预量化副本。
2. 用 `from_single_file` 加载 GGUF transformer，启用 block 级 group offload（每组 1 个 block，CUDA stream 预取），VAE 常驻 GPU。
3. 去噪与解码，按 alpha 裁剪每层边界，丢弃全透明层并记录在 `dropped_empty_layers`，写 PSD 与 sidecar，计算重组误差，校验产物。

diffusers 0.37 只把 Linear 层换成能反量化的 GGUF 层。Qwen-Image-Layered 的 `time_text_embed.addition_t_embedding`（`nn.Embedding`）在该 GGUF 中以 BF16 原始字节保存，直接使用会出现 3072 与 6144 维不匹配。`restore_unquantized_modules` 在加载后把这类张量反量化。

去噪阶段 GPU 利用率约 96%，属于计算瓶颈：20B 模型在开启 true CFG 时每步做两次前向，4 层、640 px 时序列约 9.7k token。

### Qwen-Image-Layered 参考基线（RTX 4070 12 GB）

RTX 4070 12 GB、64 GB 内存、i7-13700KF，Windows 10。专用显存为 Windows 进程专用显存峰值（PDH 计数器），耗时为单张端到端时间，含约 15–20 s 的模型加载与图注。测量脚本为 `scripts/bench/measure_qwen.py` 与 `scripts/bench/run_qwen_bench.ps1`，原始记录与审阅拼图在 `output/qwen-bench/*/measurement.json`、`contact-sheet.png`。b1–b9 未启用 `low_cpu_mem`。

| 运行 | 样图 | 分辨率 | 层 | 步数 | 耗时 | 每步 | 专用显存 | 进程内存 | 重组误差 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| b1 | 官方插画 qwen-01（1:1） | 640 | 4 | 50 | 668 s | 12.9 s | 9.67 GiB | 29.9 GiB | 3.32 |
| b2 | 香水玻璃瓶（3:4） | 640 | 4 | 50 | 664 s | 12.8 s | 9.48 GiB | 30.2 GiB | 4.08 |
| b3 | 咖啡海报（4:3） | 640 | 4 | 50 | 667 s | 12.9 s | 9.48 GiB | 29.7 GiB | 6.05 |
| b4 | 咖啡海报 | 640 | 4 | 30 | 410 s | 12.9 s | 9.48 GiB | 29.8 GiB | 5.65 |
| b5 | 咖啡海报 | 640 | 4 | 20 | 282 s | 12.9 s | 9.48 GiB | 29.3 GiB | 4.94 |
| b6 | 咖啡海报，`low_cpu_mem` | 640 | 4 | 20 | 302 s | 14.2 s | 9.45 GiB | 23.3 GiB | 4.94 |
| b7 | 角色立绘 test_image（3:4） | 640 | 3 | 30 | 344 s | 10.8 s | 7.53 GiB | 29.4 GiB | 1.83 |
| b8 | 厨房玻璃水瓶（1:1） | 640 | 6 | 30 | 568 s | 18.2 s | 10.86 GiB | 29.6 GiB | 4.87 |
| b9 | 香水玻璃瓶 | 1024 | 4 | 10 | 478 s | 45.4 s | 10.99 GiB | 30.7 GiB | 4.47 |
| b10 | 生日插画 qwen-05，`low_cpu_mem` | 640 | 5 | 30 | 524 s | 16.9 s | 10.92 GiB | 25.2 GiB | 5.98 |

重组误差为按层叠回的图与原图在可见像素上的平均 RGB 绝对误差（0–255），反映模型重绘程度，不是分层质量的完整指标。

**定档结论**

- 开放 640 px、3–8 层、20–50 步，默认 5 层 30 步（单张约 9 分钟）。1024 px 每步 45 s，50 步需约 40 分钟，且显存峰值 11.0 GiB，暂不开放。图层上限由用户在新建任务弹窗的滑块中自选：3–5 层为实测稳定档（5 层峰值 10.9 GiB，标为“推荐”）；6 层 reserved 13.4 GiB、溢出到 Windows 共享内存，标为“不建议”；7–8 层未实测，标为“强烈不建议”。选 6 层以上提交前会弹出确认。
- 服务默认启用 `low_cpu_mem`（group offload 按需锁页）。b5 与 b6 的输出逐像素一致，去噪每步慢约 10%，进程内存少约 6 GiB，并消除了约 17 GiB 的锁页主机内存（任务管理器中显示为“共享 GPU 内存”）。
- 步数：同一张咖啡海报 50/30/20 步，底部小字 “Limited edition · 250g · Arabica” 均可辨认，50 步最锐利，20 步的笔画边缘略软；背景纹理在 20 步时有轻微斑块。30 步是速度与清晰度的折中。
- CFG：6.0 对照 4.0（咖啡海报同种子，2026-11-21 三轮探针）重组误差 4.96 vs 5.65 更低，但可分离层从 4 降到 3（丢一个元素层）。分层粒度优先于重组误差，维持默认 4.0。
- 手动画面描述按管线上限 1024 tokens 生效（`decompose` 显式传 `max_sequence_length=1024`；embeds 保留自然 token 长度，短图注零代价）。1084 字中文 prompt 实测 811 tokens 完整生效；sidecar 与参数快照显示 `prompt_tokens`。此前截到 512 会静默砍半长 prompt，已修复。
- 连续稳定性：b1–b9 在同一进程序列中连续运行约 70 分钟，加上服务内两次 Qwen 任务和一次递归细拆，无 OOM、无失败。

**效果观察**（审阅 `contact-sheet.png`）

- 插画与海报（b1、b3、b10）：背景、主体组、文字标题、装饰元素分层清晰，背景层会补全被遮挡区域。海报内部的元素（杯子、纸袋、小字）倾向于留在同一层，需要递归细拆。
- 商品摄影（b2、b8）：带文字的标签和玻璃瓶身可以单独成层，玻璃的透明感保留在 alpha 中；瓶身与浅色背景相近时可能不分离（b2 有 1 层为空，已自动省略）。复杂厨房背景（b8）能分出植物、餐具、砧板等道具，但植物被拆到两层，个别层带有半透明边缘残留。
- 角色立绘（b7）：线稿与上色被拆成两层，没有语义部位，背景层为纯白。角色语义分层应使用 See-through。
- 1024 px（b9）在 10 步下与 640 px 50 步观感接近，小字更清晰，但耗时不划算。

**2026-11-21 复核（Robot Merge Master 896×448、5 层、种子 777）**，记录在 `output/qwen-bench/t1–t4`、`q6-*`、`q8-*`、`q4-robot-*`，脚本 `scripts/bench/run_qwen_quality.ps1`。

| 运行 | 量化 | 层 | 步数 | 耗时 | 每步 | 专用显存 | 进程内存 | 重组误差 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 服务任务 | Q4_K_M | 5 | 30 | 510 s | 16.4 s | 10.6 GiB（reserved） | — | 6.95 |
| q6 | Q6_K | 5 | 30 | 579 s | 18.0 s | 10.89 GiB | 34.7 GiB | 7.12 |
| q8 | Q8_0 | 5 | 30 | 533 s | 16.6 s | 10.74 GiB | 41.9 GiB | 7.19 |
| q4-s50 | Q4_K_M | 5 | 50 | 882 s | 17.1 s | 10.72 GiB | 24.7 GiB | 6.83 |
| q4-l6 | Q4_K_M | 6 | 30 | 611 s | 19.4 s | 10.83 GiB，reserved 13.4 GiB，共享 3.6 GiB | 24.5 GiB | 7.10 |

- 量化：Q6_K、Q8_0 与 Q4_K_M 的分层边界几乎逐像素相同（bbox 差 ≤ 2 px），小机器人头顶的数字“1”、边缘软硬程度肉眼无差别，Q8_0 多占约 17 GiB 内存。不更换默认权重。
- 数字“1”没有丢失：它们被分到第 4 层（与 PLAY FREE 按钮同层），所以第 3 层独显时看不到。
- 6 层能把绿机甲与标题分开，但 PyTorch reserved 峰值 13.4 GiB，超出 12 GiB 物理显存后溢出到共享内存，仍不开放。
- 耗时拆解（`metrics.phases.layering` 新增 `first_step_seconds`、`seconds_per_step`、`decode_seconds`）：文本编码器加载 3–6 s，图注与编码 4–6 s，transformer 加载 5–11 s，去噪占 96–97%，VAE 解码约 1 s。每步耗时随“层数 + 1”的帧数线性增长（4 层约 13–15 s，5 层约 16 s）。offload 对照（6 步）：不锁页每步快约 7%，但多占 17 GiB 共享内存；每组 4 个 block 与 1 个无差别；去噪期间 GPU 利用率 82–92%、PCIe 4.0。瓶颈是 20B 模型的计算量，没有可安全回收的浪费，维持现状。

### 递归细拆

`run_recursive` 读取父任务 PSD，把目标图层（裁剪后的 RGBA）作为新输入，重新生成图注并分层，再把子层缩放回父图层的边界、原位替换父图层，标签为 `L3.1`、`L3.2`。少于 2 个非空子层时以 `LAYERLENS_NO_SPLIT` 退出，任务记为 `NO_SPLIT`，父任务结果不受影响。实测咖啡海报 L3（海报整体）细拆为 3 子层用时 6 分 34 秒，得到底板与海报主体两个非空子层（第 3 个为空已省略）；海报内的杯子与纸袋仍在同一子层。递归细拆适合把“主体组”从背景板上剥离，对紧密排版的平面元素效果有限，一次处理 1 个图层，子层数开放 2–4。

**还原校验（2026-11-21）**：模型的第一帧总是画成不透明背景。咖啡海报 L4 是一层覆盖全画布、alpha 约 48（19%）的暖色调层，递归后得到一张不透明米白纸纹和两张半透明底纹，叠回后与父层的 alpha 平均误差 207、预乘颜色误差 168，整图重组误差从 5.65 升到 107.27。核对过的其他假设均不成立：子层按父层位置原位插入，Z 序正确；每个图层单独生成图注（该次图注是“plain off-white surface”，由图层本身得来，不是复用原图图注）；送入模型的 RGBA 张量 alpha 与原层一致（VaeImageProcessor 保留 4 通道）。现在 `run_recursive` 会先跳过整幅半透明的色调层，再用 `fit_children` 比较子层叠加结果与父层，误差超过阈值时保留父层并以 `SPLIT_REJECTED` 结束；通过校验的子层 alpha 会被裁剪到父层 alpha 以内。已有的正常细拆（咖啡 L3：2.55 / 4.71；Robot L5：2.63 / 5.51）都远低于阈值。

**空子层修复（2026-11-21 审查）**：`fit_children` 把子层 alpha 裁剪到父层以内；内容完全落在父层 footprint 之外的子层仍可能通过均值误差门槛，裁剪后却变成空层，`verify` 按契约拒绝空层，整个任务记为 ENGINE_FAILED（实例 ee764ccd / c7a2df61 的 L2.3）。现在裁剪后变空的子层直接丢弃，丢弃后不足 2 个子层时该目标记为 unchanged，不进入拆分流程。回归测试见 `backend.test_qwen_split`。