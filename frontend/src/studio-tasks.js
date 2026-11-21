import { api, base, errors, stage, states, terminal } from './api.js'
import { $, action, ask, badge, duration, engineBlocker, escape, fullDate, jobKind, jobLayers, once, refresh, setView, state, text, thumb, toast } from './studio-state.js'
import { clearProject, switchProject } from './studio-workbench.js'

const mapping = { all: 'all', running: 'active', done: 'succeeded', failed: 'issues', trash: 'trash' }
let filter = 'all'
let page = 1
let payload
let request
let searchTimer
let undo = []
let confirmed = new Set()
let lastMarkup = ''
let lastBulkMarkup = ''
const button = (label, data, style = 'btn-outline') =>
  '<button class="h-9 px-3 rounded-md ' + style + ' text-[13px] font-medium" ' + data + '>' + label + '</button>'

const rowState = (job) => job.deleted_at ? 'trash' : job.cancel_requested && !terminal(job) ? 'cancelling' : job.status
const chip = (kind, label) => '<span class="status-chip" data-kind="' + kind + '"><span class="status-dot"></span>' + label + '</span>'

function status(job) {
  if (job.purging) return chip('muted', '待清理')
  if (job.cancel_requested && !terminal(job)) return chip('muted', '正在取消…')
  if (job.status === 'running') {
    const { step, total_steps: total } = job.progress
    const measured = total > 0
    const percent = measured ? Math.min(100, Math.round((step ?? 0) / total * 100)) : null
    const label = stage(job).replace(/ · \d+\/\d+$/, '').replace(/…$/, '')
    return '<div class="space-y-1.5 min-w-0"><div class="flex items-center justify-between gap-2">' + chip('running', '处理中') +
      (measured ? '<span class="font-mono-num text-[12px] text-main">' + percent + '%</span>' : '') + '</div>' +
      '<div class="task-bar" ' + (measured ? 'role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="' + percent + '"' : 'data-indeterminate="true"') +
      ' aria-label="当前阶段进度"><i' + (measured ? ' style="width:' + percent + '%"' : '') + '></i></div>' +
      '<div class="text-[12px] text-mute truncate" title="' + escape(stage(job)) + '">' + escape(label) +
      (measured ? ' · <span class="font-mono-num">' + (step ?? 0) + '/' + total + '</span>' : '') + '</div></div>'
  }
  if (job.status === 'queued') {
    const ahead = job.queue_position ? job.queue_position - 1 : null
    return '<div class="space-y-1">' + chip('queued', job.queue_position ? '排队 · 第 ' + job.queue_position + ' 位' : '排队中') +
      '<div class="text-[12px] text-mute truncate" title="' + (ahead ? '前面还有 ' + ahead + ' 个任务' : '下一个开始处理') + '">' + (ahead ? '前面还有 ' + ahead + ' 个任务' : '下一个开始处理') + '</div></div>'
  }
  const failed = job.status === 'failed'
  const message = job.error ? errors[job.error.code] || job.error.message : ''
  return '<div class="min-w-0">' + chip(job.status === 'succeeded' ? 'done' : failed ? 'failed' : 'muted', states[job.status]) +
    (message ? '<div class="text-[12px] text-mute mt-1 clamp-2" title="' + escape(message) + '">' + escape(message) + '</div>' : '') + '</div>'
}

function row(job) {
  const id = escape(job.job_id)
  let actions = ''
  if (job.deleted_at) {
    actions = (job.purging ? '' : button('恢复', 'data-task="restore" data-id="' + id + '"')) +
      button('永久删除', 'data-task="purge" data-id="' + id + '"', 'btn-ghost destructive-text')
  } else if (!terminal(job)) {
    actions = button('取消', 'data-task="cancel" data-id="' + id + '"' + (job.cancel_requested ? ' disabled' : ''))
  } else {
    if (job.status === 'succeeded') actions = button('打开工作台', 'data-task="open" data-id="' + id + '"')
    else if (!['NO_SPLIT', 'SPLIT_REJECTED'].includes(job.error?.code)) {
      const blocked = engineBlocker(job.engine_id)
      actions = button('重试', 'data-task="retry" data-id="' + id + '"' + (blocked ? ' disabled title="' + escape(blocked) + '"' : ''))
    }
    actions += '<button aria-label="移到回收站" title="移到回收站" class="w-9 h-9 rounded-md btn-ghost flex items-center justify-center hover:text-[var(--danger)]" data-task="trash" data-id="' + id + '">' +
      '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/></svg></button>'
  }
  const opts = job.effective_options
  const layerCount = jobLayers(job)
  return '<div class="task-data-row grid grid-cols-12 gap-4 px-5 py-3.5 items-center hover:bg-[var(--bg-hover)] transition-colors" data-state="' + rowState(job) + '">' +
    '<div class="col-span-4 flex items-center gap-3 min-w-0">' +
    '<input type="checkbox" aria-label="勾选 ' + escape(job.filename) + '" data-task-check="' + id + '" class="accent-[var(--accent)] shrink-0"' +
    (confirmed.has(job.job_id) ? ' checked' : '') + (terminal(job) ? '' : ' disabled') + '>' +
    '<div class="w-12 h-12 rounded-lg bg-checker-sm border border-hairline overflow-hidden shrink-0">' +
    (job.purging ? '' : '<img class="w-full h-full object-contain" src="' + escape(thumb(job)) + '" alt="">') + '</div>' +
    '<div class="min-w-0"><div class="text-[14px] font-medium text-main truncate" title="' + escape(job.filename) + '">' + escape(job.filename) +
    '</div><div class="text-[13px] text-mute font-mono-num mt-0.5 truncate"><time datetime="' + escape(job.created_at) + '" title="创建时间">' +
    escape(fullDate(job.created_at)) + '</time> · ' + escape(opts.resolution) + ' px · ' + (layerCount ? escape(layerCount) + ' 层 · ' : '') +
    (job.batch_id && !job.deleted_at ? '<button class="underline" data-task="batch" data-id="' + id + '" title="设为当前批次">批次</button>' : jobKind(job)) + '</div></div></div>' +
    '<div class="col-span-2">' + badge(job.engine_id) + '</div><div class="col-span-3 min-w-0">' + status(job) + '</div>' +
    '<div class="col-span-1 text-right font-mono-num text-[13px] whitespace-nowrap ' + (job.status === 'running' ? 'text-main' : 'text-sub') + '">' +
    (job.status === 'queued' ? '<span class="text-mute">等待</span>' : duration(job)) + '</div>' +
    '<div class="col-span-2 flex items-center justify-end gap-2 flex-wrap">' + actions + '</div></div>'
}

function paintBulk() {
  const eligible = payload?.jobs.filter(terminal).map((job) => job.job_id) || []
  const selected = eligible.filter((id) => confirmed.has(id))
  const all = $('task-check-page')
  all.disabled = !eligible.length
  all.checked = !!eligible.length && selected.length === eligible.length
  all.indeterminate = selected.length > 0 && !all.checked
  const host = $('task-bulk')
  host.classList.toggle('hidden', !confirmed.size && !undo.length)
  host.classList.toggle('flex', !!confirmed.size || !!undo.length)
  const controls = (confirmed.size ? '<span class="text-[13px] text-sub">已选 ' + confirmed.size + ' 项</span>' +
    (filter === 'trash' ? button('永久删除所选', 'data-bulk="purge"', 'btn-outline destructive-text') :
      button('移到回收站', 'data-bulk="trash"')) + button('清除选择', 'data-bulk="clear"', 'btn-ghost') : '') +
    (undo.length ? button('撤销上次回收', 'data-bulk="undo"') : '')
  if (controls !== lastBulkMarkup) { host.innerHTML = controls; lastBulkMarkup = controls }
}

function paintTable() {
  if (!payload) return
  for (const [key, value] of Object.entries(mapping)) text('cnt-' + key, payload.counts[value])
  document.querySelector('.task-tab[data-status="running"]').dataset.live = String(payload.counts.active > 0)
  if ($('task-engine-filter').value === 'all') text('nav-task-count', payload.counts.all)
  const body = $('task-table-body')
  const markup = payload.jobs.map(row).join('') || '<div class="py-16 text-center text-[14px] text-mute">当前筛选条件下暂无任务记录</div>'
  if (markup !== lastMarkup) {
    const scroll = body.scrollTop
    const active = document.activeElement
    const key = body.contains(active) ? active.dataset.task : null
    const id = active?.dataset.id
    body.innerHTML = markup
    if (key && id) body.querySelector('[data-task="' + key + '"][data-id="' + id + '"]')?.focus({ preventScroll: true })
    body.scrollTop = scroll
    lastMarkup = markup
  }
  const pages = Math.max(1, Math.ceil(payload.total / payload.page_size))
  const footer = $('task-pagination')
  footer.classList.toggle('hidden', pages <= 1)
  footer.classList.toggle('flex', pages > 1)
  const controls = '<span>第 ' + page + ' / ' + pages + ' 页 · ' + payload.total + ' 项</span>' +
    button('上一页', 'data-page="-1"' + (page <= 1 ? ' disabled' : '')) +
    button('下一页', 'data-page="1"' + (page >= pages ? ' disabled' : ''))
  if (footer.innerHTML !== controls) footer.innerHTML = controls
  paintBulk()
}

export async function refreshTasks() {
  if (state.view !== 'tasks') return
  request?.abort()
  const controller = new AbortController()
  request = controller
  const params = new URLSearchParams({ filter: mapping[filter], q: $('task-search-input').value, page })
  if ($('task-engine-filter').value !== 'all') params.set('engine_id', $('task-engine-filter').value)
  try {
    const next = await api('/api/v1/task-center?' + params, { signal: controller.signal })
    if (controller.signal.aborted) return
    const last = Math.max(1, Math.ceil(next.total / next.page_size))
    if (page > last) { page = last; await refreshTasks(); return }
    payload = next
    paintTable()
  } catch (error) {
    if (!controller.signal.aborted) {
      lastMarkup = ''
      $('task-table-body').textContent = error.message
    }
  }
}

export function setTaskFilterStatus(statusFilter) {
  if (!Object.hasOwn(mapping, statusFilter)) return
  filter = statusFilter
  page = 1
  confirmed.clear()
  for (const tab of document.querySelectorAll('.task-tab')) tab.setAttribute('aria-pressed', String(tab.dataset.status === filter))
  refreshTasks()
}
export function resetTaskFilters() {
  $('task-engine-filter').value = 'all'
  $('task-search-input').value = ''
  setTaskFilterStatus('all')
}
export function selectTaskPage(checked) {
  for (const job of payload.jobs) {
    if (!terminal(job)) continue
    if (!checked) confirmed.delete(job.job_id)
    else if (confirmed.size < 200) confirmed.add(job.job_id)
  }
  paintTable()
}

function changeJobs(actionName, ids, singleName) {
  if (!ids.length) return
  const label = actionName === 'purge' ? '永久删除' : actionName === 'trash' ? '移到回收站' : '恢复'
  const execute = async () => {
    const response = await api('/api/v1/task-center/' + actionName, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids }),
    })
    const affected = response?.purged || ids
    for (const id of affected) confirmed.delete(id)
    if (actionName === 'trash') undo = affected
    else undo = undo.filter((id) => !affected.includes(id))
    if (actionName !== 'restore') {
      state.jobs = state.jobs.filter((job) => !affected.includes(job.job_id))
      clearProject(affected)
    }
    if (response?.failed?.length) toast('已清理 ' + affected.length + ' 项，' + response.failed.length + ' 项可从回收站重试。')
    else toast(label + ' · ' + affected.length + ' 项')
    refresh()
  }
  if (actionName === 'restore') { action(execute); return }
  const target = singleName ? '“' + singleName + '”' : ids.length + ' 项任务'
  ask(label + ' ' + target + '？', actionName === 'purge' ?
    '将永久删除原图、结果、缓存和任务记录。此操作无法撤销。' : '移到回收站后，可在回收站恢复。', label, execute)
}

export function retryJob(job) {
  if (!job) return
  once('retry:' + job.job_id, async () => {
    await api(base(job.job_id) + '/retry', { method: 'POST' })
    setView('tasks')
    resetTaskFilters()
    refresh()
    toast('已按原参数提交任务')
  })
}

export function bindTasks() {
  const search = $('task-search-input')
  search.maxLength = 150
  search.addEventListener('input', () => {
    page = 1
    confirmed.clear()
    clearTimeout(searchTimer)
    searchTimer = setTimeout(refreshTasks, 150)
  })
  const engines = $('task-engine-filter')
  engines.addEventListener('change', () => { page = 1; confirmed.clear(); refreshTasks() })
  $('task-table-body').addEventListener('change', (event) => {
    const id = event.target.dataset.taskCheck
    if (!id) return
    if (event.target.checked && confirmed.size >= 200) { event.target.checked = false; toast('一次可操作 200 项任务。'); return }
    if (event.target.checked) confirmed.add(id)
    else confirmed.delete(id)
    paintBulk()
  })
  $('view-tasks').addEventListener('click', (event) => {
    const data = event.target.closest('button')?.dataset
    if (!data) return
    if (data.page) { page += Number(data.page); $('task-table-body').scrollTop = 0; refreshTasks() }
    if (data.bulk === 'clear') { confirmed.clear(); paintTable() }
    if (data.bulk === 'undo') changeJobs('restore', undo)
    if (data.bulk === 'trash' || data.bulk === 'purge') changeJobs(data.bulk, [...confirmed])
    const job = payload?.jobs.find((entry) => entry.job_id === data.id)
    if (!job) return
    if (data.task === 'open') switchProject(job.job_id)
    if (data.task === 'retry') retryJob(job)
    if (data.task === 'batch') { state.batchId = job.batch_id; refresh(); toast('已选中该任务的批次') }
    if (data.task === 'cancel') ask('取消任务？', '将停止处理“' + job.filename + '”，保留原图供重试。', '确认取消', async () => {
      await api(base(job.job_id) + '/cancel', { method: 'POST' }); refresh()
    })
    if (['restore', 'trash', 'purge'].includes(data.task)) changeJobs(data.task, [job.job_id], job.filename)
  })
  setTaskFilterStatus('all')
}
