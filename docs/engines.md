# 推理引擎安装

LayerLens 的 Web 工作台和后端可以单独运行。拆层用的两个推理引擎体积大、对显卡有要求，不放在仓库里，需要在每台机器上单独安装。没装引擎时，工作台、任务中心、已有结果的预览和导出都能用，只是不能新建拆层任务。

| | Qwen-Image-Layered（通用拆层） | See-through（二次元角色拆层） |
| --- | --- | --- |
| 适合的图 | 海报、商品图、插画、场景、摄影 | 二次元角色立绘 |
| 输出 | 3–8 个 RGBA 图层，原图比例 | 约 17–23 个部位图层，附深度 PSD |
| 显卡 | NVIDIA，12 GB 显存 | NVIDIA，12 GB 显存 |
| 内存 | 32 GB 以上 | 16 GB 以上 |
| 下载量 | 约 28 GB 权重 + 约 3 GB 依赖 | 约 13 GB 权重 + 约 3 GB 依赖 |
| 占用磁盘 | 约 41 GB | 约 21 GB |
| 单张耗时（RTX 4070） | 640 px、5 层、30 步约 9 分钟 | 1024 px 约 6 分钟 |

两个引擎互不依赖，可以只装一个。

## 前置条件

- Windows 10 或 11（64 位）。安装脚本和启动脚本是 PowerShell，5.1 和 7 都能用。
- NVIDIA 显卡和驱动。引擎使用 PyTorch CUDA 12.6，驱动建议 560 及以上，测试环境是 RTX 4070 12 GB、驱动 560.94。在 PowerShell 里运行 `nvidia-smi` 能看到显卡即可。
- [uv](https://docs.astral.sh/uv/)，用来创建 Python 3.12 环境（会自动下载 Python 3.12，不影响系统里已有的 Python）。没有 uv 时，安装脚本会尝试用系统 Python 的 pip 装一份到 `.local/tools`；两者都没有就先执行 `winget install astral-sh.uv`。
- Git。See-through 需要拉取上游代码，部分依赖也从 Git 安装。
- 能访问 Hugging Face 或其镜像。下载脚本依次尝试 hf-mirror.com、huggingface.co 和 ModelScope，见[网络与下载源](#网络与下载源)。

运行 `python scripts/doctor.py` 可以一次查看显卡、内存、磁盘和两个引擎的状态（任何 Python 3.10+ 都行，不需要装包）。

## 一条命令安装

在项目根目录打开 PowerShell：

```powershell
.\scripts\setup-engine.ps1 -Engine qwen         # 通用拆层
.\scripts\setup-engine.ps1 -Engine seethrough   # 二次元角色拆层
```

提示“无法加载，因为在此系统上禁止运行脚本”时，先执行 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`，或改用 `powershell -ExecutionPolicy Bypass -File .\scripts\setup-engine.ps1 -Engine qwen`。

脚本按顺序做下面几件事。每一步都会先检查，做过的跳过；下载中断或报错后，重新运行同一条命令即可接着做。

1. 检查显卡、Git 和 uv。
2. 创建独立环境 `.local/<引擎>-venv`（Python 3.12）。两个引擎的环境互相独立，也和后端环境 `.local/backend-venv` 分开。
3. 仅 See-through：把上游仓库克隆到 `engine/see-through/`，切到测试过的提交。
4. 按 `engine/adapter/requirements-<引擎>.txt` 安装依赖，PyTorch 从 CUDA 12.6 的源安装。依赖文件没变时不会重装。
5. 下载权重到 `data/cache/`，逐个文件核对大小和 sha256。Qwen 还会用显卡把文本编码器转成 NF4 格式，只做一次。
6. 运行 `scripts/doctor.py` 自检。

装好后，服务已经在运行就直接刷新网页，新建任务里的引擎会从“未安装”变成可选；服务没运行就执行 `.\Start-LayerLens.ps1`。

可选参数：

| 参数 | 作用 |
| --- | --- |
| `-SkipModels` | 只装环境和依赖，权重以后再下载（再运行一次不带此参数的命令） |
| `-SkipLama` | 仅 See-through。不下载 LaMa 权重（195 MiB），代价是“前后拆分”不可用 |

## 装好后的目录

```
.local/
  qwen-venv/                     Qwen 推理环境
  seethrough-venv/               See-through 推理环境
  python/  tools/                uv 下载的 Python 3.12 与 uv 本身（如果由脚本安装）
engine/see-through/              See-through 上游代码（固定提交，不要在这里改代码）
data/cache/
  models/qwen-image-layered/          Qwen 管线文件，不含 41 GB 的 BF16 transformer（约 16 GB）
  models/qwen-image-layered-gguf/     Q4_K_M transformer（12 GB）
  models/qwen-image-layered-te-nf4/   NF4 文本编码器，由脚本生成（5.5 GB）
  models/layerdiff/  models/marigold/ See-through 权重（约 12.6 GB）
  torch/hub/checkpoints/lama_large_512px.ckpt   前后拆分用的 LaMa
  uv/                            依赖下载缓存，可以删除
```

这些目录都在 `.gitignore` 里。

## Qwen-Image-Layered（通用拆层）

固定版本（定义在 `engine/adapter/catalog.py`）：

- 管线：[Qwen/Qwen-Image-Layered](https://huggingface.co/Qwen/Qwen-Image-Layered) revision `8f0ca708`，Apache-2.0。只下载 text_encoder、vae、tokenizer、processor、scheduler 和配置文件。
- Transformer：[unsloth/Qwen-Image-Layered-GGUF](https://huggingface.co/unsloth/Qwen-Image-Layered-GGUF) revision `4bb0a560` 的 `qwen-image-layered-Q4_K_M.gguf`，Apache-2.0。
- 依赖：PyTorch 2.8.0+cu126、diffusers 0.37.0、transformers 5.0.0、bitsandbytes 0.48.2、gguf 0.17.1，完整列表见 `engine/adapter/requirements-qwen.txt`。

不用脚本时的等价步骤（项目根目录）：

```powershell
uv venv --python 3.12 .local\qwen-venv
uv pip install --python .local\qwen-venv\Scripts\python.exe --extra-index-url https://download.pytorch.org/whl/cu126 --index-strategy unsafe-best-match -r engine\adapter\requirements-qwen.txt
.local\qwen-venv\Scripts\python.exe scripts\engines\download_qwen.py
.local\qwen-venv\Scripts\python.exe scripts\engines\quantize_qwen_text_encoder.py
```

运行时要注意：

- 任务进程常驻内存约 24–25 GB。任务管理器里会显示约 17 GB 的“共享 GPU 内存”，那是锁页的主机内存，不是显存溢出。可用内存低于约 28 GB 时不要提交。
- 图层数默认 5。6 层在 12 GB 显卡上会溢出到共享内存，7–8 层没有实测，界面会提示并在提交前确认。
- 只测过 Q4_K_M。Q6_K、Q8_0 的分层结果和 Q4_K_M 几乎一样，内存占用更高，所以不作为默认。想试其他量化版本，执行 `download_qwen.py Q6_K`，再把 `engine/adapter/qwen_command.py` 里 `QwenEngine.quant` 改掉。

## See-through（二次元角色拆层）

固定版本（定义在 `engine/adapter/catalog.py`）：

- 上游代码：[shitagaki-lab/see-through](https://github.com/shitagaki-lab/see-through) 提交 `fc4440d`。
- 权重：Hugging Face `layerdifforg/seethroughv0.0.2_layerdiff3d`（revision `4477e6ce`）和 `layerdifforg/seethroughv0.0.1_marigold`（revision `4f4ffc46`）。
- 可选：[dreMaz/AnimeMangaInpainting](https://huggingface.co/dreMaz/AnimeMangaInpainting) 的 `lama_large_512px.ckpt`，只用于前后拆分。
- 依赖：PyTorch 2.8.0+cu126 加上游的 `requirements.txt`，见 `engine/adapter/requirements-seethrough.txt`。上游写的是 cu128，这里用 cu126，因为显存基线是在 cu126 上测的。

不用脚本时的等价步骤（项目根目录）：

```powershell
git clone https://github.com/shitagaki-lab/see-through.git engine\see-through
git -C engine\see-through checkout fc4440db7c076518e313f378e28b1504e9886a95
uv venv --python 3.12 .local\seethrough-venv
Push-Location engine\see-through
uv pip install --python ..\..\.local\seethrough-venv\Scripts\python.exe --extra-index-url https://download.pytorch.org/whl/cu126 --index-strategy unsafe-best-match -r ..\adapter\requirements-seethrough.txt
Pop-Location
.local\seethrough-venv\Scripts\python.exe scripts\engines\download_seethrough.py
```

`uv pip install` 要在 `engine\see-through` 目录里执行，因为上游依赖文件里的 `-e ./common` 是相对当前目录的。

运行时固定开启 Group Offload。1024 px 显存峰值约 10.2 GB，1280 px 约 11.1 GB，12 GB 显卡第一次使用建议选 1024。

## 网络与下载源

- 下载脚本默认按 hf-mirror.com → huggingface.co → ModelScope 的顺序尝试，失败自动换下一个，支持断点续传。只想用某几个源时设置 `$env:LAYERLENS_HF_HOSTS = 'huggingface'`（可选值 `mirror`、`huggingface`、`modelscope`，逗号分隔）。
- 读取文件清单（文件名、大小、sha256）只走 huggingface.co 和 hf-mirror.com。两个都连不上时脚本会直接报错。
- 需要代理时，在运行脚本前设置 `$env:HTTPS_PROXY = 'http://127.0.0.1:7890'`，Python 下载和 uv 都会使用它。
- PyTorch 的 CUDA 版本从 download.pytorch.org 下载，单个文件 2–3 GB。慢的话可以换成国内的 PyTorch 镜像，把 `--extra-index-url` 换成镜像地址后手动执行上面的 `uv pip install`。

## 把权重放到其他磁盘

服务只认 `data/cache/models` 这个路径。C 盘空间不够时，可以先把目录建在别的盘，再用目录联接指过去：

```powershell
New-Item -ItemType Directory D:\LayerLens\models
New-Item -ItemType Junction -Path data\cache\models -Target D:\LayerLens\models
```

已经下载过的话，先把 `data\cache\models` 整个移动到目标位置，再建联接。

## 升级与卸载

- 升级版本：只改 `engine/adapter/catalog.py` 里的提交号或 revision，以及对应的 `requirements-*.txt`，然后重新运行安装脚本。新版本需要重新测显存和耗时，再决定界面上开放哪些参数。
- 卸载一个引擎：停掉服务，删除 `.local/<引擎>-venv` 和对应的 `data/cache/models/...` 目录（See-through 还有 `engine/see-through/`）。刷新页面后，这个引擎会显示为“未安装”，已有结果仍可查看和导出。

## 常见问题

| 现象 | 处理 |
| --- | --- |
| 新建任务里引擎显示“未安装” | 运行 `python scripts/doctor.py` 查看缺了什么，再运行对应的安装命令。装好后刷新页面 |
| 显示“后台任务进程已停止” | 重新运行 `.\Start-LayerLens.ps1` |
| 下载报 `Size or digest check failed` | 文件不完整或被代理改过。重新运行安装命令会续传；反复出现时换一个下载源 |
| `uv pip install` 报找不到 `torch==2.8.0+cu126` | 没有使用 CUDA 源。确认命令里带了 `--extra-index-url https://download.pytorch.org/whl/cu126 --index-strategy unsafe-best-match` |
| 生成 NF4 文本编码器时很慢或报显存不足 | 这一步会短时占满 12 GB 显存并借用共享内存。关掉其他占显存的程序后重试 |
| 任务报 `CUDA_OOM` | 确认没有其他程序占用显存（`nvidia-smi`）。See-through 改用 1024 px，Qwen 减少层数 |
| `nvidia-smi` 找不到 | 安装或更新 NVIDIA 驱动。没有 NVIDIA 显卡的机器只能浏览和导出已有结果 |
| PowerShell 输出中文乱码 | 只影响显示。可以先执行 `[Console]::OutputEncoding = [Text.Encoding]::UTF8` |
