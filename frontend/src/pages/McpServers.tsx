import { useState } from 'react'
import { api, errLines, fmt } from '../api'
import { useSession } from '../session'
import { Badge, Btn, ErrBox, Modal, toast } from '../ui'

export const TRANSPORTS: [string, string][] = [['streamable_http', 'Streamable HTTP'], ['sse', 'SSE（旧版）'], ['stdio', 'stdio（本地进程）']]
export const CRED_LOC: Record<string, string> = { header: '请求头', query: 'URL 参数', env: '环境变量' }

export type Cred = { location: string; name: string; env: string; scheme?: string; secret?: string; has_secret?: boolean }

/** 通用预设（不含任何具体厂商）：只决定位置 / 名称 / 环境变量名，密钥值不在此填写 */
export const CRED_PRESETS: [string, string, Cred[]][] = [
  ['bearer', 'Bearer Token', [{ location: 'header', name: 'Authorization', env: 'MCP_TOKEN', scheme: 'Bearer' }]],
  ['apikey', 'API Key 请求头', [{ location: 'header', name: 'X-API-Key', env: 'MCP_API_KEY' }]],
  ['appid', 'App ID + App Key', [{ location: 'header', name: 'X-App-Id', env: 'MCP_APP_ID' }, { location: 'header', name: 'X-App-Key', env: 'MCP_APP_KEY' }]],
  ['query', 'URL 参数', [{ location: 'query', name: 'api_key', env: 'MCP_API_KEY' }]],
]

export function serverEnd(sv: any) {
  return sv.transport === 'stdio' ? [sv.command, ...(sv.args || [])].join(' ') : sv.url
}

export function credText(sv: any) {
  const cs: Cred[] = sv.credentials || []
  if (!cs.length) return '无鉴权'
  return cs.map(c => `${CRED_LOC[c.location] || c.location} ${c.name}${c.scheme ? '（' + c.scheme + '）' : ''} ← $${c.env}`).join('；')
}

export function emptyMcp(owner: string) {
  return { name: '', display_name: '', description: '', transport: 'streamable_http', url: '', command: '', args: '', env: '', owner, credentials: [] as Cred[] }
}

export function mcpFormOf(sv: any) {
  return {
    name: sv.name, display_name: sv.display_name, description: sv.description || '', transport: sv.transport, url: sv.url || '',
    command: sv.command || '', args: (sv.args || []).join(' '), env: Object.entries(sv.env || {}).map(([k, v]) => `${k}=${v}`).join('\n'),
    owner: sv.owner, credentials: (sv.credentials || []).map((c: any) => ({ ...c, secret: '' })),
  }
}

export function mcpBody(f: any) {
  const env: Record<string, string> = {}
  String(f.env || '').split('\n').map((l: string) => l.trim()).filter(Boolean).forEach((l: string) => { const i = l.indexOf('='); env[i < 0 ? l : l.slice(0, i).trim()] = i < 0 ? '' : l.slice(i + 1).trim() })
  const credentials = (f.credentials as Cred[]).map(c => {
    const o: any = { location: c.location, name: c.name.trim(), env: c.env.trim() }
    if (c.scheme) o.scheme = c.scheme
    if (c.secret) o.secret = c.secret
    return o
  })
  return { name: f.name.trim(), display_name: f.display_name, description: f.description, transport: f.transport, url: f.url, command: f.command, args: f.args, env, owner: f.owner, credentials }
}

/** MCP 服务表单（嵌在「注册工具」抽屉里，类型 = MCP） */
export function McpForm({ f, setF, editing, exampleData }: { f: any; setF: (f: any) => void; editing: boolean; exampleData?: boolean }) {
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value })
  const stdio = f.transport === 'stdio'
  const creds: Cred[] = f.credentials
  const setC = (i: number, k: keyof Cred, v: any) => setF({ ...f, credentials: creds.map((c, j) => j === i ? { ...c, [k]: v, ...(k === 'name' && v !== 'Authorization' ? { scheme: undefined } : {}) } : c) })
  const preset = (p: Cred[]) => { setF({ ...f, credentials: p.map(c => ({ ...c, location: stdio ? 'env' : c.location, name: stdio ? c.env : c.name, scheme: stdio ? undefined : c.scheme })) }); toast('已套用预设，请确认环境变量名') }
  const locs = stdio ? ['env'] : ['header', 'query']
  return <>
    <div className="muted" style={{ margin: '0 0 10px' }}>第三方 MCP 一般把一批能力打包在一个服务里。这里登记服务本身，保存后同步 tools/list，再在意图里下钻选择具体工具。</div>
    <div className="grid g2"><div className="f"><label>服务名（英文小写，唯一）*</label><input type="text" id="mN" value={f.name} disabled={editing} onChange={set('name')} placeholder="github" /></div>
      <div className="f"><label>显示名 *</label><input type="text" id="mD" value={f.display_name} onChange={set('display_name')} placeholder="GitHub MCP" /></div></div>
    <div className="f"><label>说明</label><input type="text" id="mDs" value={f.description} onChange={set('description')} /></div>
    <div className="grid g2"><div className="f"><label>传输方式 *</label><select id="mT" value={f.transport} onChange={e => setF({ ...f, transport: e.target.value, credentials: creds.map(c => ({ ...c, location: e.target.value === 'stdio' ? 'env' : (c.location === 'env' ? 'header' : c.location) })) })}>{TRANSPORTS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></div>
      <div className="f"><label>负责人 *</label><input type="text" id="mW" value={f.owner} onChange={set('owner')} /></div></div>
    {!stdio ? <div className="f"><label>服务地址 *</label><input type="text" id="mU" value={f.url} onChange={set('url')} placeholder={f.transport === 'sse' ? 'https://mcp.example.com/sse' : 'https://mcp.example.com/mcp'} />
      <div className="muted">不要把密钥写进 URL；URL 里的密钥参数保存时会被移除并转为下方凭据。</div></div>
      : <><div className="grid g2"><div className="f"><label>启动命令 *</label><input type="text" id="mC" className="mono" value={f.command} onChange={set('command')} placeholder="npx" /></div>
        <div className="f"><label>启动参数（空格分隔）</label><input type="text" id="mA" className="mono" value={f.args} onChange={set('args')} placeholder="-y @scope/server" /></div></div>
        <div className="f"><label>环境变量（每行 KEY=VALUE，不要放密钥）</label><textarea id="mE" className="mono" value={f.env} onChange={set('env')} /></div>
        <div className="muted" style={{ marginTop: -6, marginBottom: 8 }}>stdio 服务运行在 Bot 所在机器上。平台默认不在自身服务器执行命令，同步时请用「手动导入」粘贴 tools/list 结果。</div></>}
    <div className="sec" id="mCreds"><b>鉴权凭据</b>
      <div className="muted" style={{ margin: '4px 0 8px' }}>每条凭据指定位置、名称和环境变量名；导出给 Bot 时只给这三项，不含密钥值。密钥可选填，仅用于平台在线同步，加密保存、只写不读。</div>
      <div className="muted cred-presets" style={{ marginBottom: 8 }}>常用预设：{CRED_PRESETS.map(([k, l, p]) => <a key={k} data-preset={k} onClick={() => preset(p)} style={{ marginRight: 12 }}>{l}</a>)}</div>
      <table><thead><tr><th>位置</th><th>名称</th><th>环境变量</th><th>密钥（可选）</th><th></th></tr></thead><tbody>
        {creds.length ? creds.map((c, i) => <tr key={i}>
          <td><select id={'crL' + i} value={c.location} onChange={e => setC(i, 'location', e.target.value)}>{locs.map(x => <option key={x} value={x}>{CRED_LOC[x]}</option>)}</select></td>
          <td><input type="text" id={'crN' + i} className="mono" value={c.name} onChange={e => setC(i, 'name', e.target.value)} placeholder={stdio ? 'API_TOKEN' : 'X-API-Key'} style={{ width: 150 }} />
            {!stdio && c.name === 'Authorization' && <label className="muted" style={{ display: 'block', fontSize: 11 }}><input type="checkbox" checked={c.scheme === 'Bearer'} onChange={e => setC(i, 'scheme', e.target.checked ? 'Bearer' : undefined)} /> Bearer 前缀</label>}</td>
          <td><input type="text" id={'crE' + i} className="mono" value={c.env} onChange={e => setC(i, 'env', e.target.value.toUpperCase())} placeholder="ENV_NAME" style={{ width: 190 }} /></td>
          <td><input type="password" id={'crS' + i} value={c.secret || ''} onChange={e => setC(i, 'secret', e.target.value)} autoComplete="new-password" placeholder={c.has_secret ? '已设置，留空不变' : '不填则不在线同步'} style={{ width: 150 }} /></td>
          <td><a onClick={() => setF({ ...f, credentials: creds.filter((_, j) => j !== i) })}>删除</a></td></tr>)
          : <tr><td colSpan={5} className="muted">无鉴权。可添加凭据或使用上方预设。</td></tr>}</tbody></table>
      <Btn s id="crAdd" onClick={() => setF({ ...f, credentials: [...creds, { location: stdio ? 'env' : 'header', name: '', env: '' }] })}>＋ 添加凭据</Btn></div>
    {exampleData && <div className="note w" style={{ margin: '8px 0' }}>当前工具列表为示例数据，真实同步可拉取完整列表。</div>}
  </>
}

/** 同步工具面板：在线（initialize + tools/list）或手动粘贴 tools/list JSON → 差异预览 → 确认 */
export function SyncPanel({ sv, onDone }: { sv: any; onDone: () => void }) {
  const { isAdmin } = useSession()
  const [mode, setMode] = useState<'online' | 'manual'>(sv.transport === 'stdio' ? 'manual' : 'online')
  const [json, setJson] = useState('')
  const [p, setP] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string[]>()
  const run = async () => {
    setErr(undefined); setP(null); setBusy(true)
    try { setP(await api.post(`/mcp-servers/${sv.name}/sync/preview`, mode === 'manual' ? { tools_json: json } : {})) } catch (e) { setErr(errLines(e)) }
    setBusy(false)
  }
  const apply = async () => {
    try {
      const r = await api.post(`/mcp-servers/${sv.name}/sync/apply`, { tools: p.tools, source: p.source, server_info: p.server_info })
      const s = r.summary
      toast(`同步完成：新增 ${s.added} · 变更 ${s.changed} · 消失 ${s.removed}` + (r.downgraded.length ? `；${r.downgraded.length} 个启用意图转为草稿` : ''))
      setP(null); onDone()
    } catch (e) { setErr(errLines(e)) }
  }
  const d = p?.diff
  const nothing = d && !d.summary.added && !d.summary.changed && !d.summary.removed
  return <div id="syPanel">
    <div className="muted" style={{ marginBottom: 6 }}>当前 {sv.tool_count ?? 0} 个工具 · {syncText(sv)}</div>
    <div className="row" id="syMode"><Btn s={mode !== 'online'} p={mode === 'online'} onClick={() => { setMode('online'); setP(null) }}>在线同步</Btn>
      <Btn s={mode !== 'manual'} p={mode === 'manual'} onClick={() => { setMode('manual'); setP(null) }}>手动导入 JSON</Btn>
      <span className="muted">{mode === 'online' ? <>连接 <span className="mono">{serverEnd(sv)}</span>，执行 initialize + tools/list（使用已保存的凭据）{sv.transport === 'stdio' && !isAdmin ? '（stdio 仅管理员可在线同步）' : ''}</> : '粘贴 tools/list 的返回（JSON-RPC 响应、{"tools":[…]} 或数组均可）'}</span></div>
    {mode === 'manual' && <textarea id="syJson" className="mono" style={{ width: '100%', minHeight: 110, marginTop: 8 }} value={json} onChange={e => setJson(e.target.value)} placeholder='{"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"...","description":"...","inputSchema":{...}}]}}' />}
    <div className="row" style={{ margin: '8px 0' }}><Btn p id="syRun" onClick={run} disabled={busy}>{busy ? '读取中…' : '读取并对比'}</Btn>
      {d && <Btn p id="syApply" onClick={apply} disabled={!!nothing}>{nothing ? '没有变化' : '确认同步'}</Btn>}</div>
    <ErrBox lines={err} />
    {d && <div id="syDiff">
      <div className="muted" style={{ marginBottom: 6 }}>{p.source}{p.server_info?.server_info?.name ? ` · ${p.server_info.server_info.name} ${p.server_info.server_info.version || ''}` : ''} · 远端共 {p.tools.length} 个工具：
        新增 <b>{d.summary.added}</b> · 变更 <b>{d.summary.changed}</b> · 消失 <b>{d.summary.removed}</b> · 未变 {d.summary.unchanged}</div>
      <table><thead><tr><th style={{ width: 70 }}>变化</th><th>工具</th><th>说明</th><th>影响的意图</th></tr></thead><tbody>
        {d.added.map((x: any) => <tr key={'a' + x.name}><td><Badge c="g">新增</Badge></td><td className="mono">{x.tool}</td><td className="muted">{x.description || x.title}（{x.params} 个入参）</td><td className="muted">—</td></tr>)}
        {d.changed.map((x: any) => <tr key={'c' + x.name}><td><Badge c="y">变更</Badge></td><td className="mono">{x.tool}</td><td>{x.changes.join('；')}</td>
          <td>{x.used_by.length ? x.used_by.map((u: any) => <div key={u.code} className="mono">{u.code} <span className="muted">{u.status}</span></div>) : <span className="muted">未使用</span>}</td></tr>)}
        {d.removed.map((x: any) => <tr key={'r' + x.name} className={x.used_by.length ? 'rm-used' : ''}><td><Badge c="r">消失</Badge></td><td className="mono">{x.tool}</td>
          <td>{x.used_by.length ? <b style={{ color: '#b91c1c' }}>仍被意图使用：保留并标红，不会删除；这些意图需要更换工具，已启用的会因校验不通过转为草稿</b> : <span className="muted">未被使用，将从目录删除</span>}</td>
          <td>{x.used_by.length ? x.used_by.map((u: any) => <div key={u.code} className="mono" style={{ color: '#b91c1c' }}>{u.code} {u.name} <span className="muted">{u.status}</span></div>) : <span className="muted">—</span>}</td></tr>)}
        {nothing && <tr><td colSpan={4} className="muted" style={{ textAlign: 'center' }}>与已登记的工具一致，没有变化</td></tr>}
      </tbody></table></div>}
  </div>
}

export function SyncModal({ sv, onClose, onDone }: { sv: any; onClose: () => void; onDone: () => void }) {
  return <Modal title={<>同步工具 · {sv.display_name} <span className="mono">{sv.name}</span></>} testId="mcpSync" onClose={onClose} wide foot={<Btn onClick={onClose}>关闭</Btn>}>
    <SyncPanel sv={sv} onDone={onDone} />
  </Modal>
}

/** 意图编辑器里的下钻：选中服务后多选具体工具 */
export function McpPicker({ server, tools, bound, onClose, onOk }: { server: any; tools: any[]; bound: string[]; onClose: () => void; onOk: (names: string[]) => void }) {
  const [q, setQ] = useState('')
  const [sel, setSel] = useState<string[]>([])
  const avail = tools.filter(t => t.enabled && !t.remote_missing)
  const off = tools.length - avail.length
  const k = q.trim().toLowerCase()
  const shown = avail.filter(t => !k || [t.name, t.display_name, t.description].some((s: string) => (s || '').toLowerCase().includes(k)))
  return <Modal title={<>选择 MCP 工具 · {server.display_name} <span className="mono">{server.name}</span></>} testId="mcpPick" onClose={onClose}
    foot={<><span className="muted" style={{ float: 'left', lineHeight: '28px' }}>已选 {sel.length} 个</span><Btn onClick={onClose}>取消</Btn> <Btn p id="mpOk" disabled={!sel.length} onClick={() => onOk(sel)}>关联所选</Btn></>}>
    <div className="muted" style={{ marginBottom: 6 }}>一个 MCP 服务包含多项能力，意图只关联需要的具体工具（不能整体关联）。{off > 0 && `另有 ${off} 个已停用或远端已消失的工具不可选。`}</div>
    <input type="text" id="mpQ" value={q} onChange={e => setQ(e.target.value)} placeholder="搜索工具名或说明" style={{ width: '100%', marginBottom: 8 }} />
    <div id="mpList" style={{ maxHeight: 360, overflow: 'auto' }}>{shown.length ? shown.map(t => { const isB = bound.includes(t.name); return <label key={t.name} className="pickrow">
      <input type="checkbox" disabled={isB} checked={isB || sel.includes(t.name)} onChange={e => setSel(e.target.checked ? [...sel, t.name] : sel.filter(x => x !== t.name))} />
      <span><b>{t.display_name}</b> <span className="mono">{t.mcp_tool}</span>{t.confirm_required && <> <Badge c="y">写操作需确认</Badge></>}{isB && <span className="muted">（已关联）</span>}
        <div className="muted">{t.description}{t.params.length ? ` · 入参：${t.params.map((p: any) => p.name + (p.required ? '*' : '')).join(', ')}` : ''}</div></span></label> })
      : <div className="muted" style={{ textAlign: 'center', padding: 20 }}>{avail.length ? '没有匹配的工具' : '这个服务还没有可用工具，请先在工具中心同步并启用'}</div>}</div>
  </Modal>
}

export function syncText(sv: any) {
  return sv.last_sync_at ? `${sv.last_sync_source} · ${fmt(sv.last_sync_at, false)} · ${sv.last_sync_by}` : '尚未同步'
}
