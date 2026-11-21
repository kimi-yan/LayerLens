import '@fontsource-variable/inter'
import '@fontsource-variable/jetbrains-mono'
import '@fontsource-variable/noto-sans-sc'
import './studio.css'
import { bindSegmented } from './studio-segmented.js'
import { api, ApiError, stage, terminal } from './api.js'
import { $, closeAction, confirmAction, downloadBatch, lastCompleted, paintGPU, setView, state, text, toast } from './studio-state.js'
import { adjustZoom, bindWorkbench, clearProject, exportComposite, focusProjectItem, exportSelectedLayer, renderBatch, renderLayerList, renderProjectDropdown,
  renderWorkbench, resetAllLayersVisibility, resetAllLayerPositions, resetSelectedLayerPosition, resetZoom, setCanvasTool, setSelectedLayerOffset, setBackdrop, setCanvasMode, setLayerViewMode, switchProject,
  toggleMultiSelect, toggleSoloLayer, updateSelectedLayerOpacity, cancelSplit, submitSplit } from './studio-workbench.js'
import { bindTasks, refreshTasks, retryJob, selectTaskPage, setTaskFilterStatus } from './studio-tasks.js'
import { bindUpload, chooseFiles, closeNewTaskModal, openNewTaskModal, openNewTaskModalWithCurrentParams, selectModalEngine,
  submitNewTask, updateVramEstimate } from './studio-upload.js'

let revision = 0
let gpu = null
let apiReady = false
// Jobs seen as running on the previous poll, so a finish can be announced once from anywhere.
let watched = new Map()

function toggleProjectDropdown(event) {
  event.stopPropagation()
  const menu = $('project-dropdown')
  menu.classList.toggle('hidden')
  $('project-dropdown-toggle').setAttribute('aria-expanded', String(!menu.classList.contains('hidden')))
  if (menu.classList.contains('hidden')) return
  $('project-dropdown-search').value = ''
  renderProjectDropdown()
  $('project-dropdown-list').querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'center' })
  $('project-dropdown-search').focus()
}
function openFinishedTasks() {
  closeProjectDropdown()
  setView('tasks')
  setTaskFilterStatus('done')
}
function closeProjectDropdown() {
  $('project-dropdown').classList.add('hidden')
  $('project-dropdown-toggle').setAttribute('aria-expanded', 'false')
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme
  $('icon-moon').classList.toggle('hidden', theme === 'light')
  $('icon-sun').classList.toggle('hidden', theme === 'dark')
}
function toggleTheme() {
  const theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'
  applyTheme(theme)
  localStorage.setItem('layerlens:theme', theme)
}

async function update() {
  const version = ++revision
  try {
    const [jobs, capabilities] = await Promise.all([api('/api/v1/jobs'), api('/api/v1/capabilities')])
    if (version !== revision) return
    const first = !apiReady
    const changed = JSON.stringify(state.capabilities?.engines) !== JSON.stringify(capabilities.engines)
    apiReady = true
    state.jobs = jobs
    state.capabilities = capabilities
    const finished = jobs.filter((job) => watched.get(job.job_id) === 'running' && terminal(job))
    watched = new Map(jobs.filter((job) => !terminal(job)).map((job) => [job.job_id, job.status]))
    if (finished.length === 1) {
      toast(finished[0].status === 'succeeded' ? '拆层完成：' + finished[0].filename : '任务失败：' + finished[0].filename)
    } else if (finished.length > 1) toast(finished.length + ' 个任务已结束，可到任务中心查看')
    const running = jobs.find((job) => job.status === 'running')
    const strip = $('run-strip')
    strip.classList.toggle('hidden', !running)
    if (running) {
      const { step, total_steps: total } = running.progress
      const percent = total > 0 ? Math.min(100, Math.round((step ?? 0) / total * 100)) : null
      strip.dataset.indeterminate = String(percent === null)
      $('run-strip-bar').style.width = (percent ?? 35) + '%'
      strip.setAttribute('aria-valuenow', String(percent ?? 0))
      strip.title = stage(running)
    }
    if (state.view !== 'tasks') {
      text('nav-task-count', jobs.length)
      // /api/v1/jobs caps the recent list at 80 rows; at the cap the badge needs the true total.
      if (jobs.length >= 80) {
        api('/api/v1/task-center?filter=all&page=1')
          .then((center) => { if (version === revision) text('nav-task-count', center.counts.all) })
          .catch(() => {})
      }
    }
    updateVramEstimate()
    if (changed) renderWorkbench()
    if (!$('project-dropdown').classList.contains('hidden')) renderProjectDropdown()
    if (first && state.view === 'workbench') {
      const id = new URLSearchParams(location.search).get('job') || lastCompleted()?.job_id
      if (id) switchProject(id, false)
    }
    const batchId = state.view === 'workbench' ? state.project?.batch_id : (state.batchId || state.project?.batch_id)
    const batch = batchId ? await api('/api/v1/batches/' + batchId).catch(() => null) : null
    if (revision === version) { state.batch = batch; renderBatch() }
  } catch (error) {
    // Service down or HTTP errors are expected; anything else is a bug and must not vanish
    // into the "connecting" state without a trace.
    if (!(error instanceof ApiError) && error?.name !== 'TypeError' && error?.name !== 'AbortError') console.error(error)
    state.capabilities = null
    updateVramEstimate()
  }
  paintGPU(gpu)
}

async function updateGPU() {
  gpu = await api('/api/v1/system').catch(() => null)
  paintGPU(gpu)
}

function restoreURL() {
  const url = new URLSearchParams(location.search)
  const view = url.get('view') === 'tasks' ? 'tasks' : 'workbench'
  setView(view, false)
  if (view === 'workbench' && apiReady) {
    const id = url.get('job') || lastCompleted()?.job_id
    if (id) switchProject(id, false)
  }
}

Object.assign(window, {
  switchMainView: setView, toggleTheme, toggleProjectDropdown, renderLayerList, toggleMultiSelect, setLayerViewMode,
  toggleSoloLayer, resetAllLayersVisibility, updateSelectedLayerOpacity, setCanvasMode, setBackdrop, adjustZoom, resetZoom,
  setCanvasTool, setSelectedLayerOffset, resetSelectedLayerPosition, resetAllLayerPositions,
  openFinishedTasks,
  triggerQuickRerun: () => retryJob(state.project), exportComposite, exportSelectedLayer, downloadBatch,
  setTaskFilterStatus, selectTaskPage, openNewTaskModal, openNewTaskModalWithCurrentParams, closeNewTaskModal,
  chooseFiles, selectModalEngine, updateVramEstimate, submitNewTask, closeAction, confirmAction, cancelSplit, submitSplit,
})

function refreshView() {
  if (state.view === 'tasks') refreshTasks()
  else if (!state.project && lastCompleted()) switchProject(lastCompleted().job_id, false)
}

document.addEventListener('click', (event) => {
  if (!$('topbar-project-pill').contains(event.target)) closeProjectDropdown()
})
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && !$('project-dropdown').classList.contains('hidden')) {
    closeProjectDropdown()
    $('project-dropdown-toggle').focus()
    return
  }
  if ($('project-dropdown').contains(event.target) && ['ArrowDown', 'ArrowUp'].includes(event.key)) {
    event.preventDefault()
    focusProjectItem(event.key === 'ArrowDown' ? 1 : -1)
    return
  }
  if (event.target.id === 'project-dropdown-search' && event.key === 'Enter') {
    $('project-dropdown-list').querySelector('.project-item')?.click()
    return
  }
  if (event.target.closest('input, textarea, select, button, dialog, [contenteditable]') || document.querySelector('dialog:modal')) return
  if (event.key.toLowerCase() === 's' && state.view === 'workbench') toggleSoloLayer()
})
$('project-dropdown-search').addEventListener('input', renderProjectDropdown)
window.addEventListener('popstate', restoreURL)
window.addEventListener('studio-refresh', () => { update(); refreshView() })
window.addEventListener('studio-view', () => { refreshView(); renderBatch() })
applyTheme(localStorage.getItem('layerlens:theme') === 'light' ? 'light' : 'dark')
bindWorkbench()
bindUpload()
// One styled tooltip for the whole app: shows any title attribute, plus the full text of clipped
// .truncate / .clamp-2 elements, immediately and wrapped instead of the delayed native bubble.
const tip = document.createElement('div')
tip.className = 'app-tip'
tip.setAttribute('role', 'tooltip')
document.body.append(tip)
let tipTarget = null
const clipped = (el) => el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1
function tipText(el) {
  if (el.title) { el.dataset.tip = el.title; el.removeAttribute('title') }
  if (el.dataset.tip) return el.dataset.tip
  return el.matches('.truncate, .clamp-2') && clipped(el) ? el.textContent.trim() : ''
}
function hideTip() { tipTarget = null; tip.dataset.show = 'false' }
document.addEventListener('mouseover', (event) => {
  let el = event.target instanceof Element ? event.target : null
  let label = ''
  while (el && el !== document.body && !(label = tipText(el))) el = el.parentElement
  if (!label) { hideTip(); return }
  if (el === tipTarget && tip.textContent === label) return
  tipTarget = el
  const layer = el.closest('dialog[open]') || document.body
  if (tip.parentElement !== layer) layer.append(tip)
  tip.textContent = label
  tip.dataset.show = 'true'
  const box = el.getBoundingClientRect()
  const width = tip.offsetWidth
  const height = tip.offsetHeight
  const left = Math.max(8, Math.min(innerWidth - width - 8, box.left + box.width / 2 - width / 2))
  const below = box.bottom + 6 + height < innerHeight
  tip.style.left = left + 'px'
  tip.style.top = (below ? box.bottom + 6 : box.top - height - 6) + 'px'
}, true)
for (const type of ['scroll', 'mousedown', 'keydown']) document.addEventListener(type, hideTip, true)
bindTasks()
bindSegmented()
renderWorkbench()
restoreURL()
update()
updateGPU()
setInterval(() => { update(); if (state.view === 'tasks') refreshTasks() }, 2200)
setInterval(updateGPU, 5000)
