import { api, ApiError, base, submitFiles } from './api.js'
import { $, action, ask, engine, engineName, escape, refresh, setView, state, text, toast } from './studio-state.js'
import { resetTaskFilters } from './studio-tasks.js'

let files = []
const defaultEngine = 'qwen'
let engineId = defaultEngine
let submitting = false
const number = (id) => Number($(id).value)
const flag = (id) => $(id).checked
const advanced = { seethrough: 'extra-st-params', qwen: 'extra-qw-params' }
// Every form field returns to the value written in index.html whenever a new task is opened.
const formFields = ['st-res', 'st-depth-res', 'st-steps', 'st-seed', 'st-depth-steps', 'st-tblr', 'qw-res', 'qw-layers', 'qw-steps',
  'qw-prompt', 'qw-seed', 'qw-cfg', 'qw-caption', 'qw-negative', 'qw-cfg-normalize']
const defaults = {}

function captureDefaults() {
  for (const id of formFields) defaults[id] = $(id).type === 'checkbox' ? $(id).checked : $(id).value
}

function resetForm() {
  for (const id of formFields) {
    if ($(id).type === 'checkbox') $(id).checked = defaults[id]
    else $(id).value = defaults[id]
  }
  text('st-steps-val', $('st-steps').value + ' 步')
  $('advanced-toggle').setAttribute('aria-expanded', 'false')
  // Fall back to the other engine when only that one is installed.
  const other = defaultEngine === 'qwen' ? 'seethrough' : 'qwen'
  selectModalEngine(engine(defaultEngine)?.ready || !engine(other)?.ready ? defaultEngine : other)
  try { localStorage.removeItem('layerlens:task-draft:v1') } catch { /* Stale drafts from older builds are optional to clear. */ }
}
// Reference timings from an RTX 4070 12 GB at 640 px (docs/architecture.md): model loading plus
// seconds per step for each layer count. 7 and 8 are extrapolated from the linear growth of 3–6.
const qwenTiming = { load: 20, perStep: { 3: 12, 4: 14.2, 5: 17, 6: 19.4, 7: 22, 8: 24.5 } }
// See-through end-to-end seconds at 30 steps, both passes included (docs/architecture.md).
const stTiming = { 1024: 355, 1280: 486 }
// Layer-cap guidance is phrased against a 12 GB GPU as the reference class. 6+ is allowed but discouraged.
const layerTiers = {
  3: ['ok', '轻量', '元素较少的画面够用，速度最快，显存需求约 7.5 GiB。'],
  4: ['ok', '均衡', '显存需求约 9.5 GiB，多数显卡余量充足。'],
  5: ['ok', '推荐', '默认档位。显存峰值约 10.9 GiB，12 GB 显卡可稳定运行，但余量不大；提交前请关闭其他占用显存的程序。'],
  6: ['warn', '不建议', '显存需求约 13.4 GiB。显存不足 16 GB 的显卡会借用系统内存，速度明显变慢，并可能失败。'],
  7: ['danger', '强烈不建议', '显存需求更高（预计超过 14 GiB），显存不足 24 GB 的显卡很可能失败；层数过多也会把画面切得过碎。'],
  8: ['danger', '强烈不建议', '显存需求更高（预计超过 14 GiB），显存不足 24 GB 的显卡很可能失败；层数过多也会把画面切得过碎。'],
}
const layerTier = (layers) => layerTiers[layers] || layerTiers[8]
// Scene templates: a manual prompt measurably improves key-layer separation, so give users a
// starting point that describes each art style's layout conventions instead of a blank box.
const promptTemplates = {
  ecom: 'E-commerce product photo on a pure white background: the product, its printed label text and any promo badges are separate elements.',
  poster: 'Flat graphic poster: headline text, the main illustration subject, decorative shapes and small footer text are separate elements on a plain background.',
  sticker: 'Sticker sheet on white background: each sticker is a separate element with its own white border, plus handwritten note text.',
  scan: 'Scanned document page: paper background, printed text blocks, tables and stamp marks are separate elements.',
}

// The 2.2 s poll repaints these hints; rewriting innerHTML when nothing changed would drop text
// selections, steal button focus and re-announce the role="status" regions on every tick.
let capMarkupKey = ''
let setupMarkup = ''

function paintLayerCap() {
  const slider = $('qw-layers')
  const layers = Number(slider.value)
  const [tone, label, detail] = layerTier(layers)
  text('qw-layers-val', layers + ' 层')
  $('qw-layers-val').dataset.tone = tone
  slider.dataset.tone = tone
  slider.style.setProperty('--fill', ((layers - slider.min) / (slider.max - slider.min || 1) * 100) + '%')
  const key = layers + ':' + slider.min + ':' + slider.max
  if (key === capMarkupKey) return
  capMarkupKey = key
  const hint = $('qw-layers-hint')
  hint.dataset.tone = tone
  hint.innerHTML = '<b>' + escape(label) + '</b><span>' + escape(detail) + ' 实际输出可能更少，空层会自动省略。</span>'
  const ticks = []
  for (let value = Number(slider.min); value <= Number(slider.max); value++) {
    ticks.push('<span data-tone="' + layerTier(value)[0] + '" data-active="' + (value === layers) + '">' + value + (value === 5 ? '<i>推荐</i>' : '') + '</span>')
  }
  $('qw-layers-ticks').innerHTML = ticks.join('')
}

function error(message) {
  text('modal-error', message)
  $('modal-error').classList.toggle('hidden', !message)
}

// Object URLs for the draft thumbnails, released as soon as a file leaves the draft.
const fileThumbs = new Map()
function paintFiles() {
  for (const [file, url] of fileThumbs) {
    if (!files.includes(file)) { URL.revokeObjectURL(url); fileThumbs.delete(file) }
  }
  text('modal-upload-filename', files.length === 1 ? files[0].name : files.length ?
    '已选择 ' + files.length + ' 张图片' : '点击上传图片，或拖拽到这里')
  const bytes = files.reduce((sum, file) => sum + file.size, 0)
  text('modal-upload-meta', files.length ? (bytes / 1024 ** 2).toFixed(1) + ' MiB · 可继续点击或拖入添加' : '支持透明底 PNG · 单张最多 32 MiB')
  text('modal-upload-cta', files.length ? '继续添加' : '选择图片')
  $('modal-dropzone').dataset.filled = String(files.length > 0)
  const list = $('modal-file-list')
  list.classList.toggle('hidden', !files.length)
  list.innerHTML = files.map((file, index) => {
    if (!fileThumbs.has(file)) fileThumbs.set(file, URL.createObjectURL(file))
    return '<div class="flex justify-between items-center gap-2.5 rounded-md surface-elevated px-2.5 py-1.5">' +
      '<img src="' + fileThumbs.get(file) + '" alt="" class="w-9 h-9 rounded object-contain bg-checker-sm border border-hairline shrink-0">' +
      '<span class="text-[13px] text-main truncate min-w-0 flex-1" title="' + escape(file.name) + '">' + escape(file.name) + '</span><button data-remove-file="' +
      index + '" class="h-8 px-2.5 btn-ghost rounded text-[13px]" aria-label="移除 ' + escape(file.name) + '"' +
      (submitting ? ' disabled' : '') + '>移除</button></div>'
  }).join('')
  updateVramEstimate()
}

function addFiles(selected) {
  if (submitting) return
  const limit = state.capabilities?.max_upload_bytes ?? 32 * 1024 ** 2
  const maxFiles = state.capabilities?.max_batch ?? 20
  const rejected = []
  for (const file of selected) {
    if (!/\.(png|jpe?g|webp|bmp)$/i.test(file.name) || !file.size || file.size > limit || files.length >= maxFiles) rejected.push(file.name)
    else files.push(file)
  }
  error(rejected.length ? '无法导入“' + rejected[0] + '”。请检查格式、单张大小和批次数量。' : '')
  paintFiles()
}

export function chooseFiles() { if (!submitting) $('upload-input').click() }

function syncAdvanced() {
  const open = $('advanced-toggle').getAttribute('aria-expanded') === 'true'
  for (const [model, panel] of Object.entries(advanced)) $(panel).classList.toggle('hidden', !open || model !== engineId)
  $('advanced-toggle').setAttribute('aria-controls', advanced[engineId])
}

export function selectModalEngine(id) {
  if (submitting) return
  engineId = id
  for (const model of ['qwen', 'seethrough']) {
    $('engine-card-' + model).setAttribute('aria-pressed', String(model === id))
    $('params-' + model).classList.toggle('hidden', model !== id)
  }
  syncAdvanced()
  error('')
  updateVramEstimate()
}

// Slider and number limits follow the backend schema so the form cannot offer untested values.
function applySchema() {
  const schema = engine('qwen')?.options_schema?.properties
  if (!schema) return
  for (const [id, key] of [['qw-layers', 'layers'], ['qw-steps', 'inference_steps'], ['qw-cfg', 'true_cfg_scale'], ['qw-seed', 'seed']]) {
    if (schema[key]?.minimum !== undefined) $(id).min = schema[key].minimum
    if (schema[key]?.maximum !== undefined) $(id).max = schema[key].maximum
  }
  const resolutions = engine('qwen')?.resolutions || [640]
  $('qw-res').innerHTML = resolutions.map((value) => '<option value="' + value + '">' + value + ' px · 按原图比例</option>').join('')
}

function qwenEstimate() {
  const layers = number('qw-layers')
  const seconds = qwenTiming.load + number('qw-steps') * (qwenTiming.perStep[layers] ?? 24.5)
  const minutes = Math.max(1, Math.round(seconds / 60))
  return '参考耗时约 ' + minutes + ' 分钟/张（以 RTX 4070 12 GB 为基准' + (layers >= 7 ? '，外推估算' : '') + '，实际取决于硬件，不含排队）。'
}

function stEstimate() {
  const seconds = (stTiming[number('st-res')] ?? stTiming[1024]) * number('st-steps') / 30
  return '参考耗时约 ' + Math.max(1, Math.round(seconds / 60)) + ' 分钟/张（以 RTX 4070 12 GB 为基准，实际取决于硬件，不含排队）。'
}

// Explains an engine that cannot run yet and gives the one command that installs it.
function paintSetup(ready) {
  for (const model of ['qwen', 'seethrough']) $('engine-card-' + model).dataset.ready = String(!state.capabilities || Boolean(engine(model)?.ready))
  const note = $('engine-setup')
  const profile = engine(engineId)
  const show = Boolean(state.capabilities && profile && !ready)
  note.classList.toggle('hidden', !show)
  $('params-step').classList.toggle('hidden', show)
  if (!show) { setupMarkup = ''; return }
  const other = ['qwen', 'seethrough'].find((model) => model !== engineId && engine(model)?.ready)
  const switchHint = other ? '<p class="setup-note-foot">「' + escape(engineName(other)) + '」已经可以使用，可以先切换过去。</p>' : ''
  let markup
  if (!state.capabilities.worker_alive) {
    markup = '<p class="setup-note-title">后台任务进程已停止</p><p class="setup-note-text">重新运行 Start-LayerLens.ps1 后即可提交任务，已有结果不受影响。</p>'
  } else {
    const setup = profile.setup || {}
    const size = setup.download_gib ? '需要下载约 ' + setup.download_gib + ' GB 模型文件，占用约 ' + setup.disk_gib + ' GB 磁盘，' : ''
    markup = '<p class="setup-note-title">「' + escape(engineName(engineId)) + '」还没有安装</p>' +
      '<p class="setup-note-text">缺少' + escape((profile.missing_parts || []).map((part) => part.label).join('、') || '所需文件') + '。在项目根目录打开 PowerShell 运行下面的命令，' + size + '装好后刷新本页。</p>' +
      (setup.command ? '<div class="setup-note-command"><code>' + escape(setup.command) + '</code><button type="button" data-copy="' + escape(setup.command) +
        '" class="h-8 px-3 rounded-md btn-outline text-[13px] shrink-0">复制</button></div>' : '') +
      '<p class="setup-note-foot">完整步骤、硬件要求和常见问题见 ' + escape((setup.guide || 'docs/engines.md').split('#')[0]) + '。</p>' + switchHint
  }
  if (markup !== setupMarkup) { note.innerHTML = markup; setupMarkup = markup }
}

export function updateVramEstimate() {
  const profile = engine(engineId)
  const ready = profile?.ready
  text('vram-estimate-text', ready ? profile.runtime : state.capabilities ? (state.capabilities.worker_alive ? '未安装，暂时不能提交' : '任务进程已停止') : '正在连接…')
  $('vram-dot').style.background = ready ? 'var(--accent)' : state.capabilities ? 'var(--warn)' : 'var(--text-muted)'
  paintSetup(ready)
  if (engine('qwen')) text('qw-runtime', engine('qwen').runtime)
  paintLayerCap()
  text('qw-steps-val', $('qw-steps').value + ' 步')
  text('qw-estimate', qwenEstimate())
  text('st-estimate', stEstimate())
  $('submit-task').disabled = !ready || !files.length || submitting
  text('submit-task', submitting ? '正在提交…' : '开始拆层任务')
}

export function openNewTaskModal(keepParams = false) {
  applySchema()
  if (!keepParams) resetForm()
  paintFiles()
  $('new-task-modal').showModal()
}
export function closeNewTaskModal() {
  if (!submitting) $('new-task-modal').close()
}

function fillQwen(opts) {
  for (const [id, value] of Object.entries({ 'qw-res': opts.resolution, 'qw-layers': opts.layers, 'qw-steps': opts.inference_steps,
    'qw-seed': opts.seed, 'qw-cfg': opts.true_cfg_scale, 'qw-caption': opts.caption_language })) if (value !== undefined) $(id).value = value
  $('qw-prompt').value = opts.prompt || ''
  $('qw-negative').value = (opts.negative_prompt || '').trim()
  $('qw-cfg-normalize').checked = opts.cfg_normalize !== false
}

export function openNewTaskModalWithCurrentParams() {
  const project = state.project
  if (!project) return
  const load = () => action(async () => {
    const response = await fetch(base(project.job_id) + '/source')
    if (!response.ok) throw new Error('无法加载原图。')
    const blob = await response.blob()
    files = [new File([blob], project.filename, { type: blob.type })]
    const opts = project.effective_options
    applySchema()
    selectModalEngine(project.engine_id)
    if (project.engine_id === 'seethrough') {
      for (const [key, value] of Object.entries({ 'st-res': opts.resolution, 'st-depth-res': opts.resolution_depth,
        'st-steps': opts.inference_steps, 'st-seed': opts.seed, 'st-depth-steps': opts.inference_steps_depth })) $(key).value = value
      $('st-tblr').checked = opts.tblr_split
      text('st-steps-val', opts.inference_steps + ' 步')
    } else {
      fillQwen(opts)
    }
    openNewTaskModal(true)
  })
  if (files.length) ask('替换当前草稿？', '将载入此任务的图片和参数。', '替换草稿', load)
  else load()
}

function invalidField(ids) {
  return ids.map((id) => $(id)).find((field) => !field.value || !field.checkValidity())
}

export async function submitNewTask(confirmed = false) {
  if ($('submit-task').disabled || submitting) return
  const invalid = invalidField(engineId === 'seethrough' ? ['st-seed'] : ['qw-seed', 'qw-cfg'])
  if (invalid) {
    if ($('advanced-toggle').getAttribute('aria-expanded') === 'false') $('advanced-toggle').click()
    invalid.reportValidity()
    return
  }
  const layers = number('qw-layers')
  if (engineId === 'qwen' && confirmed !== true && layerTier(layers)[0] !== 'ok') {
    ask('确定使用 ' + layers + ' 层？', layerTier(layers)[2] + ' 建议改回 5 层以内。', '仍然提交', () => submitNewTask(true))
    return
  }
  const opts = engineId === 'seethrough' ? {
    resolution: number('st-res'), resolution_depth: number('st-depth-res'), inference_steps: number('st-steps'),
    inference_steps_depth: number('st-depth-steps'), seed: number('st-seed'), tblr_split: flag('st-tblr'), low_vram: true,
  } : {
    resolution: number('qw-res'), layers: number('qw-layers'), inference_steps: number('qw-steps'), true_cfg_scale: number('qw-cfg'),
    seed: number('qw-seed'), prompt: $('qw-prompt').value.trim(), negative_prompt: $('qw-negative').value.trim() || ' ',
    caption_language: $('qw-caption').value, cfg_normalize: flag('qw-cfg-normalize'),
  }
  submitting = true
  error('')
  paintFiles()
  try {
    const reply = await submitFiles(files, opts, engineId)
    state.batchId = reply.batch_id || null
    files = []
    $('new-task-modal').close()
    setView('tasks')
    resetTaskFilters()
    refresh()
    toast('任务已加入本地队列')
  } catch (exception) {
    const item = exception instanceof ApiError ? files[exception.detail?.file_index] : null
    const field = exception.detail?.fields?.[0]?.loc?.at(-1)
    error((item ? '“' + item.name + '” ' : '') + exception.message + (field ? '（' + field + '）' : ''))
  } finally {
    submitting = false
    paintFiles()
  }
}

export function bindUpload() {
  const dialog = $('new-task-modal')
  const dropzone = $('modal-dropzone')
  $('advanced-toggle').addEventListener('click', (event) => {
    const open = event.currentTarget.getAttribute('aria-expanded') === 'false'
    event.currentTarget.setAttribute('aria-expanded', String(open))
    syncAdvanced()
  })
  for (let step = 1; step <= 10; step++) $('st-depth-steps').add(new Option(String(step), String(step)))
  for (const id of ['qw-layers', 'qw-steps']) $(id).addEventListener('input', updateVramEstimate)
  $('qw-prompt-template').addEventListener('change', (event) => {
    const template = promptTemplates[event.target.value]
    if (template) { $('qw-prompt').value = template; event.target.value = '' }
  })
  $('upload-input').addEventListener('change', (event) => {
    addFiles(Array.from(event.target.files))
    event.target.value = ''
  })
  dialog.addEventListener('cancel', (event) => { if (submitting) event.preventDefault() })
  dialog.addEventListener('click', (event) => {
    const copy = event.target.closest('[data-copy]')
    if (copy) {
      navigator.clipboard?.writeText(copy.dataset.copy).then(() => toast('已复制安装命令'), () => toast('复制失败，请手动选中命令复制。'))
      return
    }
    const index = event.target.dataset.removeFile
    if (index !== undefined && !submitting) { files.splice(Number(index), 1); error(''); paintFiles() }
  })
  dropzone.addEventListener('keydown', (event) => {
    if (['Enter', ' '].includes(event.key)) { event.preventDefault(); chooseFiles() }
  })
  dropzone.addEventListener('dragover', (event) => { event.preventDefault(); dropzone.classList.add('is-dragover') })
  dropzone.addEventListener('dragleave', () => dropzone.classList.remove('is-dragover'))
  dropzone.addEventListener('drop', (event) => {
    event.preventDefault()
    dropzone.classList.remove('is-dragover')
    addFiles(Array.from(event.dataTransfer.files))
  })
  captureDefaults()
  selectModalEngine(defaultEngine)
}
