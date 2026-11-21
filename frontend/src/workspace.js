const editsKey = 'layerlens:preview:v1'

const validOffset = (value) => value === undefined || (Number.isInteger(value) && Math.abs(value) <= 20000)
export const offsetOf = (edits, layer) => [edits[layer.id]?.offsetX ?? 0, edits[layer.id]?.offsetY ?? 0]

export function loadEdits() {
  try {
    const stored = JSON.parse(localStorage.getItem(editsKey) || '{}')
    const jobs = Object.entries(stored).filter(([id, layers]) => /^[0-9a-f]{32}$/.test(id) && layers && typeof layers === 'object')
    return Object.fromEntries(jobs.slice(-60).map(([id, layers]) => [id, Object.fromEntries(
      Object.entries(layers).filter(([layer, edit]) => /^layer-\d+$/.test(layer) && edit &&
        (edit.visible === undefined || typeof edit.visible === 'boolean') &&
        (edit.opacity === undefined || (Number.isInteger(edit.opacity) && edit.opacity >= 0 && edit.opacity <= 100)) &&
        validOffset(edit.offsetX) && validOffset(edit.offsetY)
      )
    )]))
  } catch {
    return {}
  }
}

export function storeEdits(jobs) {
  try { localStorage.setItem(editsKey, JSON.stringify(jobs)) }
  catch { /* Preview remains editable when storage is unavailable. */ }
}

export function inComposition(layer, edits, soloId) {
  return soloId ? layer.id === soloId : edits[layer.id]?.visible !== false
}

export async function exportPng(manifest, layers, edits, filename) {
  if (!layers.length) throw new Error('请先显示至少一个图层。')
  const rasters = await Promise.all(layers.map(async (layer) => {
    const image = new Image()
    image.src = layer.image
    try { await image.decode() }
    catch { throw new Error('图层加载失败，请重试。') }
    return { layer, image }
  }))
  const canvas = document.createElement('canvas')
  canvas.width = manifest.width
  canvas.height = manifest.height
  const context = canvas.getContext('2d')
  for (const { layer, image } of rasters) {
    const [x, y, right, bottom] = layer.bbox_xyxy
    context.globalAlpha = (edits[layer.id]?.opacity ?? 100) / 100
    const [dx, dy] = offsetOf(edits, layer)
    context.drawImage(image, x + dx, y + dy, right - x, bottom - y)
  }
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/png'))
  if (!blob) throw new Error('无法生成 PNG，请重试。')
  const link = document.createElement('a')
  const url = URL.createObjectURL(blob)
  link.href = url
  link.download = filename.replace(/[<>:"/\\|?*\u0000-\u001f]/g, '_')
  document.body.append(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 10000)
}
