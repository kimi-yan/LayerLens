// Sliding selection thumb for side-by-side toggle groups (.seg). The thumb is the only
// element painting the selected background, so switching options translates it instead
// of swapping colors. Tone comes from the selected button's data-thumb, else the group's.
const selectedOf = (group) =>
  group.querySelector(':scope > button[aria-pressed="true"], :scope > button[aria-checked="true"]')

function place(group, animate) {
  let thumb = group.querySelector(':scope > .seg-thumb')
  const fresh = !thumb
  if (fresh) {
    thumb = document.createElement('span')
    thumb.className = 'seg-thumb'
    thumb.setAttribute('aria-hidden', 'true')
    group.prepend(thumb)
  }
  const target = selectedOf(group)
  if (!target || !target.offsetWidth) {
    thumb.dataset.visible = 'false'
    return
  }
  const geometry = 'translate(' + target.offsetLeft + 'px,' + target.offsetTop + 'px)'
  // A re-rendered group recreates its thumb; start it where the old one sat so it still glides.
  const from = fresh ? group.segLast : null
  thumb.style.transition = 'none'
  if (from) Object.assign(thumb.style, from)
  if (!animate || from || thumb.dataset.visible !== 'true') {
    if (!from) Object.assign(thumb.style, { transform: geometry, width: target.offsetWidth + 'px', height: target.offsetHeight + 'px' })
    void thumb.offsetWidth
  }
  thumb.style.transition = ''
  thumb.dataset.tone = target.dataset.thumb || group.dataset.segTone || 'accent'
  Object.assign(thumb.style, { transform: geometry, width: target.offsetWidth + 'px', height: target.offsetHeight + 'px' })
  thumb.dataset.visible = 'true'
  group.segLast = { transform: geometry, width: thumb.style.width, height: thumb.style.height }
}

export function bindSegmented(root = document) {
  const resize = new ResizeObserver((entries) => entries.forEach((entry) => place(entry.target, false)))
  for (const group of root.querySelectorAll('.seg')) {
    place(group, false)
    resize.observe(group)
    new MutationObserver((records) => {
      if (records.some((r) => r.target !== group || r.type === 'childList')) place(group, true)
    }).observe(group, { subtree: true, childList: true, attributes: true, attributeFilter: ['aria-pressed', 'aria-checked'] })
  }
}
