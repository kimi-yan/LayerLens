import { api, base, result } from './api.js'
import { exportPng, inComposition, loadEdits, offsetOf, storeEdits } from './workspace.js'
import { $, action, anyEngineReady, badge, engineBlocker, batchLabel, dayLabel, download, duration, engine, engineModel, engineName, escape, fullDate, jobKind, jobLayers, once, refresh, setView, state, text, thumb, toast } from './studio-state.js'

const tags = {
  'back hair': '后发', 'front hair': '前发', headwear: '头饰', face: '面部', irides: '虹膜', eyebrow: '眉毛',
  eyewhite: '眼白', eyelash: '睫毛', eyewear: '眼镜', ears: '耳朵', earwear: '耳饰', nose: '鼻子', mouth: '嘴部',
  neck: '颈部', neckwear: '颈饰', topwear: '上装', handwear: '手饰', bottomwear: '下装', legwear: '腿部服饰',
  footwear: '鞋履', tail: '尾巴', wings: '翅膀', objects: '道具',
}
const modeHelp = {
  composite: '按叠放顺序实时渲染可见图层与不透明度',
  original: '显示与分层结果对齐的原始图片',
  compare: '拖动中线比较原始图片与拆解合成',
  depth: '灰度显示模型估计的部位前后关系',
  mask: '白色为可见区域，黑色为透明，灰色为部分透明',
}
const icon = (path) => '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true">' + path + '</svg>'
const eye = (shown) => icon(shown ? '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>' :
  '<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8"/><line x1="1" y1="1" x2="23" y2="23"/>')
const actionIcons = {
  left_right: '<line x1="12" y1="3" x2="12" y2="21"/><polyline points="8 8 4 12 8 16"/><polyline points="16 8 20 12 16 16"/>',
  depth: '<polygon points="12 2 22 8 12 14 2 8 12 2"/><polyline points="2 14 12 20 22 14"/>',
  recursive: '<path d="M16 3h5v5M8 3H3v5M12 22v-8.3a4 4 0 0 0-1.172-2.872L3 3m12 6 6-6"/>',
}
const actionLabels = { left_right: '左右拆分', depth: '前后拆分', recursive: '递归细拆此层' }

const panel = { selected: null, solo: null, checked: new Set(), multi: false, list: 'list', mode: 'composite', backdrop: 'checker', zoom: 1, split: 50, childLayers: 3, pending: null,
  tool: 'move', pan: { x: 0, y: 0 }, space: false, undo: [], redo: [] }
let editHistory = loadEdits()
let projectRequest
const manifest = () => state.manifest
const edits = () => editHistory[state.project?.job_id] || {}
const layers = () => manifest()?.layers || []
const current = () => layers().find((layer) => layer.id === panel.selected)
const opacity = (layer) => edits()[layer.id]?.opacity ?? 100
const visible = (layer) => inComposition(layer, edits(), panel.solo)
// Qwen layers have no semantic labels: L3 is the third layer from the bottom, L3.2 the second
// child of a recursive split of L3.
const qwenName = (tag) => {
  const [root, ...children] = tag.slice(1).split('.')
  const base = root === '1' && !children.length ? '第 1 层 · 底' : '第 ' + root + ' 层'
  return children.reduce((label, index) => label + ' › 子层 ' + index, base)
}
const name = (layer) => /^L\d+(\.\d+)*$/.test(layer.tag) ? qwenName(layer.tag) : tags[layer.tag] || layer.tag.replace(/^(.*)-([lr01])$/, (_, part, index) =>
  (tags[part] || part) + '（' + { l: '左', r: '右', 0: '前', 1: '后' }[index] + '）')
const id = (layer) => /^layer-\d+$/.test(layer.id) ? layer.id : ''

// Project switcher: every finished job of the recent list, grouped by day, searchable by name or
// full timestamp so same-named uploads stay distinguishable.
let projectMarkup = ''
const projectHit = (job, query) => !query || (job.filename + ' ' + fullDate(job.created_at) + ' ' + engineName(job.engine_id) + ' ' + engineModel(job.engine_id)).toLowerCase().includes(query)
export function renderProjectDropdown() {
  const done = state.jobs.filter((job) => job.status === 'succeeded')
  const query = $('project-dropdown-search').value.trim().toLowerCase()
  const shown = done.filter((job) => projectHit(job, query))
  text('project-dropdown-count', query ? shown.length + ' / ' + done.length + ' 个' : '共 ' + done.length + ' 个')
  let day = ''
  const markup = shown.map((job) => {
    const active = job.job_id === state.project?.job_id
    const label = dayLabel(job.created_at)
    const heading = label !== day ? '<div class="project-day">' + escape(label) + '</div>' : ''
    day = label
    const layers = jobLayers(job)
    return heading + '<button type="button" role="option" aria-selected="' + active + '" data-project="' + escape(job.job_id) + '" class="project-item" title="' +
      escape(job.filename + ' · ' + fullDate(job.created_at)) + '"><span class="project-thumb bg-checker-sm"><img src="' + escape(thumb(job)) + '" alt="" loading="lazy"></span>' +
      '<span class="min-w-0 flex-1"><span class="flex items-center gap-2 min-w-0"><span class="text-[14px] font-medium text-main truncate">' + escape(job.filename) + '</span>' +
      (active ? '<span class="project-current">当前</span>' : '') + '</span><span class="block text-[12px] text-mute font-mono-num truncate mt-0.5">' +
      escape(fullDate(job.created_at)) + (layers ? ' · ' + escape(layers) + ' 层' : '') + ' · ' + jobKind(job) + '</span></span>' +
      badge(job.engine_id).replace('class="engine-badge"', 'class="engine-badge shrink-0"') + '</button>'
  }).join('') || '<p class="px-3 py-8 text-center text-[13px] text-mute">' + (done.length ? '没有匹配的项目' : '暂无已完成任务') + '</p>'
  const list = $('project-dropdown-list')
  if (projectMarkup === markup && list.childElementCount) return
  // The 2.2 s poll re-renders an open menu; keep keyboard focus on the same project.
  const focused = list.contains(document.activeElement) ? document.activeElement.dataset.project : null
  list.innerHTML = markup
  projectMarkup = markup
  if (focused) list.querySelector('[data-project="' + focused + '"]')?.focus({ preventScroll: true })
}
export function focusProjectItem(step) {
  const items = [...document.querySelectorAll('#project-dropdown-list .project-item')]
  if (!items.length) return
  const index = items.indexOf(document.activeElement)
  const next = index < 0 ? (step > 0 ? 0 : items.length - 1) : Math.max(0, Math.min(items.length - 1, index + step))
  items[next].focus()
  items[next].scrollIntoView({ block: 'nearest' })
}

export async function switchProject(jobId, push = true) {
  $('project-dropdown').classList.add('hidden')
  if (!/^[a-f\d]{32}$/.test(jobId)) return
  if (state.project?.job_id === jobId && state.manifest) { setView('workbench', push); return }
  projectRequest?.abort()
  const controller = new AbortController()
  projectRequest = controller
  await action(async () => {
    try {
      const job = state.jobs.find((entry) => entry.job_id === jobId) || await api(base(jobId), { signal: controller.signal })
      if (job.status !== 'succeeded') { setView('tasks', push); return }
      const info = await api(result(jobId) + '/layers', { signal: controller.signal })
      if (controller.signal.aborted) return
      Object.assign(panel, { selected: info.layers.at(-1)?.id, solo: null, multi: false, mode: 'composite', zoom: 1, split: 50, pending: null, pan: { x: 0, y: 0 }, undo: [], redo: [] })
      panel.checked.clear()
      // Alpha maps hold up to 1 MB per layer; keep only the project being viewed.
      const keep = new Set(info.layers.map((layer) => layer.image))
      for (const key of alphaCache.keys()) if (!keep.has(key)) alphaCache.delete(key)
      $('layer-search').value = ''
      state.project = job
      state.manifest = info
      setView('workbench', push)
      renderWorkbench()
      refresh()
    } catch (error) {
      if (!controller.signal.aborted) throw error
    }
  })
}

export function clearProject(deleted) {
  if (!deleted.includes(state.project?.job_id)) return
  state.project = null
  state.manifest = null
  panel.selected = null
  panel.solo = null
  panel.pending = null
  panel.undo = []
  panel.redo = []
  panel.checked.clear()
  alphaCache.clear()
  renderWorkbench()
}

function inspector() {
  const layer = current()
  text('layer-count-pill', layers().length + ' 层')
  text('inspector-layer-name', layer ? name(layer) : '—')
  text('inspector-opacity-val', layer ? opacity(layer) + '%' : '—')
  $('inspector-opacity-slider').disabled = !layer
  $('inspector-opacity-slider').value = layer ? opacity(layer) : 100
  $('btn-solo').disabled = !layer
  $('btn-solo').setAttribute('aria-pressed', String(Boolean(panel.solo)))
  const allShown = !panel.solo && layers().every((entry) => edits()[entry.id]?.visible !== false)
  $('btn-show-all').disabled = !manifest()
  $('btn-show-all').setAttribute('aria-pressed', String(Boolean(manifest()) && allShown))
  $('btn-multiselect').setAttribute('aria-pressed', String(panel.multi))
  for (const style of ['list', 'grid']) $('btn-view-' + style).setAttribute('aria-pressed', String(panel.list === style))
  const profile = engine(state.project?.engine_id)
  const available = profile?.actions || []
  const methods = profile?.declared_actions || []
  const recursive = methods.includes('recursive')
  text('contextual-action-label', state.project ? (recursive ? '递归细拆 · ' : '二次部位拆分 · ') + engineName(state.project.engine_id) : '二次拆分')
  const actions = $('contextual-action-buttons')
  actions.className = 'seg grid ' + (recursive ? 'gap-2 grid-cols-[1fr_auto]' : 'split-track gap-1 grid-cols-2')
  const limit = profile?.max_split_targets ?? 32
  const blocked = state.capabilities && state.project ? engineBlocker(state.project.engine_id) : ''
  const chosen = panel.multi ? panel.checked.size : layer ? 1 : 0
  const allowed = chosen > 0 && chosen <= limit
  if (panel.pending && !(allowed && available.includes(panel.pending))) panel.pending = null
  actions.innerHTML = methods.map((method) =>
    '<button data-split="' + method + '" role="radio" aria-checked="' + (panel.pending === method) +
    '" data-thumb="' + (method === 'recursive' ? 'info' : 'split') + '" class="split-option h-9 rounded-md text-[13px] font-medium flex items-center justify-center gap-1.5' +
    (method === 'recursive' ? ' split-recursive' : '') + '"' + (allowed && available.includes(method) ? '' : ' disabled') +
    (available.includes(method) ? '' : ' title="' + escape(blocked || (method === 'depth' ? '前后拆分需要 LaMa 权重，重新运行 See-through 安装脚本即可补上。' : '当前引擎不支持此操作。')) + '"') +
    '>' + icon(actionIcons[method]) +
    actionLabels[method] + '</button>').join('') + (recursive && profile?.split_layers ? childCountPicker(profile.split_layers) : '')
  const note = $('contextual-action-note')
  note.textContent = blocked ? '这个引擎在当前环境中' + (state.capabilities?.worker_alive === false ? '已停止' : '还没有安装') + '，结果可以查看和导出，暂时不能继续拆分。' : recursive ? (chosen > limit ? '递归细拆一次处理 ' + limit + ' 个图层，请减少勾选。' :
    '以所选图层为输入重新分层，子层原位替换该层，作为新任务排队。沿用原任务步数，通常需 5–10 分钟。') : ''
  note.classList.toggle('hidden', !note.textContent || Boolean(panel.pending))
  positionControls()
  drawMoveOverlay()
  $('split-confirm').classList.toggle('hidden', !panel.pending)
  $('split-confirm').dataset.method = panel.pending || ''
  if (panel.pending) {
    const target = panel.multi ? '已勾选的 ' + chosen + ' 个图层' : '「' + name(layer) + '」'
    const outcome = panel.pending === 'recursive' ? '拆成 ' + panel.childLayers + ' 个子层并原位替换' :
      panel.pending === 'depth' ? '按前后关系拆分' : '按左右拆分'
    text('split-summary', '将' + target + outcome + '，作为新任务加入队列，原任务结果保持不变。' + (panel.pending === 'recursive' ? '沿用原任务步数，通常需 5–10 分钟。' : ''))
    text('split-submit', '确认' + actionLabels[panel.pending].replace('此层', ''))
  }
}

function childCountPicker([low, high]) {
  const options = []
  for (let count = low; count <= high; count++) {
    options.push('<option value="' + count + '"' + (count === panel.childLayers ? ' selected' : '') + '>' + count + ' 子层</option>')
  }
  return '<select id="split-child-layers" aria-label="细拆生成的子层数" class="h-9 px-2 rounded-md surface-panel border border-hairline text-[13px] text-main">' +
    options.join('') + '</select>'
}

export function renderLayerList() {
  const query = $('layer-search').value.trim().toLowerCase()
  const host = $('layer-stack-container')
  const grid = panel.list === 'grid'
  host.className = grid ? 'flex-1 overflow-y-auto p-3 grid grid-cols-2 gap-2.5 content-start' : 'flex-1 overflow-y-auto p-2 space-y-1'
  host.innerHTML = layers().toReversed().filter((layer) => id(layer) && (name(layer) + ' ' + layer.tag).toLowerCase().includes(query)).map((layer) => {
    const active = layer.id === panel.selected
    const thumb = '<img src="' + escape(layer.image) + '" class="w-full h-full object-contain ' + (visible(layer) ? '' : 'opacity-30') + '" alt="">'
    const checkbox = panel.multi ? '<input type="checkbox" data-layer-check="' + id(layer) + '" aria-label="勾选 ' + escape(name(layer)) +
      '" class="accent-[var(--accent)] w-4 h-4 shrink-0 cursor-pointer" ' + (panel.checked.has(layer.id) ? 'checked' : '') + '>' : ''
    const label = '<span class="text-[14px] font-medium truncate ' + (visible(layer) ? 'text-main' : 'text-mute line-through') + '">' + escape(name(layer)) + '</span>'
    const op = '<span data-opacity="' + id(layer) + '" class="text-[12px] text-mute font-mono-num">' + opacity(layer) + '%</span>'
    const toggle = '<button data-layer-eye="' + id(layer) + '" aria-label="' + (visible(layer) ? '隐藏 ' : '显示 ') + escape(name(layer)) +
      '" class="w-9 h-9 rounded-md btn-ghost flex items-center justify-center shrink-0 ' + (grid ? 'absolute top-1 right-1 surface-panel' : '') + '">' + eye(visible(layer)) + '</button>'
    if (grid) return '<div class="relative rounded-lg p-2 border layer-item" data-selected="' + active +
      '"><button data-layer-pick="' + id(layer) + '" class="w-full min-w-0 text-left"><span class="block aspect-square rounded-md bg-checker-sm overflow-hidden mb-1.5">' +
      thumb + '</span><span class="flex flex-col min-w-0" title="' + escape(name(layer)) + '">' + label + op + '</span></button>' + toggle + checkbox + '</div>'
    return '<div class="group flex items-center gap-1.5 p-2 rounded-lg border layer-item" data-selected="' + active + '">' + checkbox +
      '<button data-layer-pick="' + id(layer) + '" class="flex items-center gap-2.5 flex-1 min-w-0 text-left"><span class="w-11 h-11 rounded-md bg-checker-sm border border-hairline overflow-hidden shrink-0">' +
      thumb + '</span><span class="min-w-0 flex flex-col">' + label + op + '</span></button>' + toggle + '</div>'
  }).join('') || '<div class="col-span-2 py-10 text-center text-[13px] text-mute">' + (layers().length ? '未找到匹配的图层' : '暂无图层') + '</div>'
}

const shift = (layer) => layer && panel.mode === 'composite' ? offsetOf(edits(), layer) : [0, 0]
function raster(url, layer) {
  const frame = manifest()
  const [x, y, right, bottom] = layer?.bbox_xyxy || [0, 0, frame.width, frame.height]
  const [dx, dy] = shift(layer)
  const style = 'left:' + (x + dx) / frame.width * 100 + '%;top:' + (y + dy) / frame.height * 100 + '%;width:' +
    (right - x) / frame.width * 100 + '%;height:' + (bottom - y) / frame.height * 100 + '%;opacity:' + (layer ? opacity(layer) / 100 : 1)
  return '<img class="absolute canvas-raster" alt="" draggable="false" src="' + escape(url) + '"' +
    (layer ? ' data-raster="' + id(layer) + '"' : '') + ' style="' + style + '">'
}

function renderCanvas() {
  const frame = manifest()
  $('artwork-frame').classList.toggle('hidden', !frame)
  $('studio-empty').classList.toggle('hidden', Boolean(frame))
  // A fresh checkout has the web app but no engine yet; the empty canvas says what to do next.
  const installed = !state.capabilities || anyEngineReady()
  text('studio-empty-title', installed ? '还没有拆层结果' : '还没有可用的拆层引擎')
  text('studio-empty-text', '工作台和任务中心可以正常使用。安装任一推理引擎后即可新建拆层任务。')
  $('studio-empty-text').classList.toggle('hidden', installed)
  text('studio-empty-action', installed ? '新建任务' : '查看安装方法')
  const comparing = panel.mode === 'compare' && frame
  $('compare-overlay').classList.toggle('hidden', !comparing)
  for (const button of document.querySelectorAll('.canvas-mode-btn')) {
    button.disabled = !frame || (button.dataset.mode === 'depth' && !frame.depth)
    button.setAttribute('aria-pressed', String(button.dataset.mode === panel.mode))
  }
  text('canvas-mode-hint', !frame ? '完成拆层后可在这里预览和调整' : panel.mode === 'composite' ? (panel.tool === 'move' ?
    '点选拖动图层 · 按住 Ctrl 只动当前层 · 空格平移 · Ctrl+滚轮缩放' : '拖动或滚轮平移 · Ctrl+滚轮缩放 · V 切回移动') : modeHelp[panel.mode])
  if (!frame) { $('artwork-svg-layer').replaceChildren(); return }
  $('artwork-frame').style.setProperty('--frame-ratio', frame.width / frame.height)
  applyTransform()
  const host = $('artwork-svg-layer')
  host.classList.toggle('mask-backdrop', panel.mode === 'mask')
  if (panel.mode === 'original') host.innerHTML = raster(frame.source)
  else if (panel.mode === 'depth') host.innerHTML = raster(frame.depth)
  else if (panel.mode === 'mask') host.innerHTML = current() ? raster(current().mask, current()) : ''
  else {
    host.innerHTML = frame.layers.filter(visible).map((layer) => raster(layer.image, layer)).join('')
    frame.layers.filter(visible).forEach(alphaOf)
  }
  if (comparing) {
    $('compare-original-svg').innerHTML = raster(frame.source)
    applyCompare(panel.split)
  }
  drawMoveOverlay()
  setCursor()
}

function renderRightPanel() {
  const project = state.project
  const frame = manifest()
  const cards = [
    ['psd', '彩色分层 PSD', '完整 ' + layers().length + ' 层可编辑图层', 'composite', '下载 PSD'],
    ['depth-psd', '深度分层 PSD', '各语义图层的灰度深度通道', 'depth', '下载 PSD'],
    ['png-zip', '透明 PNG 序列 (ZIP)', '全尺寸按语义标签命名导出', null, '下载 ZIP'],
  ]
  $('export-cards-container').innerHTML = frame ? cards.filter(([kind]) => project.exports[kind]).map(([kind, title, desc, mode, label]) =>
    '<div class="p-3 rounded-lg surface-elevated border border-hairline flex flex-wrap items-center justify-between gap-x-2.5 gap-y-2"><div class="min-w-[8.5rem] flex-1">' +
    '<div class="text-[14px] font-medium text-main whitespace-nowrap">' + title + '</div><div class="text-[12px] text-mute truncate mt-0.5" title="' + desc + '">' + desc +
    '</div></div><div class="flex items-center gap-1.5 shrink-0 ml-auto">' + (mode ? '<button data-preview-mode="' + mode +
      '" class="h-8 px-2.5 rounded btn-ghost text-[13px]" aria-label="预览 ' + title + '">预览</button>' : '') +
    '<button data-download="' + escape(project.exports[kind]) + '" class="h-8 px-2.5 rounded btn-outline text-[13px] font-medium inline-flex items-center" aria-label="' +
    label + ' · ' + title + '">' + label + '</button></div></div>').join('') : '<p class="text-[13px] text-mute">完成拆层后可下载</p>'
  const opts = project?.effective_options
  const details = frame?.details || {}
  const derivation = project?.derivation
  const engineRow = project ? ['推理引擎', engineName(project.engine_id) + ' · ' + engineModel(project.engine_id)] : []
  const params = !opts ? [] : project.engine_id === 'qwen' ? [
    engineRow, ['画布', frame ? frame.width + ' × ' + frame.height + ' px' : opts.resolution + ' px'],
    ['拆分层数', (derivation?.layers ? derivation.layers + ' 子层 · 递归' : opts.layers + ' 层')], ['推理步数', opts.inference_steps],
    ['CFG', opts.true_cfg_scale + (opts.cfg_normalize ? ' · 归一化' : '')], ['随机种子', opts.seed],
    ['图注语言', opts.caption_language === 'zh' ? '中文' : '英文'], ['权重', project.engine_version.replace(/^qwen-image-layered-gguf-/, 'GGUF ')],
    ...(details.prompt_tokens != null ? [['提示词 tokens', details.prompt_tokens + ' / 1024']] : []),
    ...(details.reconstruction_mae != null ? [['重组误差', details.reconstruction_mae + ' / 255']] : []),
    ...Object.entries(details.recursive_fidelity || {}).map(([tag, fit]) => ['子层还原 · ' + qwenName(tag), fit.color_error + ' / 255']),
    ...(details.dropped_empty_layers?.length ? [['省略空层', details.dropped_empty_layers.map(qwenName).join('、')]] : []),
  ] : [engineRow, ['目标分辨率', opts.resolution + ' px'],
    ['推理步数', opts.inference_steps], ['随机种子', opts.seed], ['深度分辨率', opts.resolution_depth + ' px'],
    ['Group Offload', opts.low_vram ? '已启用' : '关闭'], ['智能左右拆分', opts.tblr_split ? '已启用' : '关闭']]
  text('snapshot-duration', duration(project))
  const caption = details.prompt ? '<div class="pt-1.5 mt-1 border-t border-hairline"><div class="text-sub mb-0.5">' +
    (opts?.prompt ? '画面描述' : '自动图注') + '</div><p class="text-main leading-relaxed max-h-24 overflow-y-auto select-text">' + escape(details.prompt) + '</p></div>' : ''
  $('param-snapshot-list').innerHTML = (params.map(([key, value]) => '<div class="flex justify-between gap-2.5"><span class="text-sub shrink-0">' +
    escape(key) + '</span><span class="font-mono-num text-main text-right truncate" title="' + escape(value) + '">' + escape(value) + '</span></div>').join('') + caption) ||
    '<span class="text-mute">—</span>'
  for (const control of document.querySelectorAll('[data-needs-project]')) control.disabled = !project
  const rerunBlocked = project && state.capabilities ? engineBlocker(project.engine_id) : ''
  $('btn-quick-rerun').disabled = !project || Boolean(rerunBlocked)
  $('btn-quick-rerun').title = rerunBlocked || '使用当前参数重新推理'
  renderBatch()
}

export function renderBatch() {
  const project = state.project
  const batch = state.batch?.batch_id === project?.batch_id ? state.batch : null
  text('batch-summary', batch ? batchLabel(batch) : '—')
  $('batch-switcher-list').innerHTML = batch ? batch.jobs.map((job) => '<button data-project="' + escape(job.job_id) +
    '" class="text-left p-2 rounded-lg border border-hairline min-w-0 layer-item" data-selected="' + (job.job_id === project?.job_id) + '"' +
    (job.status === 'succeeded' ? '' : ' disabled') + '><span class="block aspect-square rounded bg-checker-sm overflow-hidden mb-1.5"><img src="' +
    escape(thumb(job)) + '" class="w-full h-full object-contain" alt=""></span><span class="block text-[13px] text-main truncate">' +
    escape(job.filename) + '</span><span class="text-[12px] text-mute">' + escape(engineName(job.engine_id)) + '</span></button>').join('') : ''
  $('batch-export-workbench').disabled = !batch?.export_url
  $('batch-export-tasks').disabled = !state.batch?.export_url
}

export function renderWorkbench() {
  text('topbar-filename', state.project?.filename || '尚无已拆解项目')
  text('topbar-filetime', state.project ? fullDate(state.project.created_at) : '')
  $('topbar-engine-badge').innerHTML = state.project ? badge(state.project.engine_id) : ''
  text('hud-meta-text', manifest() ? manifest().width + ' × ' + manifest().height + ' px · ' + state.project.effective_options.inference_steps + ' 步' : '—')
  inspector()
  renderLayerList()
  renderCanvas()
  renderRightPanel()
  setBackdrop(panel.backdrop)
}

function patchEdits(patches, persist = true) {
  const jobId = state.project.job_id
  const record = { ...edits() }
  for (const [layerId, patch] of Object.entries(patches)) record[layerId] = { ...record[layerId], ...patch }
  delete editHistory[jobId]
  editHistory = Object.fromEntries(Object.entries({ ...editHistory, [jobId]: record }).slice(-60))
  if (persist) storeEdits(editHistory)
}
const update = (layer, patch) => patchEdits({ [layer.id]: patch })

export function toggleMultiSelect() {
  panel.multi = !panel.multi
  panel.checked.clear()
  if (panel.multi && current()) panel.checked.add(panel.selected)
  inspector()
  renderLayerList()
}
export function setLayerViewMode(mode) { panel.list = mode; inspector(); renderLayerList() }
export function selectLayer(layerId) {
  if (!layers().some((layer) => layer.id === layerId)) return
  panel.selected = layerId
  if (panel.solo) panel.solo = layerId
  inspector(); renderLayerList(); renderCanvas()
}
export function toggleSoloLayer() {
  if (!current()) return
  panel.solo = panel.solo ? null : panel.selected
  panel.mode = 'composite'
  inspector(); renderLayerList(); renderCanvas()
}
export function resetAllLayersVisibility() {
  if (!manifest()) return
  panel.solo = null
  const existing = edits()
  const patches = layers().filter((layer) => existing[layer.id]?.visible === false)
    .map((layer) => ({ id: layer.id, from: { visible: false }, to: { visible: true } }))
  editHistory[state.project.job_id] = Object.fromEntries(Object.entries(existing).map(([layer, edit]) => [layer, { opacity: edit.opacity, offsetX: edit.offsetX, offsetY: edit.offsetY }]))
  storeEdits(editHistory)
  recordPaint(patches)
  inspector(); renderLayerList(); renderCanvas()
}
export function updateSelectedLayerOpacity(value) {
  const layer = current()
  if (!layer) return
  const from = { opacity: opacity(layer) }
  update(layer, { opacity: Number(value) })
  recordPaint([{ id: layer.id, from, to: { opacity: Number(value) } }], 'opacity:' + layer.id)
  text('inspector-opacity-val', value + '%')
  const label = document.querySelector('[data-opacity="' + id(layer) + '"]')
  if (label) label.textContent = value + '%'
  const image = document.querySelector('[data-raster="' + id(layer) + '"]')
  if (image) image.style.opacity = Number(value) / 100
}
export function setCanvasMode(mode) {
  if (!manifest() || (mode === 'depth' && !manifest().depth)) return
  panel.mode = mode
  renderCanvas()
}
export function setBackdrop(bg) {
  panel.backdrop = bg
  const frame = $('artwork-frame')
  frame.classList.toggle('bg-checker-lg', bg === 'checker')
  frame.style.backgroundColor = { dark: '#24272D', black: '#050505', white: '#FFFFFF' }[bg] || ''
  for (const dot of document.querySelectorAll('.backdrop-dot')) dot.setAttribute('aria-pressed', String(dot.dataset.bg === bg))
}
export function adjustZoom(delta) {
  panel.zoom = Math.max(0.25, Math.min(4, panel.zoom + delta))
  applyTransform()
  text('hud-zoom-val', Math.round(panel.zoom * 100) + '%')
}
export function resetZoom() { panel.zoom = 1; panel.pan = { x: 0, y: 0 }; adjustZoom(0) }
function applyTransform() {
  const frame = $('artwork-frame')
  frame.style.transform = 'translate(' + panel.pan.x + 'px,' + panel.pan.y + 'px) scale(' + panel.zoom + ')'
  frame.style.setProperty('--zoom', panel.zoom)
}
function applyCompare(percent) {
  panel.split = Number(percent)
  $('compare-handle').style.left = panel.split + '%'
  $('compare-left-clip').style.clipPath = 'inset(0 ' + (100 - panel.split) + '% 0 0)'
  $('compare-range').value = panel.split
}

// ---- Layer move / pan -------------------------------------------------------------------------
// Move tool follows Photoshop "auto-select" / Figma: pressing on a visible object picks the topmost
// opaque layer under the cursor and drags it; pressing inside the current layer's bounds (or with
// Ctrl/⌘ held) always drags the current layer, so a layer covered by others stays reachable.
const maxShift = 20000
const clampShift = (value) => Math.max(-maxShift, Math.min(maxShift, Math.round(value)))
const moved = (layer) => offsetOf(edits(), layer).some(Boolean)
const signed = (value) => value > 0 ? '+' + value : String(value)
const movableTargets = () => {
  if (!manifest() || panel.mode !== 'composite') return []
  const pool = panel.multi && panel.checked.size ? layers().filter((layer) => panel.checked.has(layer.id)) : [current()].filter(Boolean)
  return pool.filter(visible)
}
const movable = () => movableTargets().length > 0
const moveTool = () => panel.tool === 'move' && !panel.space && panel.mode === 'composite'
let drag = null
let hover = null
let readoutTimer

function positionControls() {
  const layer = current()
  const [x, y] = layer ? offsetOf(edits(), layer) : ['', '']
  for (const [key, value] of [['x', x], ['y', y]]) {
    const field = $('inspector-offset-' + key)
    field.disabled = !layer
    if (document.activeElement !== field) field.value = value
  }
  $('btn-reset-pos').disabled = !layer || !moved(layer)
  const movedCount = layers().filter(moved).length
  const any = movedCount > 0
  $('btn-reset-all-pos').classList.toggle('hidden', !any)
  $('pos-moved-note').classList.toggle('hidden', !any)
  text('pos-moved-note', '已移动 ' + movedCount + ' 层')
  $('export-offset-note').classList.toggle('hidden', !any)
}

function setCursor() {
  $('canvas-stage-wrapper').dataset.cursor = drag ? 'grabbing' : panel.space || panel.tool === 'pan' ? 'grab' :
    moveTool() && (hover || movable()) ? 'move' : 'default'
}

export function setCanvasTool(tool) {
  panel.tool = tool
  hover = null
  for (const button of document.querySelectorAll('.tool-btn')) button.setAttribute('aria-checked', String(button.dataset.tool === tool))
  renderCanvas()
}

// Per image: tight opaque bounds (fractions of the layer bbox) plus a downscaled alpha map for picking.
const alphaCache = new Map()
function scanAlpha(image) {
  const scale = Math.min(1, 1024 / Math.max(image.naturalWidth, image.naturalHeight))
  const w = Math.max(1, Math.round(image.naturalWidth * scale))
  const h = Math.max(1, Math.round(image.naturalHeight * scale))
  const canvas = document.createElement('canvas')
  canvas.width = w
  canvas.height = h
  const context = canvas.getContext('2d', { willReadFrequently: true })
  context.drawImage(image, 0, 0, w, h)
  const data = context.getImageData(0, 0, w, h).data
  const alpha = new Uint8Array(w * h)
  let x0 = w, y0 = h, x1 = -1, y1 = -1
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const value = data[(y * w + x) * 4 + 3]
      alpha[y * w + x] = value
      if (value > 8) {
        if (x < x0) x0 = x
        if (x > x1) x1 = x
        if (y < y0) y0 = y
        if (y > y1) y1 = y
      }
    }
  }
  return { w, h, alpha, box: x1 < 0 ? null : { x0: x0 / w, y0: y0 / h, x1: (x1 + 1) / w, y1: (y1 + 1) / h } }
}
// undefined while the layer image is still being scanned.
function alphaOf(layer) {
  const hit = alphaCache.get(layer.image)
  if (hit === 'pending') return undefined
  if (hit !== undefined) return hit
  alphaCache.set(layer.image, 'pending')
  const image = new Image()
  image.onload = () => {
    try { alphaCache.set(layer.image, scanAlpha(image)) } catch { alphaCache.set(layer.image, null) }
    drawMoveOverlay()
  }
  image.onerror = () => alphaCache.set(layer.image, null)
  image.src = layer.image
  return undefined
}
// Tight opaque bounds in canvas pixels, including the current offset; null if fully transparent.
function boundsOf(layer) {
  const box = alphaOf(layer)?.box
  if (!box) return null
  const [x, y, right, bottom] = layer.bbox_xyxy
  const [dx, dy] = offsetOf(edits(), layer)
  return { left: x + dx + box.x0 * (right - x), top: y + dy + box.y0 * (bottom - y), right: x + dx + box.x1 * (right - x), bottom: y + dy + box.y1 * (bottom - y) }
}
// Client coordinates -> canvas pixels; the frame's bounding rect already includes pan and zoom.
function canvasPoint(event) {
  const rect = $('artwork-frame').getBoundingClientRect()
  return [(event.clientX - rect.left) / rect.width * manifest().width, (event.clientY - rect.top) / rect.height * manifest().height]
}
const inside = (bounds, [px, py]) => bounds && px >= bounds.left && px < bounds.right && py >= bounds.top && py < bounds.bottom
// Like Photoshop's locked Background layer, a bottom layer covering the whole canvas is not
// auto-picked, so clicking scenery never drags the backdrop; select it in the list to move it.
function isBackdrop(layer) {
  const frame = manifest()
  const bounds = layer === layers()[0] && layers().length > 1 ? boundsOf(layer) : null
  return Boolean(bounds) && (bounds.right - bounds.left) * (bounds.bottom - bounds.top) >= frame.width * frame.height * 0.9
}
function pickAt(point) {
  const [px, py] = point
  for (const layer of layers().filter(visible).toReversed()) {
    if (isBackdrop(layer)) return null
    const map = alphaOf(layer)
    // A layer above is still being scanned: picking below it could grab the wrong layer.
    if (map === undefined) return null
    if (!map?.box || opacity(layer) === 0) continue
    const [x, y, right, bottom] = layer.bbox_xyxy
    const [dx, dy] = offsetOf(edits(), layer)
    const u = (px - x - dx) / (right - x)
    const v = (py - y - dy) / (bottom - y)
    if (u < 0 || v < 0 || u >= 1 || v >= 1) continue
    if (map.alpha[Math.floor(v * map.h) * map.w + Math.floor(u * map.w)] > 24) return layer
  }
  return null
}
// What a press at this point would drag: the current selection when the press lands inside it,
// otherwise the topmost opaque layer under the cursor (single-selection mode only).
function targetAt(event) {
  const point = canvasPoint(event)
  const targets = movableTargets()
  if (targets.some((layer) => inside(boundsOf(layer), point))) return { pick: null, targets }
  if (event.ctrlKey || event.metaKey || panel.multi) return { pick: null, targets }
  const pick = pickAt(point)
  return pick ? { pick, targets: [pick] } : { pick: null, targets }
}

const percent = (value, size) => value / size * 100 + '%'
function boxMarkup(bounds, kind, label) {
  const frame = manifest()
  const style = 'left:' + percent(bounds.left, frame.width) + ';top:' + percent(bounds.top, frame.height) + ';width:' +
    percent(bounds.right - bounds.left, frame.width) + ';height:' + percent(bounds.bottom - bounds.top, frame.height)
  const tag = label ? '<span class="move-tag" data-inside="' + (bounds.top / frame.height < 0.06) + '">' + escape(label) + '</span>' : ''
  return '<div class="move-box" data-kind="' + kind + '" style="' + style + '">' + tag + '</div>'
}
function drawMoveOverlay() {
  const host = $('move-overlay')
  if (!manifest() || panel.tool !== 'move' || panel.mode !== 'composite') { host.replaceChildren(); return }
  const targets = movableTargets()
  const selected = targets.map((layer) => {
    const bounds = boundsOf(layer)
    return bounds ? boxMarkup(bounds, 'selected', targets.length === 1 ? name(layer) : '') : ''
  }).join('')
  const hovered = hover && !drag && !targets.includes(hover) && boundsOf(hover) ? boxMarkup(boundsOf(hover), 'hover', name(hover)) : ''
  host.innerHTML = selected + hovered
}

function paintOffsets(ids) {
  for (const layerId of ids) {
    const layer = layers().find((entry) => entry.id === layerId)
    const image = document.querySelector('[data-raster="' + layerId + '"]')
    if (!layer || !image) continue
    const [x, y] = layer.bbox_xyxy
    const [dx, dy] = offsetOf(edits(), layer)
    image.style.left = (x + dx) / manifest().width * 100 + '%'
    image.style.top = (y + dy) / manifest().height * 100 + '%'
  }
  positionControls()
  drawMoveOverlay()
}

// Offset history: one entry per drag / key burst / reset, undone with Ctrl+Z.
function applyChanges(changes, side, persist = true) {
  patchEdits(Object.fromEntries(changes.map((change) => [change.id, { offsetX: change[side][0], offsetY: change[side][1] }])), persist)
  paintOffsets(changes.map((change) => change.id))
}
function record(changes, mergeKey) {
  changes = changes.filter((change) => change.from[0] !== change.to[0] || change.from[1] !== change.to[1])
  if (!changes.length) return
  const last = panel.undo.at(-1)
  const same = last && last.mergeKey === mergeKey && last.changes.map((change) => change.id).join() === changes.map((change) => change.id).join()
  if (mergeKey && same && Date.now() - last.at < 600) {
    last.changes.forEach((change, index) => { change.to = changes[index].to })
    last.at = Date.now()
  } else panel.undo = [...panel.undo, { changes, mergeKey, at: Date.now() }].slice(-100)
  panel.redo = []
}
// Opacity / visibility edits share the offset history so Ctrl+Z walks every adjustment in order.
function recordPaint(patches, mergeKey) {
  patches = patches.filter((patch) => JSON.stringify(patch.from) !== JSON.stringify(patch.to))
  if (!patches.length) return
  const last = panel.undo.at(-1)
  const same = last && last.paint && last.mergeKey === mergeKey && last.paint.map((patch) => patch.id).join() === patches.map((patch) => patch.id).join()
  if (mergeKey && same && Date.now() - last.at < 600) {
    last.paint.forEach((patch, index) => { patch.to = patches[index].to })
    last.at = Date.now()
  } else panel.undo = [...panel.undo, { paint: patches, mergeKey, at: Date.now() }].slice(-100)
  panel.redo = []
}
function step(from, side) {
  const entry = (from === 'undo' ? panel.undo : panel.redo).pop()
  if (!entry) return
  if (entry.paint) {
    const patches = entry.paint.filter((patch) => layers().some((layer) => layer.id === patch.id))
    patchEdits(Object.fromEntries(patches.map((patch) => [patch.id, side === 'from' ? patch.from : patch.to])))
    inspector(); renderLayerList(); renderCanvas()
    ;(from === 'undo' ? panel.redo : panel.undo).push(entry)
    toast((from === 'undo' ? '已撤销' : '已重做') + '显示调整 · ' + patches.length + ' 个图层')
    return
  }
  const changes = entry.changes.filter((change) => layers().some((layer) => layer.id === change.id))
  applyChanges(changes, side)
  ;(from === 'undo' ? panel.redo : panel.undo).push(entry)
  toast((from === 'undo' ? '已撤销' : '已重做') + '位置调整 · ' + changes.length + ' 个图层')
}
const undo = () => step('undo', 'from')
const redo = () => step('redo', 'to')

function nudge(dx, dy) {
  const targets = movableTargets()
  const changes = targets.map((layer) => {
    const from = offsetOf(edits(), layer)
    return { id: layer.id, from, to: [clampShift(from[0] + dx), clampShift(from[1] + dy)] }
  })
  applyChanges(changes, 'to')
  record(changes, 'key')
  const lead = targets.includes(current()) ? current() : targets[0]
  const bounds = boundsOf(lead)
  const rect = $('artwork-frame').getBoundingClientRect()
  const anchor = bounds ? [rect.left + bounds.right / manifest().width * rect.width, rect.top + bounds.bottom / manifest().height * rect.height] : [rect.right, rect.bottom]
  showReadout(lead, ...anchor)
  clearTimeout(readoutTimer)
  readoutTimer = setTimeout(() => { $('move-readout').dataset.show = 'false' }, 700)
}

export function setSelectedLayerOffset(axis, value) {
  const layer = current()
  const number = Number(value)
  if (!layer || value === '' || !Number.isFinite(number)) { positionControls(); return }
  const from = offsetOf(edits(), layer)
  const to = axis === 'x' ? [clampShift(number), from[1]] : [from[0], clampShift(number)]
  const changes = [{ id: layer.id, from, to }]
  applyChanges(changes, 'to')
  record(changes)
  $('inspector-offset-' + axis).value = axis === 'x' ? to[0] : to[1]
}
function resetPositions(targets) {
  const changes = targets.filter(moved).map((layer) => ({ id: layer.id, from: offsetOf(edits(), layer), to: [0, 0] }))
  applyChanges(changes, 'to')
  record(changes)
}
export function resetSelectedLayerPosition() { if (current()) resetPositions([current()]) }
export function resetAllLayerPositions() { resetPositions(layers()) }

function showReadout(layer, x, y) {
  const tip = $('move-readout')
  const [dx, dy] = offsetOf(edits(), layer)
  tip.textContent = 'X ' + signed(dx) + '  Y ' + signed(dy) + ' px'
  tip.style.left = Math.max(8, Math.min(x + 16, innerWidth - 160)) + 'px'
  tip.style.top = Math.max(8, Math.min(y + 18, innerHeight - 36)) + 'px'
  tip.dataset.show = 'true'
}

function startDrag(event) {
  if (!manifest() || drag || (event.button !== 0 && event.button !== 1) || event.target.closest('button, input')) return
  const middle = event.button === 1
  if (!middle && event.target.closest('#compare-overlay')) return
  let kind = middle || panel.space || panel.tool === 'pan' ? 'pan' : null
  let targets = []
  if (!kind && moveTool()) {
    const target = targetAt(event)
    if (target.pick && target.pick.id !== panel.selected) {
      selectLayer(target.pick.id)
      document.querySelector('[data-layer-pick="' + target.pick.id + '"]')?.scrollIntoView({ block: 'nearest' })
    }
    targets = target.targets
    kind = targets.length ? 'move' : null
  }
  if (!kind) return
  event.preventDefault()
  $('canvas-stage-wrapper').setPointerCapture(event.pointerId)
  hover = null
  drag = { kind, id: event.pointerId, x: event.clientX, y: event.clientY, pan: { ...panel.pan }, targets,
    from: new Map(targets.map((layer) => [layer.id, offsetOf(edits(), layer)])), axis: null, changed: false }
  $('artwork-frame').classList.add('is-dragging')
  drawMoveOverlay()
  setCursor()
}
function updateHover(event) {
  const next = moveTool() && manifest() && !event.target.closest('button, input, #compare-overlay') ? targetAt(event).pick : null
  if (next === hover) return
  hover = next
  drawMoveOverlay()
  setCursor()
}
function dragMove(event) {
  if (!drag) { updateHover(event); return }
  if (event.pointerId !== drag.id) return
  let dx = event.clientX - drag.x
  let dy = event.clientY - drag.y
  if (drag.kind === 'pan') {
    panel.pan = { x: Math.max(-4000, Math.min(4000, drag.pan.x + dx)), y: Math.max(-4000, Math.min(4000, drag.pan.y + dy)) }
    applyTransform()
    return
  }
  if (event.shiftKey) {
    if (!drag.axis && Math.max(Math.abs(dx), Math.abs(dy)) > 4) drag.axis = Math.abs(dx) >= Math.abs(dy) ? 'x' : 'y'
    if (drag.axis === 'x') dy = 0
    else if (drag.axis === 'y') dx = 0
    else { dx = 0; dy = 0 }
  } else drag.axis = null
  const ratio = manifest().width / $('artwork-frame').offsetWidth / panel.zoom
  const changes = drag.targets.map((layer) => {
    const from = drag.from.get(layer.id)
    return { id: layer.id, from, to: [clampShift(from[0] + dx * ratio), clampShift(from[1] + dy * ratio)] }
  })
  applyChanges(changes, 'to', false)
  drag.changed = true
  clearTimeout(readoutTimer)
  showReadout(drag.targets.includes(current()) ? current() : drag.targets[0], event.clientX, event.clientY)
}
function finishDrag(cancel) {
  const done = drag
  drag = null
  $('artwork-frame').classList.remove('is-dragging')
  $('move-readout').dataset.show = 'false'
  if (done.kind === 'move' && done.changed) {
    if (cancel) applyChanges(done.targets.map((layer) => ({ id: layer.id, to: done.from.get(layer.id) })), 'to', false)
    storeEdits(editHistory)
    if (!cancel) record(done.targets.map((layer) => ({ id: layer.id, from: done.from.get(layer.id), to: offsetOf(edits(), layer) })))
  }
  if (done.kind === 'pan' && cancel) { panel.pan = done.pan; applyTransform() }
  drawMoveOverlay()
  setCursor()
}
function endDrag(event) {
  // An interrupted gesture (pointercancel) reverts like Escape; only a real release commits.
  if (drag && event.pointerId === drag.id) finishDrag(event.type === 'pointercancel')
}

// Ctrl/⌘ + wheel (or trackpad pinch) zooms around the cursor; a plain wheel pans like Figma.
let wheelTimer
function wheel(event) {
  if (!manifest() || event.target.closest('#compare-overlay input') && !event.ctrlKey) return
  event.preventDefault()
  const frame = $('artwork-frame')
  frame.classList.add('is-dragging')
  clearTimeout(wheelTimer)
  wheelTimer = setTimeout(() => { if (!drag) frame.classList.remove('is-dragging') }, 160)
  if (event.ctrlKey || event.metaKey) {
    const rect = frame.getBoundingClientRect()
    const next = Math.max(0.25, Math.min(4, panel.zoom * Math.exp(-event.deltaY * 0.0025)))
    const offsetX = event.clientX - (rect.left + rect.width / 2)
    const offsetY = event.clientY - (rect.top + rect.height / 2)
    const factor = 1 - next / panel.zoom
    panel.pan = { x: panel.pan.x + offsetX * factor, y: panel.pan.y + offsetY * factor }
    panel.zoom = next
    text('hud-zoom-val', Math.round(next * 100) + '%')
  } else panel.pan = { x: Math.max(-4000, Math.min(4000, panel.pan.x - event.deltaX)), y: Math.max(-4000, Math.min(4000, panel.pan.y - event.deltaY)) }
  applyTransform()
}

const typing = (event) => event.target.closest?.('input, textarea, select, [contenteditable]')
function canvasKey(event) {
  if (state.view !== 'workbench' || !manifest() || document.querySelector('dialog:modal') || typing(event) ||
    $('project-dropdown').contains(event.target)) return
  const key = event.key.toLowerCase()
  if (key === 'escape' && drag) { finishDrag(true); return }
  if (event.ctrlKey || event.metaKey) {
    if (key === 'z' || key === 'y') {
      event.preventDefault()
      if (key === 'y' || event.shiftKey) redo()
      else undo()
    } else if (key === '0') { event.preventDefault(); resetZoom() }
    return
  }
  if (event.altKey) return
  if (event.key === ' ') {
    // The canvas owns Space (hand tool) even when a toolbar button kept focus; Enter still activates it.
    event.preventDefault()
    if (!panel.space) { panel.space = true; hover = null; drawMoveOverlay(); setCursor() }
  } else if (key === 'v') setCanvasTool('move')
  else if (key === 'h') setCanvasTool('pan')
  else if (key.startsWith('arrow') && movable()) {
    event.preventDefault()
    const size = event.shiftKey ? 10 : 1
    nudge(...{ arrowleft: [-size, 0], arrowright: [size, 0], arrowup: [0, -size], arrowdown: [0, size] }[key])
  }
}
function releaseSpace() { if (panel.space) { panel.space = false; setCursor() } }

export function cancelSplit() { panel.pending = null; inspector() }
export function submitSplit() { if (panel.pending) sendDerived(panel.pending) }

function sendDerived(method) {
  const selected = panel.multi ? layers().filter((layer) => panel.checked.has(layer.id)) : current() ? [current()] : []
  if (!selected.length) { toast('请选择要拆分的图层。'); return }
  once('split:' + state.project.job_id, async () => {
    const body = { method, target_tags: selected.map((layer) => layer.tag) }
    if (method === 'recursive') body.layers = panel.childLayers
    await api(result(state.project.job_id) + '/postprocess', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    })
    panel.pending = null
    inspector()
    setView('tasks')
    refresh()
    toast(method === 'recursive' ? '递归细拆已加入队列，完成后在任务中心打开' : '二次拆分已加入队列')
  })
}
function exportEdited(selectedOnly) {
  if (!manifest()) return
  const chosen = selectedOnly ? [current()].filter(Boolean) : layers().filter(visible)
  const stem = state.project.filename.replace(/\.[^.]+$/, '')
  action(() => exportPng(manifest(), chosen, edits(), stem + (selectedOnly ? '-' + current()?.tag : '-合成') + '.png'))
}
export const exportComposite = () => exportEdited(false)
export const exportSelectedLayer = () => exportEdited(true)

export function bindWorkbench() {
  document.addEventListener('click', (event) => {
    const button = event.target.closest('button')
    if (!button) return
    const data = button.dataset
    if (data.project) switchProject(data.project)
    if (data.layerPick) selectLayer(data.layerPick)
    if (data.layerEye) {
      const layer = layers().find((entry) => entry.id === data.layerEye)
      if (!layer) return
      if (panel.solo) panel.solo = null
      const from = { visible: edits()[layer.id]?.visible !== false }
      const to = { visible: !from.visible }
      update(layer, to)
      recordPaint([{ id: layer.id, from, to }])
      inspector(); renderLayerList(); renderCanvas()
    }
    if (data.split) { panel.pending = panel.pending === data.split ? null : data.split; inspector() }
    if (data.previewMode) setCanvasMode(data.previewMode)
    if (data.download) download(data.download)
  })
  $('layer-stack-container').addEventListener('change', (event) => {
    const key = event.target.dataset.layerCheck
    if (key) {
      if (!event.target.checked) panel.checked.delete(key)
      else if (panel.checked.size < 32) panel.checked.add(key)
      else { event.target.checked = false; toast('一次最多选择 32 个图层。') }
      inspector()
    }
  })
  $('contextual-action-buttons').addEventListener('change', (event) => {
    if (event.target.id === 'split-child-layers') { panel.childLayers = Number(event.target.value); inspector() }
  })
  $('compare-range').addEventListener('input', (event) => applyCompare(event.target.value))
  const stage = $('canvas-stage-wrapper')
  stage.addEventListener('pointerdown', startDrag)
  stage.addEventListener('pointermove', dragMove)
  stage.addEventListener('pointerleave', () => { if (!drag && hover) { hover = null; drawMoveOverlay(); setCursor() } })
  stage.addEventListener('wheel', wheel, { passive: false })
  for (const type of ['pointerup', 'pointercancel', 'lostpointercapture']) stage.addEventListener(type, endDrag)
  stage.addEventListener('auxclick', (event) => { if (event.button === 1) event.preventDefault() })
  document.addEventListener('keydown', canvasKey)
  document.addEventListener('keyup', (event) => {
    if (event.key !== ' ' || !panel.space) return
    if (!typing(event)) event.preventDefault()
    releaseSpace()
  })
  window.addEventListener('blur', releaseSpace)
}
