import { ApiError, terminal } from './api.js'

export const $ = (id) => document.getElementById(id)
export const text = (id, value) => { $(id).textContent = value }
export const escape = (value) => String(value ?? '').replace(/[&<>"']/g, (char) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char])
export const state = {
  view: 'workbench', jobs: [], project: null, manifest: null, batch: null, capabilities: null,
}
export const engine = (id) => state.capabilities?.engines.find((item) => item.id === id)
// Users pick engines by what they are good at; the model name is secondary detail.
const engineLabels = { seethrough: '二次元角色拆层', qwen: '通用拆层' }
export const engineName = (id) => engineLabels[id] || engine(id)?.name || id
// Why an engine cannot take new tasks right now, in words a user can act on; '' when it can.
// Engines are installed separately from the web app (docs/engines.md), so "not installed" is a normal state.
export function engineBlocker(id) {
  if (!state.capabilities) return '正在连接服务…'
  const item = engine(id)
  if (!item) return '当前服务没有提供这个引擎。'
  if (item.ready) return ''
  if (!state.capabilities.worker_alive) return '后台任务进程已停止，重启服务后才能提交。'
  return '尚未安装，缺少' + (item.missing_parts || []).map((part) => part.label).join('、')
}
export const anyEngineReady = () => (state.capabilities?.engines || []).some((item) => item.ready)
export const engineModel = (id) => engine(id)?.name || (id === 'qwen' ? 'Qwen-Image-Layered' : 'See-through V3')
export const thumb = (job) => '/api/v1/jobs/' + job.job_id + '/thumbnail'
export const lastCompleted = () => state.jobs.find((job) => job.status === 'succeeded')
export const badge = (id) => '<span class="engine-badge" data-engine="' + escape(id) + '" title="模型：' + escape(engineModel(id)) + '">' +
  escape(engineName(id)) + '</span>'
const pad = (value) => String(value).padStart(2, '0')
const dayKey = (date) => date.getFullYear() + '-' + pad(date.getMonth() + 1) + '-' + pad(date.getDate())
// 2026-11-21 13:04:02 in local time; same-named uploads are told apart by this stamp.
export const fullDate = (iso) => {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? '—' : dayKey(date) + ' ' + pad(date.getHours()) + ':' + pad(date.getMinutes()) + ':' + pad(date.getSeconds())
}
export function dayLabel(iso) {
  const date = new Date(iso)
  const today = new Date()
  const yesterday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1)
  if (dayKey(date) === dayKey(today)) return '今天'
  if (dayKey(date) === dayKey(yesterday)) return '昨天'
  return dayKey(date)
}
export const jobLayers = (job) => job.derivation?.layers ?? job.effective_options?.layers
export const jobKind = (job) => job.derivation ? (job.derivation.method === 'recursive' ? '递归细拆' : '二次拆分') : job.batch_id ? '批次' : '标准拆层'
export function duration(job) {
  if (!job?.started_at) return '—'
  const seconds = Math.max(0, Math.floor(((job.finished_at ? Date.parse(job.finished_at) : Date.now()) - Date.parse(job.started_at)) / 1000))
  return seconds < 60 ? seconds + ' 秒' : Math.floor(seconds / 60) + ' 分 ' + seconds % 60 + ' 秒'
}

let toastTimer
export function toast(message) {
  const bubble = document.createElement('div')
  bubble.className = 'rounded-lg float-hud text-main text-[14px] px-5 py-3 whitespace-normal'
  bubble.textContent = message
  $('toast-container').replaceChildren(bubble)
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => bubble.remove(), 4500)
}

export async function action(run) {
  try { return await run() }
  catch (error) { toast(error.message || '操作失败，可重试。'); return undefined }
}

// Runs at most one request per key, so double clicks cannot create duplicate retries or splits.
const pending = new Set()
export async function once(key, run) {
  if (pending.has(key)) return undefined
  pending.add(key)
  try { return await action(run) }
  finally { pending.delete(key) }
}

export function refresh() {
  window.dispatchEvent(new Event('studio-refresh'))
}

export function setView(view, push = true) {
  state.view = view
  for (const key of ['workbench', 'tasks']) {
    const selected = key === view
    $('view-' + key).classList.toggle('hidden', !selected)
    $('nav-' + key).setAttribute('aria-pressed', String(selected))
  }
  $('topbar-project-pill').classList.toggle('md:flex', view === 'workbench')
  const url = new URL(location.href)
  if (view === 'tasks') url.searchParams.set('view', 'tasks')
  else url.searchParams.delete('view')
  if (state.project && view === 'workbench') url.searchParams.set('job', state.project.job_id)
  else if (push) url.searchParams.delete('job')
  if (push && url.href !== location.href) history.pushState(null, '', url)
  window.dispatchEvent(new Event('studio-view'))
}

const gib = (mib) => (mib / 1024).toFixed(1)

export function paintGPU(system) {
  const gpu = system?.gpu
  const engines = state.capabilities?.engines || []
  const running = state.jobs.find((job) => job.status === 'running')
  const anyReady = anyEngineReady()
  const stopped = state.capabilities && !state.capabilities.worker_alive
  const dot = $('gpu-chip').previousElementSibling
  dot.style.background = running ? 'var(--info)' : anyReady ? 'var(--accent)' : state.capabilities ? 'var(--warn)' : 'var(--text-muted)'
  dot.classList.toggle('animate-pulse', Boolean(running))
  // Without an installed engine the chip says so first; the GPU reading matters only once tasks can run.
  text('gpu-chip', !state.capabilities ? '正在连接…' : stopped ? '任务进程已停止' : !anyReady ? '未安装推理引擎' :
    gpu ? gpu.name.replace('NVIDIA GeForce ', '') + ' · ' + gib(gpu.used_mib) + '/' + Math.round(gpu.total_mib / 1024) + 'GB' : '未检测到 NVIDIA GPU')
  const heading = running ? '正在推理 · ' + engineName(running.engine_id) : stopped ? '后台任务进程已停止' : anyReady ? '推理引擎' : '还没有安装推理引擎'
  const rows = engines.map((item) => {
    const active = running?.engine_id === item.id
    const status = active ? '运行中' : item.ready ? item.runtime : stopped ? '已停止' : '未安装'
    return '<div class="flex justify-between gap-3"><span class="shrink-0 whitespace-nowrap' + (active ? ' text-main font-medium' : '') + '" title="模型：' + escape(item.name) + '">' +
      escape(engineName(item.id)) + '</span><span class="text-right ' + (item.ready ? 'text-main' : 'text-mute') + '">' + escape(status) + '</span></div>'
  }).join('')
  const hint = !state.capabilities || (anyReady && gpu) ? '' : '<p class="pt-1.5 mt-1 border-t border-hairline text-[12px] leading-relaxed">' + (stopped ?
    '重新运行 Start-LayerLens.ps1 后即可继续处理任务。' : !anyReady ? '查看、调整和导出已有结果不受影响。安装步骤见项目内的 docs/engines.md，装好后刷新页面。' :
    '读取不到显卡信息（nvidia-smi 不可用）。任务仍会提交，推理需要 NVIDIA 显卡。') + '</p>'
  const task = running ? '<div class="pt-1.5 mt-1 border-t border-hairline"><div class="text-mute">当前任务</div><div class="text-main truncate" title="' +
    escape(running.filename) + '">' + escape(running.filename) + '</div><div class="text-mute">' + escape(engine(running.engine_id)?.runtime || '') + '</div></div>' : ''
  const share = gpu ? Math.min(100, Math.round(gpu.used_mib / gpu.total_mib * 100)) : 0
  const memory = system?.memory
  const meters = gpu ? '<div class="w-full h-2 rounded-full bg-[var(--bg-hover)] mt-2 overflow-hidden"><div class="h-full rounded-full" style="width:' + share +
    '%;background:var(--accent)"></div></div><div class="flex justify-between text-[12px] text-mute font-mono-num"><span>VRAM ' + gib(gpu.used_mib) + ' / ' +
    gib(gpu.total_mib) + ' GB</span>' + (memory ? '<span>RAM ' + gib(memory.used_mib) + ' / ' + Math.round(memory.total_mib / 1024) + ' GB</span>' : '') + '</div>' : ''
  $('gpu-details').innerHTML = '<div class="font-medium text-main mb-2 flex items-center justify-between gap-2.5"><span>' + escape(heading) + '</span>' +
    (gpu ? '<span class="font-mono-num text-mute">驱动 ' + escape(gpu.driver) + '</span>' : '') + '</div>' + rows + task + meters + hint
}

export function batchLabel(batch) {
  return batch.jobs.filter((job) => job.status === 'succeeded').length + ' / ' + batch.jobs.length + ' 已完成'
}

export async function downloadBatch() {
  const batch = state.batch
  if (!batch) { toast('当前结果没有关联批次。'); return }
  if (!batch.jobs.every(terminal) || !batch.export_url) { toast('批次结束后即可下载。'); return }
  download(batch.export_url)
}

// Checks the response before handing the URL to the browser, so a missing result shows a message
// instead of saving the error body as a file. The probe is aborted once the headers arrive.
export function download(url) {
  if (!url) return undefined
  return once('download:' + url, async () => {
    const probe = new AbortController()
    const response = await fetch(url, { signal: probe.signal })
    probe.abort()
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}))
      throw new ApiError(response.status, detail.detail)
    }
    const link = document.createElement('a')
    link.href = url
    link.download = ''
    document.body.append(link)
    link.click()
    link.remove()
  })
}

let confirmation
export function ask(title, message, label, run) {
  confirmation = run
  text('action-title', title)
  text('action-message', message)
  text('action-confirm', label)
  $('action-confirm').classList.toggle('destructive', label === '永久删除')
  $('action-dialog').showModal()
}
export function closeAction() { $('action-dialog').close(); confirmation = null }
export function confirmAction() {
  const run = confirmation
  closeAction()
  if (run) action(run)
}
