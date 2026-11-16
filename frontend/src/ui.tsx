import { ReactNode, useEffect, useState } from 'react'

export const ST_C: Record<string, string> = { 草稿: 'y', 启用: 'g', 停用: 'n' }

export function Badge({ children, c = 'n', title }: { children: ReactNode; c?: string; title?: string }) {
  return <span className={'b b-' + c} title={title}>{children}</span>
}

export function Btn({ children, onClick, p, s, d, disabled, id, title, type }: {
  children: ReactNode; onClick?: () => void; p?: boolean; s?: boolean; d?: boolean; disabled?: boolean; id?: string;
  title?: string; type?: 'button' | 'submit'
}) {
  const cls = ['btn', p && 'p', s && 's', d && 'd'].filter(Boolean).join(' ')
  return <button type={type || 'button'} id={id} className={cls} onClick={onClick} disabled={disabled} title={title}>{children}</button>
}

/** 业务分组：从已有分组里选，也可输入新分组。 */
export function GroupInput({ value, onChange, options, id = 'tGrp' }: { value: string; onChange: (v: string) => void; options: string[]; id?: string }) {
  const [open, setOpen] = useState(false)
  const q = value.trim().toLowerCase()
  const shown = options.filter(g => !q || g.toLowerCase().includes(q))
  const isNew = !!value.trim() && !options.some(g => g === value.trim())
  return <div className="gcombo">
    <input type="text" id={id} value={value} autoComplete="off" placeholder="可选，从已有分组选择或输入新分组"
      onChange={e => onChange(e.target.value)} onFocus={() => setOpen(true)} onBlur={() => setTimeout(() => setOpen(false), 150)} />
    {open && <div className="combo-list">
      {shown.map(g => <div key={g} className={'combo-item' + (g === value.trim() ? ' on' : '')} onMouseDown={e => { e.preventDefault(); onChange(g); setOpen(false) }}>{g}</div>)}
      {isNew && <div className="combo-item muted">使用新分组「{value.trim()}」</div>}
      {!shown.length && !isNew && <div className="combo-empty">{options.length ? '没有匹配的分组' : '还没有分组，可直接输入'}</div>}
    </div>}
  </div>
}

export function Modal({ title, children, foot, onClose, wide, testId }: {
  title: ReactNode; children: ReactNode; foot?: ReactNode; onClose: () => void; wide?: boolean; testId?: string
}) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [onClose])
  return <>
    <div className="mask show" style={{ zIndex: 65 }} onClick={onClose} />
    <div className="modal show" id="modal" data-testid={testId} style={wide ? { width: 1120 } : undefined} role="dialog">
      <div className="dh"><span className="sp">{title}</span><a onClick={onClose} aria-label="关闭">✕</a></div>
      <div className="db">{children}</div>
      {foot && <div className="df">{foot}</div>}
    </div>
  </>
}

export function Drawer({ title, children, foot, onClose, testId }: {
  title: ReactNode; children: ReactNode; foot?: ReactNode; onClose: () => void; testId?: string
}) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape' && !document.querySelector('.modal.show')) onClose() }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [onClose])
  return <>
    <div className="mask show" onClick={onClose} />
    <div className="drawer show" id="drawer" data-testid={testId} role="dialog">
      <div className="dh"><span className="sp">{title}</span><a onClick={onClose} aria-label="关闭">✕</a></div>
      <div className="db" id="dBody">{children}</div>
      {foot && <div className="df">{foot}</div>}
    </div>
  </>
}

let toastSetter: ((s: string) => void) | null = null
export function toast(msg: string) { toastSetter?.(msg) }
export function ToastHost() {
  const [msg, setMsg] = useState('')
  const [show, setShow] = useState(false)
  useEffect(() => {
    let t: any
    toastSetter = (s: string) => { setMsg(s); setShow(true); clearTimeout(t); t = setTimeout(() => setShow(false), 2200) }
    return () => { toastSetter = null }
  }, [])
  return <div className={'toast' + (show ? ' show' : '')} id="toast" role="status">{msg}</div>
}

export function ErrBox({ lines, title }: { lines: string[] | null | undefined; title?: string }) {
  if (!lines || !lines.length) return null
  return <div className="err">{title && <b>{title}<br /></b>}{lines.map((l, i) => <div key={i}>{l}</div>)}</div>
}

export function TagBadges({ tags }: { tags?: Record<string, string[]> }) {
  if (!tags) return null
  const vals = ['platform', 'site', 'language', 'category'].flatMap(k => tags[k] || [])
  return <>{vals.map((v, i) => <span key={i} className="tg">{v}</span>)}</>
}

export function Pager({ page, total, size, onPage, onSize, sizes }: {
  page: number; total: number; size: number; onPage: (p: number) => void
  onSize?: (s: number) => void; sizes?: number[]
}) {
  const pages = Math.max(1, Math.ceil((total || 0) / size))
  if (pages <= 1 && !onSize) return null
  return <div className="pager"><span className="muted">共 {total} 条</span>
    {onSize && (sizes || [20, 50, 100]).map(s => <Btn key={s} s={size !== s} p={size === s} onClick={() => onSize(s)}>{s} 条/页</Btn>)}
    {pages > 1 && <><Btn s disabled={page <= 1} onClick={() => onPage(page - 1)}>上一页</Btn>
      <span className="muted">{page} / {pages}</span>
      <Btn s disabled={page >= pages} onClick={() => onPage(page + 1)}>下一页</Btn></>}</div>
}

export function useAsync<T>(fn: () => Promise<T>, deps: any[]): [T | undefined, () => void, any] {
  const [data, setData] = useState<T>()
  const [err, setErr] = useState<any>()
  const [n, setN] = useState(0)
  useEffect(() => {
    let alive = true
    fn().then(d => { if (alive) { setData(d); setErr(undefined) } }).catch(e => { if (alive) setErr(e) })
    return () => { alive = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, n])
  return [data, () => setN(x => x + 1), err]
}

export const TAGK: [string, string][] = [['platform', '平台'], ['site', '站点'], ['language', '语言'], ['category', '类目']]
export const splitT = (v: string) => String(v || '').split(/[,，\s]+/).map(x => x.trim()).filter(Boolean)
