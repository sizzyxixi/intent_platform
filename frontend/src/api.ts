export class ApiError extends Error {
  status: number
  code: string
  details: Record<string, any>
  constructor(status: number, code: string, message: string, details: Record<string, any> = {}) {
    super(message)
    this.status = status
    this.code = code
    this.details = details
  }
}

let onUnauthorized: () => void = () => {}
export function setUnauthorizedHandler(fn: () => void) { onUnauthorized = fn }

async function request<T>(method: string, url: string, body?: any): Promise<T> {
  const init: RequestInit = { method, credentials: 'same-origin', headers: {} }
  if (body instanceof FormData) init.body = body
  else if (body !== undefined) {
    init.body = JSON.stringify(body)
    ;(init.headers as Record<string, string>)['Content-Type'] = 'application/json'
  }
  const r = await fetch('/api/admin' + url, init)
  const ct = r.headers.get('content-type') || ''
  const data = ct.includes('application/json') ? await r.json() : await r.text()
  if (!r.ok) {
    if (r.status === 401 && !url.startsWith('/auth/login')) onUnauthorized()
    const d = typeof data === 'object' && data ? data : {}
    throw new ApiError(r.status, d.code || 'ERROR', d.message || `请求失败（${r.status}）`, d.details || {})
  }
  return data as T
}

export const api = {
  get: <T = any>(u: string) => request<T>('GET', u),
  post: <T = any>(u: string, b?: any) => request<T>('POST', u, b ?? {}),
  put: <T = any>(u: string, b?: any) => request<T>('PUT', u, b ?? {}),
  patch: <T = any>(u: string, b?: any) => request<T>('PATCH', u, b ?? {}),
  del: <T = any>(u: string) => request<T>('DELETE', u),
}

export function qs(o: Record<string, any>) {
  const p = new URLSearchParams()
  Object.entries(o).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== '') p.set(k, String(v)) })
  const s = p.toString()
  return s ? '?' + s : ''
}

/** 把错误整理成多行文本（含 details.problems / errors）。 */
export function errLines(e: any): string[] {
  if (!(e instanceof ApiError)) return [String(e?.message || e)]
  const d = e.details || {}
  if (Array.isArray(d.problems) && d.problems.length) return d.problems
  if (Array.isArray(d.errors) && d.errors.length) return d.errors.map((x: any) => x.message || String(x))
  return [e.message]
}

/** UTC ISO → 北京时间 "YYYY-MM-DD HH:mm:ss" */
export function fmt(iso?: string | null, withSec = true): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  const t = new Date(d.getTime() + 8 * 3600 * 1000)
  const p = (n: number) => String(n).padStart(2, '0')
  const s = `${t.getUTCFullYear()}-${p(t.getUTCMonth() + 1)}-${p(t.getUTCDate())} ${p(t.getUTCHours())}:${p(t.getUTCMinutes())}`
  return withSec ? s + ':' + p(t.getUTCSeconds()) : s
}

export function download(name: string, text: string, type = 'application/json') {
  const a = document.createElement('a')
  a.href = URL.createObjectURL(new Blob([text], { type: type + ';charset=utf-8' }))
  a.download = name
  document.body.appendChild(a)
  a.click()
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove() }, 100)
}
