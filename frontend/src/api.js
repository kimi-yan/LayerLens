export const terminal = (job) => ['succeeded', 'failed', 'cancelled'].includes(job?.status)
export const base = (id) => '/api/v1/jobs/' + id
export const result = (id) => base(id) + '/results/base'

const requestErrors = {
  TRASH_CONFLICT: '任务状态已变化。请刷新列表，只选择已结束的任务。',
  RESTORE_CONFLICT: '选中的任务已移除，或已经开始永久清理。请刷新后重试。',
  TRASH_REQUIRED: '请先将任务移到回收站，然后再永久删除。',
  ACTIVE_DERIVATION: '有排队中或正在处理的二次拆分仍在使用这些文件。请等它结束后重试。',
  PARENT_UNAVAILABLE: '该任务依赖的原始拆分结果已永久删除。可以载入图片与参数，重新拆分。',
  INVALID_IMAGE: '图片无法读取，请换一张静态 PNG、JPEG、WebP 或 BMP 图片。',
  ANIMATED_IMAGE: '这是动图。请先保存为一张静态图片再上传。',
  IMAGE_TOO_LARGE: '图片超过 32 MiB，请压缩后再上传。',
  IMAGE_TOO_MANY_PIXELS: '图片超过 2400 万像素，请缩小尺寸后再上传。',
  INVALID_OPTIONS: '有拆分参数超出允许范围，请检查标出的选项。',
  RESULT_MISSING: '任务产物缺失。可按原参数重跑，重新生成结果。',
  ENGINE_UNAVAILABLE: '这个推理引擎还没有安装，或后台任务进程已停止。安装方法见 docs/engines.md。',
  ACTION_UNAVAILABLE: '当前模型不支持这种二次拆分，或所需的引擎文件还没有安装。',
  TOO_MANY_TARGETS: '该模型一次只能细拆有限数量的图层，请减少选择。',
}

export class ApiError extends Error {
  constructor(status, detail) {
    const messages = {
      403: '请求被拒绝，请在 LayerLens 工作台中重试。',
      404: '没有找到这项任务或结果。可以回任务中心查看。',
      409: '任务状态已改变，请刷新后检查。',
      413: '上传内容超过大小限制，请减少图片数量或压缩图片。',
      422: '图片或参数无法读取，请检查后重试。',
      503: '推理服务暂时不可用：引擎可能还没有安装，或后台任务进程已停止。',
    }
    super(requestErrors[detail?.code] || messages[status] || '服务请求失败，请重试。')
    this.detail = detail
  }
}

export async function api(path, options) {
  const response = await fetch(path, options)
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}))
    throw new ApiError(response.status, payload.detail)
  }
  return response.status === 204 ? null : response.json()
}

export function submitFiles(files, options, engineId = 'seethrough') {
  const batch = files.length > 1
  const body = new FormData()
  for (const file of files) body.append(batch ? 'files' : 'file', file)
  body.append('options', JSON.stringify(options))
  body.append('engine_id', engineId)
  return api(batch ? '/api/v1/batches' : '/api/v1/jobs', { method: 'POST', body })
}

export const errors = {
  ENGINE_FAILED: '推理失败，请检查图片后重试。',
  ENGINE_INTERRUPTED: '服务在处理时中断，请重新提交。',
  CUDA_OOM: '显存不足。可以释放其他应用占用的显存，或改用 1024。',
  NO_SPLIT: '这个图层没有可拆开的部件，请选择其他图层。',
  SPLIT_REJECTED: '模型输出的子层还原不出原图层（多为整幅半透明的色调或纹理层），已保留原图层。请选择含实体内容的图层。',
  ENGINE_UNAVAILABLE: '提交时推理引擎还没有安装或已停止，安装或重启服务后可重试。',
}

export const states = { queued: '排队中', running: '处理中', succeeded: '已完成', failed: '失败', cancelled: '已取消' }
export function stage(job) {
  if (job.cancel_requested && job.status === 'running') return '正在取消…'
  if (job.status === 'queued') return job.queue_position ? '队列第 ' + job.queue_position + ' 位' : '排队中'
  if (terminal(job)) return states[job.status]
  const { stage, pass_index, step, total_steps } = job.progress
  const label = {
    starting: '准备推理', loading: '加载模型', layerdiff: pass_index === 2 ? '头部细分' : '全身拆分',
    depth: '深度推理', exporting: '写入图层', validating: '校验产物', complete: '发布结果', postprocess: '二次拆分',
    captioning: '生成图注', encoding: '编码提示词', layering: '分层生成', decoding: '解码图层',
  }[stage] || '处理中'
  return step == null ? label + '…' : label + ' · ' + step + '/' + total_steps
}
