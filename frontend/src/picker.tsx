import { useEffect, useMemo, useRef, useState, type RefObject, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

export type CascadeNode = {
  id: string | number
  name: string
  description?: string
  depth?: number
  disabled?: boolean
  children?: CascadeNode[]
}

export type SelectOption = { value: string; label: string; hint?: string; title?: string }

function useDismiss(open: boolean, refs: Array<RefObject<HTMLElement | null>>, close: () => void) {
  useEffect(() => {
    if (!open) return
    const down = (e: MouseEvent) => {
      const t = e.target as Node
      if (refs.some(r => r.current && r.current.contains(t))) return
      close()
    }
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') close() }
    document.addEventListener('mousedown', down)
    document.addEventListener('keydown', key)
    return () => {
      document.removeEventListener('mousedown', down)
      document.removeEventListener('keydown', key)
    }
  }, [open, close, refs])
}

function useFixedPanel(open: boolean, trig: RefObject<HTMLElement | null>, panel: RefObject<HTMLElement | null>, minWidth = 0) {
  useEffect(() => {
    if (!open) return
    const place = () => {
      const t = trig.current, el = panel.current
      if (!t || !el) return
      const r = t.getBoundingClientRect()
      const maxH = 320
      const below = window.innerHeight - r.bottom - 8
      const above = r.top - 8
      const flip = below < 200 && above > below
      const w = Math.max(r.width, minWidth, 220)
      el.style.position = 'fixed'
      el.style.left = Math.max(8, Math.min(r.left, window.innerWidth - w - 8)) + 'px'
      el.style.width = 'auto'
      el.style.minWidth = w + 'px'
      el.style.maxWidth = Math.min(760, window.innerWidth - 16) + 'px'
      el.style.zIndex = '80'
      if (flip) {
        el.style.bottom = (window.innerHeight - r.top + 4) + 'px'
        el.style.top = 'auto'
        el.style.maxHeight = Math.min(maxH, Math.max(140, above)) + 'px'
      } else {
        el.style.top = (r.bottom + 4) + 'px'
        el.style.bottom = 'auto'
        el.style.maxHeight = Math.min(maxH, Math.max(140, below)) + 'px'
      }
    }
    place()
    const id = requestAnimationFrame(place)
    window.addEventListener('resize', place)
    window.addEventListener('scroll', place, true)
    return () => {
      cancelAnimationFrame(id)
      window.removeEventListener('resize', place)
      window.removeEventListener('scroll', place, true)
    }
  }, [open, minWidth])
}

function Panel({ id, open, trigRef, onClose, children, minWidth }: {
  id: string; open: boolean; trigRef: RefObject<HTMLElement | null>; onClose: () => void
  children: ReactNode; minWidth?: number
}) {
  const panelRef = useRef<HTMLDivElement>(null)
  useFixedPanel(open, trigRef, panelRef, minWidth)
  useDismiss(open, [trigRef, panelRef], onClose)
  if (!open) return null
  return createPortal(<div id={id + 'Panel'} ref={panelRef} className="cas-panel" role="listbox">{children}</div>, document.body)
}

export type PathHit = { id: string; path: string; names: string[]; node: CascadeNode; disabled?: boolean }

export function flattenSelectable(nodes: CascadeNode[], canSelect: (n: CascadeNode) => boolean, names: string[] = []): PathHit[] {
  const out: PathHit[] = []
  for (const n of nodes) {
    const nm = [...names, n.name]
    if (canSelect(n)) out.push({ id: String(n.id), path: nm.join(' / '), names: nm, node: n, disabled: n.disabled })
    if (n.children?.length) out.push(...flattenSelectable(n.children, canSelect, nm))
  }
  return out
}

function findPath(nodes: CascadeNode[], id: string, names: string[] = [], trail: CascadeNode[] = []): { path: string; trail: CascadeNode[] } | null {
  for (const n of nodes) {
    const nm = [...names, n.name]
    const tr = [...trail, n]
    if (String(n.id) === id) return { path: nm.join(' / '), trail: tr }
    if (n.children?.length) {
      const hit = findPath(n.children, id, nm, tr)
      if (hit) return hit
    }
  }
  return null
}

export function Cascader({
  id, value, onChange, tree, disabled, placeholder = '请选择',
  leafOnly = true, selectable, empty,
}: {
  id: string
  value: string
  onChange: (v: string) => void
  tree: CascadeNode[]
  disabled?: boolean
  placeholder?: string
  leafOnly?: boolean
  selectable?: (n: CascadeNode) => boolean
  empty?: { value: string; label: string }
}) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [trail, setTrail] = useState<CascadeNode[]>([])
  const trigRef = useRef<HTMLButtonElement>(null)
  const canSelect = (n: CascadeNode) => {
    if (n.disabled) return false
    if (selectable) return selectable(n)
    return leafOnly ? !n.children?.length : true
  }
  const found = useMemo(() => (value ? findPath(tree, String(value)) : null), [tree, value])
  const label = value && empty && value === empty.value ? empty.label : (found?.path || '')

  const openPanel = () => {
    setQ('')
    setTrail(found?.trail.slice(0, -1) || [])
    setOpen(o => !o)
  }
  const close = () => setOpen(false)
  const pick = (v: string) => { onChange(v); setOpen(false) }

  const cols: CascadeNode[][] = [tree]
  for (const n of trail) {
    if (n.children?.length) cols.push(n.children)
    else break
  }

  const qn = q.trim().toLowerCase()
  const hits = useMemo(() => {
    const all = flattenSelectable(tree, canSelect)
    const extra = empty ? [{ id: empty.value, path: empty.label, names: [empty.label], node: { id: empty.value, name: empty.label }, disabled: false } as PathHit] : []
    const list = extra.concat(all)
    if (!qn) return list
    return list.filter(h => h.path.toLowerCase().includes(qn) || String(h.node.id).toLowerCase().includes(qn) || (h.node.description || '').toLowerCase().includes(qn))
  }, [tree, qn, empty, selectable, leafOnly])

  return <div className="cas">
    <button type="button" id={id} ref={trigRef} className={'cas-trig' + (open ? ' open' : '')}
      disabled={disabled} aria-expanded={open} aria-haspopup="listbox" data-value={value}
      onClick={() => { if (!disabled) openPanel() }}>
      <span className={'cas-lab' + (!label ? ' muted' : '')}>{label || placeholder}</span>
      <span className="cas-arr">▾</span>
    </button>
    <Panel id={id} open={open} trigRef={trigRef} onClose={close} minWidth={qn ? 280 : Math.min(180 * Math.max(cols.length, 1), 640)}>
      <div className="cas-search"><input id={id + 'Search'} type="text" placeholder="搜索完整路径" value={q} autoComplete="off"
        autoFocus onChange={e => setQ(e.target.value)} /></div>
      {qn ? <div className="cas-hits">
        {hits.length ? hits.map(h => <div key={h.id} className={'cas-item' + (String(value) === h.id ? ' on' : '') + (h.disabled ? ' disabled' : '')}
          data-value={h.id} data-pick={h.path} role="option" onMouseDown={e => { e.preventDefault(); if (!h.disabled) pick(h.id) }}>
          <div className="cas-name">{h.path}</div>
        </div>) : <div className="cas-empty">没有匹配的分类</div>}
      </div> : <div className="cas-cols">
        {empty && <div className="cas-empty-opt">
          <div className={'cas-item' + (value === empty.value ? ' on' : '')} data-value={empty.value} data-pick={empty.label}
            onMouseDown={e => { e.preventDefault(); pick(empty.value) }}>{empty.label}</div>
        </div>}
        {cols.map((list, i) => <div className="cas-col" key={i}>
          {list.length ? list.map(n => {
            const active = trail[i] && String(trail[i].id) === String(n.id)
            const sel = String(n.id) === String(value)
            const has = !!n.children?.length
            const ok = canSelect(n)
            return <div key={String(n.id)}
              className={'cas-item' + (active || sel ? ' on' : '') + (n.disabled ? ' disabled' : '') + (ok ? ' pickable' : '')}
              data-value={String(n.id)} data-pick={[...trail.slice(0, i).map(x => x.name), n.name].join(' / ')}
              onMouseEnter={() => { if (has) setTrail(t => [...t.slice(0, i), n]) }}
              onMouseDown={e => {
                e.preventDefault()
                if (has) setTrail(t => [...t.slice(0, i), n])
                if (ok) pick(String(n.id))
              }}>
              <div className="cas-name">{n.name}{n.disabled ? <span className="muted">（已停用）</span> : null}</div>
              {n.description ? <div className="cas-desc">{n.description}</div> : null}
              {has ? <span className="cas-more">›</span> : null}
            </div>
          }) : <div className="cas-empty">无下级</div>}
        </div>)}
        {!tree.length && <div className="cas-empty">暂无分类</div>}
      </div>}
    </Panel>
  </div>
}

export function SearchSelect({
  id, value, onChange, options, disabled, placeholder = '请选择', empty, className,
}: {
  id: string
  value: string
  onChange: (v: string) => void
  options: SelectOption[]
  disabled?: boolean
  placeholder?: string
  empty?: { value: string; label: string }
  className?: string
}) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const trigRef = useRef<HTMLButtonElement>(null)
  const all = (empty ? [{ value: empty.value, label: empty.label }, ...options] : options)
  const cur = all.find(o => o.value === value)
  const qn = q.trim().toLowerCase()
  const shown = qn ? all.filter(o => o.label.toLowerCase().includes(qn) || o.value.toLowerCase().includes(qn) || (o.hint || '').toLowerCase().includes(qn)) : all
  const close = () => setOpen(false)
  const pick = (v: string) => { onChange(v); setOpen(false) }
  return <div className={'cas' + (className ? ' ' + className : '')}>
    <button type="button" id={id} ref={trigRef} className={'cas-trig' + (open ? ' open' : '')}
      disabled={disabled} aria-expanded={open} aria-haspopup="listbox" data-value={value}
      title={cur?.title || cur?.hint || undefined}
      onClick={() => { if (!disabled) { setQ(''); setOpen(o => !o) } }}>
      <span className={'cas-lab' + (!cur ? ' muted' : '')}>{cur?.label || placeholder}</span>
      <span className="cas-arr">▾</span>
    </button>
    <Panel id={id} open={open} trigRef={trigRef} onClose={close}>
      <div className="cas-search"><input id={id + 'Search'} type="text" placeholder="搜索" value={q} autoComplete="off"
        autoFocus onChange={e => setQ(e.target.value)} /></div>
      <div className="cas-hits">
        {shown.length ? shown.map(o => <div key={o.value} className={'cas-item' + (o.value === value ? ' on' : '')}
          data-value={o.value} data-pick={o.label} role="option" title={o.title || o.hint || undefined}
          onMouseDown={e => { e.preventDefault(); pick(o.value) }}>
          <div className="cas-name">{o.label}</div>
          {o.hint ? <div className="cas-desc">{o.hint}</div> : null}
        </div>) : <div className="cas-empty">没有匹配项</div>}
      </div>
    </Panel>
  </div>
}
