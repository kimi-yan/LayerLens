# API 与运维

所有端点以 `/api/v1` 为前缀。运行中的 [OpenAPI](http://127.0.0.1:8765/openapi.json) 以服务实际接口为准。

## 提交和查询

用 PowerShell 7 提交图片。两条命令都会创建真实 GPU 任务，需要对应引擎已经安装（见[推理引擎安装](engines.md)）。

```powershell
$api = 'http://127.0.0.1:8765/api/v1'
# See-through：角色立绘，1024 px 为 12 GB 显存的首选验证档位
$form = @{
    file = Get-Item 'engine/see-through/common/assets/test_image.png'
    engine_id = 'seethrough'
    options = '{"resolution":1024,"inference_steps":30}'
}
$job = Invoke-RestMethod "$api/jobs" -Method Post -Form $form
# Qwen-Image-Layered：海报、商品与场景图，640 px，单张约 7–11 分钟
$form = @{
    file = Get-Item 'D:/images/poster.png'   # 换成任意海报、商品或插画
    engine_id = 'qwen'
    options = '{"layers":4,"inference_steps":30}'
}
$job = Invoke-RestMethod "$api/jobs" -Method Post -Form $form
Invoke-RestMethod "$api/jobs/$($job.job_id)"
```

Windows PowerShell 5.1 没有 `-Form` 参数，可改用 `curl.exe -F`。PS 5.1 的 `Invoke-RestMethod` 会把无 charset 的 UTF-8 JSON 按 Latin-1 显示，中文字段显示为乱码属正常，浏览器和 PowerShell 7 不受影响。

| 接口 | 行为 |
| --- | --- |
| `GET /capabilities` | `engines` 按引擎列出 `ready`、`missing`（缺失文件的相对路径）、`missing_parts`（按推理环境 / 上游代码 / 模型权重归类，界面用它说明缺什么）、`setup`（安装命令 `command`、文档位置 `guide`、下载量 `download_gib`、磁盘占用 `disk_gib`）、`defaults`、`options_schema`、`resolutions`、`exports`、`runtime`、`depth`、`actions`（当前可用的二次拆分）、`declared_actions`、`max_split_targets` 和 `split_layers`。顶层的 `engine_ready`、`missing`、`defaults`、`options_schema`、`resolutions`、`depth_split_ready` 为兼容旧客户端保留，只描述 `default_engine`（See-through）。 |
| `GET /system` | GPU 型号、驱动、显存用量和系统内存。无法读取 GPU 时 `gpu` 为 `null`。 |
| `POST /jobs`、`POST /batches` | 单图用 `file`，批次用 `files` 重复字段，支持 2–20 张。`engine_id` 为 `seethrough` 或 `qwen`，`options` 按该引擎的 schema 严格校验。批次所有图片通过校验后才入队。 |
| `GET /task-center` | 分页查询。支持 `filter`、`q`、`engine_id` 和 `page`。回收站用 `filter=trash`。 |
| `GET /jobs/{id}`、`POST /jobs/{id}/cancel`、`POST /jobs/{id}/retry` | 轮询状态、取消和创建重试任务。`progress` 是实际阶段，存在步数时对应当前阶段。`engine_version` 记录模型与权重版本，`effective_options` 记录实际生效参数。 |
| `GET /jobs/{id}/results/base/layers` | 图层清单和素材地址，从底到顶排列。无深度模型的清单 `has_depth=false`，不含 `depth` 地址。Qwen 结果附带 `details`：`prompt`（实际使用的图注或用户描述）、`reconstruction_mae`、`dropped_empty_layers`。 |
| `GET /jobs/{id}/results/base/exports/{kind}` | `psd`、`png-zip`，See-through 另有 `depth-psd`。任务的 `exports` 字段只列出该引擎实际提供的格式。 |
| `POST /jobs/{id}/results/base/postprocess` | 二次拆分，见下文。 |
| `GET /batches/{id}` | 批次状态。批次结束后用 `export_url` 下载可用成果及状态清单。 |

支持静态 PNG、JPEG、WebP 和 BMP，每张最多 32 MiB、2400 万像素。上传失败时用 `detail.code` 映射错误。`detail.file_index` 从 0 开始，指向有问题的文件。参数问题在 `INVALID_OPTIONS` 的 `fields` 中给出。

### 引擎参数

| 引擎 | 参数 |
| --- | --- |
| `seethrough` | `resolution` 1024/1280，`resolution_depth` 512/640/768，`inference_steps` 1–100，`inference_steps_depth` -1 或 1–10，`seed`，`tblr_split`，`low_vram` 必须为 `true`。 |
| `qwen` | `resolution` 640（按原图比例保持约 640² 像素），`layers` 3–8（默认 5），`inference_steps` 20–50，`true_cfg_scale` 1–8，`seed`，`prompt`（可选，留空则由 Qwen2.5-VL 自动生成图注），`negative_prompt`，`caption_language` `en`/`zh`，`cfg_normalize`。除 `layers` 外，范围只开放了参考环境（RTX 4070 12 GB）实测过的档位；`layers` 6–8 允许提交，但 6 层在 12 GB 显卡上会溢出到共享内存，7–8 层未实测，界面会提示并要求确认。见[架构文档](architecture.md#qwen-image-layered-参考基线rtx-4070-12-gb)。 |

### 二次拆分

请求体为 `{"method": ..., "target_tags": [...], "layers": n}`。

- See-through 支持 `left_right` 和 `depth`（前后拆分需要 LaMa 权重），一次最多 32 个图层，不接受 `layers`。
- Qwen 支持 `recursive`：把所选图层作为输入再次分层，子层原位替换父图层，标签为 `L2.1`、`L2.2` 等。一次 1 个图层，`layers` 为 2–4，默认 3。子任务沿用父任务的步数、CFG 和种子，并为该图层重新生成图注。
- 不支持的动作返回 503 `ACTION_UNAVAILABLE`，超出目标数返回 422 `TOO_MANY_TARGETS`，子层数越界返回 422 `INVALID_OPTIONS`。模型没有拆出至少 2 个非空子层时，任务以 `NO_SPLIT` 结束；Qwen 子层叠回后还原不出父图层（alpha 或预乘颜色平均误差超过 32 / 24，或父层是 99.5% 分位 alpha 低于 64 的整幅半透明色调层）时，任务以 `SPLIT_REJECTED` 结束，父任务结果不变。成功时 `details.recursive_fidelity` 记录每个被拆图层的 alpha / 颜色还原误差。

## 回收、故障与文件位置

- 批量管理用 `POST /task-center/{trash,restore,purge}`，请求内容为 `{"ids":["任务 ID"]}`，每次最多 200 项。回收仅接受已结束任务。永久删除只接受回收站内且未被活动拆分引用的任务。响应 `purged` 和 `failed` 分别列出成功和待重试的 ID。
- `data/jobs/queue.sqlite3` 是任务库。`data/jobs/{id}/engine.log` 是引擎日志，`work/` 是运行中间目录，`result/` 是已发布结果。正常退出时，未结束的任务可返回队列。异常中断后，启动恢复流程把已有 `result/` 的任务记为成功，已请求取消的记为取消，其余记为 `ENGINE_INTERRUPTED`。
- 遇到无法提交时先运行 `python scripts/doctor.py`，或查 `/capabilities` 中该引擎的 `missing`；`ready` 为 `false` 且 `worker_alive` 为 `true` 表示引擎没装全，`worker_alive` 为 `false` 时重启服务。遇到显存溢出时确认单 GPU 串行，检查 `nvidia-smi` 是否有其他进程占用显存。See-through 改用 1024 重试；Qwen 减少层数，或关闭其他占用显存与内存的程序后重试。
- Qwen 任务运行时进程常驻内存约 24–25 GB（服务默认启用 `low_cpu_mem`），Windows 任务管理器会把 group offload 的锁页内存显示为“共享 GPU 内存”，这是主机内存，不是显存溢出。可用物理内存低于约 28 GB 时不要提交 Qwen 任务。
- 结果缺失先查看任务目录和日志，再决定是否重跑。
- 当前服务绑定 `127.0.0.1`。日常清理任务请用回收站或 API。进行磁盘维护前先确认任务状态，保护任务库、上传图片、模型缓存和上游 checkout。
