import { useEffect, useRef, useState } from 'react'
import { api, errLines, qs } from '../api'
import { useSession } from '../session'
import { Badge, Btn, Drawer, ErrBox, GroupInput, Modal, toast } from '../ui'
import { McpForm, SyncPanel, emptyMcp, mcpBody, mcpFormOf } from './McpServers'

/** 注册工具的类型（MCP 实际登记的是服务，再同步子工具） */
export const KINDS: [string, string][] = [['http', 'API 接口'], ['mcp', 'MCP'], ['cli', 'CLI 命令'], ['skill', '技能'], ['workflow', '工作流']]
export const KIND_LABEL: Record<string, string> = { http: 'API 接口', mcp: 'MCP 工具', cli: 'CLI 命令', skill: '技能', workflow: '工作流' }
export const PARAM_TYPES = ['string', 'number', 'boolean', 'file', 'file_list', 'string[]']
export const pTypeLabel = (t: string) => ({ file: '文件', file_list: '文件列表' } as Record<string, string>)[t] || t
export const isFileParam = (p: any) => p?.type === 'file' || p?.type === 'file_list'
export const WF_PLAT: Record<string, string> = { dify: 'Dify', coze: 'Coze', generic: '通用 HTTP' }
const KIND_HINT: Record<string, string> = { mcp: '登记 MCP 服务并同步其子工具', skill: '上传技能文件夹或 .zip（须含 SKILL.md）', workflow: '对接 Dify / Coze / 通用 HTTP 工作流' }
const TYPE_C: Record<string, string> = { http: 'b', cli: 'n', skill: 'g', workflow: 'o', mcp: 'p' }
export const TypeTag = ({ type }: { type: string }) => <Badge c={TYPE_C[type] || 'n'}>{KIND_LABEL[type] || type}</Badge>

const CLI_META = ['pack', 'pack_name', 'group', 'source_ref', 'risk_level']

type P = { name: string; type: string; required: boolean; description: string; default?: string }

function blankTool(owner: string) {
  return {
    name: '', display_name: '', description: '', group: '', owner, url: '', method: 'GET', auth: '无', output_example: '{}', output_description: '',
    bin: '', command_template: '', cli_auth: '无', cli_auth_env: '', cli_meta: {} as Record<string, string>, login_command: '', install: '', verified_version: '', output_format: 'json', confirm_required: false, secret: '',
    wf: { platform: 'dify', response_mode: 'blocking', base_url: 'https://api.dify.ai/v1', auth_env: 'DIFY_APP_KEY', user_mode: 'constant', user_value: 'intent-bot',
      region: 'coze.cn', workflow_id: '', workflow_version: '', is_async: false, bot_id: '', app_id: '', ext: '', url: '', wmethod: 'POST', wauth: '无',
      timeout_sec: '', output_mapping: [] as { from: string; as: string }[], params_locked: true },
  }
}

function toolForm(t: any) {
  const b = blankTool(t.owner)
  const c = t.config || {}
  const wf = t.type === 'workflow' ? {
    ...b.wf, platform: c.platform, response_mode: c.response_mode || 'blocking', base_url: c.base_url || b.wf.base_url, auth_env: c.auth_env || '',
    user_mode: c.user?.mode || 'constant', user_value: c.user?.value || '', region: c.region || 'coze.cn', workflow_id: c.workflow_id || '', workflow_version: c.workflow_version || '',
    is_async: !!c.is_async, bot_id: c.bot_id || '', app_id: c.app_id || '', ext: c.ext ? JSON.stringify(c.ext) : '', url: c.url || '', wmethod: c.method || 'POST', wauth: c.auth || '无',
    timeout_sec: c.timeout_sec != null ? String(c.timeout_sec) : '', output_mapping: c.output_mapping || [], params_locked: c.params_locked !== false,
  } : b.wf
  return {
    ...b, name: t.name, display_name: t.display_name, description: t.description, group: t.group || t.cli?.group || '', url: t.url || '', method: t.method || 'GET', auth: t.auth || '无',
    output_example: t.output_example || (t.type === 'http' ? '{}' : ''), output_description: t.output_description || t.cli?.output_description || c.output_description || '',
    bin: t.cli?.bin || '', command_template: t.cli?.command_template || '', cli_auth: t.cli?.auth || '无', login_command: t.cli?.login_command || '',
    cli_auth_env: t.cli?.auth_env || '', cli_meta: Object.fromEntries(CLI_META.filter(k => t.cli?.[k]).map(k => [k, t.cli[k]])),
    install: t.cli?.install || '', verified_version: t.cli?.verified_version || '', output_format: t.cli?.output_format || 'json',
    confirm_required: t.type === 'workflow' ? !!c.confirm_required : !!t.cli?.confirm_required, wf,
  }
}

/** 统一的「注册工具 / 编辑工具」抽屉。tool = 要编辑的工具；server = 要编辑的 MCP 服务；都没有时为新建 */
export default function ToolEdit({ tool, server, onClose, onDone }: { tool?: any; server?: any; onClose: () => void; onDone: (server?: string) => void }) {
  const { user } = useSession()
  const [kind, setKind] = useState<string>(server ? 'mcp' : tool ? tool.type : (sessionStorage.getItem('toolKind') || 'http'))
  const [f, setF] = useState<any>(() => tool ? toolForm(tool) : blankTool(user.display_name))
  const [params, setParams] = useState<P[]>(() => tool ? tool.params.map((p: any) => ({ ...p, description: p.description || '', default: p.default || '' }))
    : [{ name: '', type: 'string', required: true, description: '' }])
  const [srv, setSrv] = useState<any>(server || null)
  const [mf, setMf] = useState<any>(() => server ? mcpFormOf(server) : emptyMcp(user.display_name))
  const [pkg, setPkg] = useState<any>(null)        // 本次上传、尚未保存的技能包
  const [detail, setDetail] = useState<any>(null)  // 已有技能的 SKILL.md 与版本
  const [syncOpen, setSyncOpen] = useState(false)
  const [err, setErr] = useState<string[]>()
  const [busy, setBusy] = useState(false)
  const [groups, setGroups] = useState<string[]>([])
  const editing = !!tool || !!srv
  useEffect(() => {
    api.get('/tools' + qs({ page: 1, page_size: 1 })).then((d: any) => {
      const gs = [...new Set((d.facets?.groups || []).map((x: any) => x.group).filter(Boolean))] as string[]
      setGroups(gs)
    }).catch(() => {})
  }, [])
  useEffect(() => { if (tool?.type === 'skill') api.get('/tools/' + tool.name).then(setDetail).catch(() => {}) }, [tool])
  useEffect(() => { if (!editing) sessionStorage.setItem('toolKind', kind) }, [kind, editing])
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value })
  const setW = (k: string, v: any) => setF((x: any) => ({ ...x, wf: { ...x.wf, [k]: v } }))
  const wf = f.wf

  const reloadSrv = async (name: string) => { const s = await api.get('/mcp-servers/' + name); setSrv(s); setMf(mcpFormOf(s)); return s }
  const saveMcp = async () => {
    try {
      const r = srv ? await api.put('/mcp-servers/' + srv.name, mcpBody(mf)) : await api.post('/mcp-servers', mcpBody(mf))
      const w = r.warnings?.length ? '（' + r.warnings.join('；') + '）' : ''
      await reloadSrv(r.name)
      toast((srv ? 'MCP 服务已更新' : 'MCP 服务已登记，请在下方同步工具') + w)
      if (r.warnings?.length) setErr(r.warnings); else setErr(undefined)
      onDone(r.name)
    } catch (e) { setErr(errLines(e)) }
  }
  const wfConfig = () => {
    const c: any = { platform: wf.platform, response_mode: wf.response_mode, auth_env: wf.auth_env, timeout_sec: wf.timeout_sec, confirm_required: f.confirm_required,
      output_mapping: wf.output_mapping.filter((m: any) => m.from || m.as), params_locked: wf.params_locked, output_description: f.output_description }
    if (wf.platform === 'dify') Object.assign(c, { base_url: wf.base_url, user: { mode: wf.user_mode, value: wf.user_value } })
    if (wf.platform === 'coze') Object.assign(c, { region: wf.region, workflow_id: wf.workflow_id, workflow_version: wf.workflow_version, is_async: wf.is_async, bot_id: wf.bot_id, app_id: wf.app_id, ext: wf.ext })
    if (wf.platform === 'generic') Object.assign(c, { url: wf.url, method: wf.wmethod, auth: wf.wauth })
    return c
  }
  const save = async () => {
    if (kind === 'mcp') return saveMcp()
    let config: any
    if (kind === 'http') config = { url: f.url, method: f.method, auth: f.auth, output_example: f.output_example }
    else if (kind === 'cli') {
      config = { ...f.cli_meta, bin: f.bin, command_template: f.command_template, auth: f.cli_auth, auth_env: f.cli_auth_env, login_command: f.login_command, install: f.install, verified_version: f.verified_version, output_format: f.output_format, confirm_required: f.confirm_required, output_description: f.output_description }
      const g = (f.group || '').trim()
      if (g) config.group = g; else delete config.group
    }
    else if (kind === 'skill') {
      if (!tool && !pkg) { setErr(['请先上传包含 SKILL.md 的技能文件夹或 .zip']); return }
      config = pkg ? { package_id: pkg.package_id } : {}
    } else config = wfConfig()
    const ps = kind === 'skill' ? [] : params.map(p => ({ ...p, name: p.name.trim() })).filter(p => p.name)
    const body: any = { type: kind, name: f.name.trim(), display_name: f.display_name, description: kind === 'skill' ? (pkg?.description || f.description) : f.description, owner: f.owner, params: ps, config, group: (f.group || '').trim() }
    if (f.secret) body.secret = f.secret
    try {
      const r = tool ? await api.put('/tools/' + tool.name, body) : await api.post('/tools', body)
      toast(tool ? '工具已更新' + (kind === 'skill' && pkg ? `，技能包 v${r.skill?.version}` : '') + (r.warnings?.length ? '（' + r.warnings.join('；') + '）' : '') : '工具已注册，运营可在意图中选用'); onDone(); onClose()
    } catch (e) { setErr(errLines(e)) }
  }

  // —— 技能包上传（文件夹或 .zip，服务端解析 SKILL.md） ——
  const upload = async (files: FileList | null) => {
    if (!files || !files.length) return
    const fd = new FormData()
    Array.from(files).forEach(fl => { fd.append('files', fl, fl.name); fd.append('paths', (fl as any).webkitRelativePath || fl.name) })
    setBusy(true); setErr(undefined)
    try {
      const r = await api.post('/skill-packages', fd)
      setPkg(r)
      setF((x: any) => ({ ...x, description: r.description, name: tool ? x.name : (x.name || r.suggested_tool_name || ''), display_name: x.display_name || r.name }))
      toast(`已解析 SKILL.md：${r.name}，${r.files.length} 个文件`)
    } catch (e) { setErr(errLines(e)); setPkg(null) }
    setBusy(false)
  }

  const sp = (i: number, k: keyof P, v: any) => setParams(params.map((p, j) => j === i ? { ...p, [k]: v } : p))
  const paramTable = (withDefault: boolean, locked: boolean) => <table><thead><tr><th>参数名</th><th>类型</th><th>必填</th>{withDefault && <th>默认值</th>}<th>说明</th>{!locked && <th></th>}</tr></thead><tbody>
    {params.map((p, i) => locked ? <tr key={i}><td className="mono">{p.name}</td><td className="muted">{pTypeLabel(p.type)}</td><td>{p.required ? <Badge c="r">必填</Badge> : <span className="muted">选填</span>}</td>
      {withDefault && <td className="mono muted">{p.default || '—'}</td>}<td className="muted">{p.description}</td></tr>
      : <tr key={i}><td><input type="text" id={'pN' + i} className="mono" value={p.name} onChange={e => sp(i, 'name', e.target.value)} style={{ width: 120 }} /></td>
        <td><select id={'pT' + i} value={p.type} onChange={e => sp(i, 'type', e.target.value)}>{PARAM_TYPES.map(x => <option key={x} value={x}>{pTypeLabel(x)}</option>)}</select></td>
        <td><input type="checkbox" id={'pR' + i} checked={p.required} onChange={e => sp(i, 'required', e.target.checked)} /></td>
        {withDefault && <td><input type="text" id={'pDef' + i} className="mono" value={p.default || ''} onChange={e => sp(i, 'default', e.target.value)} style={{ width: 90 }} /></td>}
        <td><input type="text" id={'pD' + i} value={p.description} onChange={e => sp(i, 'description', e.target.value)} style={{ width: '100%' }} /></td>
        <td><a onClick={() => setParams(params.filter((_, j) => j !== i))}>删除</a></td></tr>)}
    {!params.length && <tr><td colSpan={6} className="muted">{kind === 'workflow' && wf.platform !== 'generic' ? '尚未同步入参' : '尚未添加入参'}</td></tr>}</tbody></table>
  const addParam = <Btn s onClick={() => setParams([...params, { name: '', type: 'string', required: false, description: '', default: '' }])}>＋ 添加入参</Btn>

  const title = kind === 'mcp' ? (srv ? `编辑 MCP 服务 · ${srv.name}` : '注册工具 · MCP') : tool ? `编辑工具 · ${tool.name}` : '注册工具'
  const curSkillVer = detail?.skill_versions?.[0]?.version || tool?.skill?.version
  const descRo = kind === 'skill'
  return <Drawer testId="toolEdit" title={title} onClose={onClose} foot={<><Btn onClick={onClose}>取消</Btn> <Btn p id="toolSave" onClick={save} disabled={busy}>{kind === 'mcp' && !srv ? '保存服务' : '保存'}</Btn></>}>
    <ErrBox lines={err} />
    <div className="grid g2"><div className="f"><label>工具类型 *</label><select id="tTy" value={kind} disabled={editing} onChange={e => { setKind(e.target.value); setErr(undefined); setParams(e.target.value === 'workflow' || e.target.value === 'skill' ? [] : [{ name: '', type: 'string', required: true, description: '' }]) }}>
      {KINDS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></div>
      <div className="muted" style={{ alignSelf: 'end', paddingBottom: 12 }}>{KIND_HINT[kind] || ''}</div></div>

    {kind === 'mcp' ? <>
      <McpForm f={mf} setF={setMf} editing={!!srv} exampleData={srv?.example_data} groups={groups} />
      {srv ? <div className="sec" id="mSync"><b>同步工具</b>{srv.example_data && <div className="muted" style={{ margin: '0 0 6px' }}>以下为示例数据，真实同步可拉取完整工具列表。</div>}
        <SyncPanel sv={srv} onDone={() => { reloadSrv(srv.name); onDone(srv.name) }} /></div>
        : <div className="muted">先保存服务信息，再在下方同步 tools/list。</div>}
    </> : <>
      <div className="grid g2"><div className="f"><label>工具名（英文，唯一）*</label><input type="text" id="tN" value={f.name} disabled={!!tool} onChange={set('name')} placeholder="my_tool" /></div>
        <div className="f"><label>显示名 *</label><input type="text" id="tD" value={f.display_name} onChange={set('display_name')} placeholder="显示名" /></div></div>
      <div className="f"><label>描述 *{descRo ? '（来自 SKILL.md，只读）' : '（运营据此判断该挂到哪个意图）'}</label><input type="text" id="tDs" value={f.description} readOnly={descRo} onChange={set('description')} placeholder={descRo ? '上传技能包后自动填入' : ''} /></div>
      <div className="f"><label>业务分组</label><GroupInput value={f.group || ''} onChange={g => setF({ ...f, group: g })} options={groups} />
        <div className="muted">可选。用于在工具目录里分类筛选；可选用已有分组，也可以输入新名称。</div></div>

      {kind === 'http' && <div className="grid g2"><div className="f"><label>接口地址 *</label><input type="text" id="tU" value={f.url} onChange={set('url')} placeholder="https://..." /></div>
        <div className="f"><label>方法</label><select id="tM" value={f.method} onChange={set('method')}>{['GET', 'POST', 'PUT', 'DELETE'].map(m => <option key={m}>{m}</option>)}</select></div></div>}

      {kind === 'cli' && <>
        <div className="grid g2"><div className="f"><label>可执行程序 *</label><input type="text" id="tBin" value={f.bin} onChange={set('bin')} placeholder="mycli" /></div>
          <div className="f"><label>已验证版本</label><input type="text" id="tCv" value={f.verified_version} onChange={set('verified_version')} placeholder="1.0.0" /></div></div>
        <div className="f"><label>命令 / 子命令模板 *</label><input type="text" id="tCmd" className="mono" value={f.command_template} onChange={set('command_template')} placeholder="mycli product get --sku {sku} --json" />
          <div className="muted">用 {'{参数名}'} 引用下方入参；选填参数为空时，连同它前面的 --选项 一起省略</div>
          {f.cli_meta.source_ref && <div className="muted" id="tCsrc">来源：{f.cli_meta.pack_name || f.cli_meta.pack}{f.cli_meta.group && ` › ${f.cli_meta.group}`} · <span className="mono">{f.cli_meta.source_ref}</span>（按官方命令目录接入）</div>}</div>
        <div className="grid g2"><div className="f"><label>鉴权方式</label><select id="tCa" value={f.cli_auth} onChange={set('cli_auth')}>{['无', 'CLI 内置登录（OAuth）', '环境变量 Token'].map(x => <option key={x}>{x}</option>)}</select></div>
          <div className="f"><label>登录 / 鉴权命令</label><input type="text" id="tLg" className="mono" value={f.login_command} onChange={set('login_command')} placeholder="mycli auth login" /></div></div>
        <div className="f"><label>凭据环境变量（可选）</label><input type="text" id="tCe" className="mono" value={f.cli_auth_env} onChange={set('cli_auth_env')} placeholder="MYCLI_TOKEN" />
          <div className="muted">Bot 运行环境可用这个环境变量注入 Token，代替交互式登录；只登记变量名，不保存值</div></div>
        <div className="grid g2"><div className="f"><label>安装方式（所需环境）</label><input type="text" id="tIn" className="mono" value={f.install} onChange={set('install')} placeholder="curl -fsSL https://.../install | bash" /></div>
          <div className="f"><label>输出格式</label><select id="tOf" value={f.output_format} onChange={set('output_format')}><option value="json">JSON</option><option value="text">纯文本</option></select></div></div>
        <div className="f"><label><input type="checkbox" id="tCf" checked={f.confirm_required} onChange={set('confirm_required')} /> 写操作，Bot 执行前需用户确认</label></div></>}

      {kind === 'skill' && <div className="sec" id="skSec"><b>技能包 *</b>
        <div className="muted" style={{ margin: '4px 0 8px' }}>上传包含 SKILL.md 的文件夹或 .zip，平台解析前置元数据中的 name、description。技能没有入参：意图抽取的槽位会作为上下文交给 AI。{tool ? '重新上传即作为新版本。' : ''}</div>
        <div className="grid g2"><div className="f"><label>上传技能文件夹</label><input type="file" id="skDir" multiple {...{ webkitdirectory: '', directory: '' } as any} onChange={e => { upload(e.target.files); e.target.value = '' }} /></div>
          <div className="f"><label>或上传 .zip</label><input type="file" id="skZip" accept=".zip,application/zip" onChange={e => { upload(e.target.files); e.target.value = '' }} /></div></div>
        {busy && <div className="muted">解析中…</div>}
        {pkg ? <SkillFiles head={<><Badge c="g">待保存</Badge> {pkg.name} · {pkg.source === 'zip' ? (pkg.original_name || '.zip') : '文件夹'} · {pkg.files.length} 个文件{tool ? ` · 保存后为 v${(curSkillVer || 0) + 1}` : ' · 保存后为 v1'}</>} files={pkg.files} md={pkg.skill_md} />
          : tool && tool.skill && <SkillFiles head={<>当前版本 v{tool.skill.version} · {tool.skill.files.length} 个文件</>} files={tool.skill.files} md={detail?.skill_md} />}
        {tool && !tool.skill?.files?.length && !pkg && <div className="note w">该技能尚未上传技能包（由旧版「技能/工作流」迁移而来），请上传 SKILL.md 技能包。</div>}
        {detail?.skill_versions?.length > 0 && <div className="muted" style={{ marginTop: 6 }}>历史版本：{detail.skill_versions.map((v: any) => <a key={v.version} href={`/api/admin/tools/${tool.name}/skill-package?version=${v.version}`} style={{ marginRight: 10 }}>v{v.version} 下载</a>)}</div>}
      </div>}

      {kind === 'workflow' && <WorkflowForm f={f} setF={setF} wf={wf} setW={setW} params={params} setParams={setParams} paramTable={paramTable} addParam={addParam} onSync={() => setSyncOpen(true)} config={wfConfig} />}

      {(kind === 'http' || kind === 'cli') && <div className="sec"><b>入参</b>{paramTable(false, false)}{addParam}</div>}
      {kind !== 'skill' && <div className="f"><label>{kind === 'http' ? '出参示例（JSON）' : '输出说明 / 示例（可选）'}</label><textarea id="tO" className="mono" value={kind === 'http' ? f.output_example : f.output_description} onChange={set(kind === 'http' ? 'output_example' : 'output_description')} /></div>}
      <div className="grid g2">{kind === 'http' ? <div className="f"><label>鉴权方式</label><select id="tA" value={f.auth} onChange={set('auth')}>{['无', 'API Key', 'Bearer Token', 'OAuth2'].map(x => <option key={x}>{x}</option>)}</select></div> : <div />}
        <div className="f"><label>负责人 *</label><input type="text" id="tW" value={f.owner} onChange={set('owner')} /></div></div>
      {kind === 'http' && f.auth !== '无' && <div className="f"><label>密钥（加密托管，只写不读{tool?.has_secret ? '；已设置，留空保持不变' : ''}）</label><input type="password" id="tSec" value={f.secret} onChange={set('secret')} autoComplete="new-password" /></div>}
    </>}
    {syncOpen && <WfSyncModal config={wfConfig()} onClose={() => setSyncOpen(false)} onOk={(ps, om) => {
      setParams(ps.map((p: any) => ({ ...p, description: p.description || '', default: p.default || '' })))
      setF((x: any) => ({ ...x, wf: { ...x.wf, params_locked: true, output_mapping: x.wf.output_mapping.length ? x.wf.output_mapping : om } }))
      setSyncOpen(false); toast(`已同步 ${ps.length} 个入参`)
    }} />}
  </Drawer>
}

function SkillFiles({ head, files, md }: { head: any; files: any[]; md?: string }) {
  return <div id="skFiles"><div className="muted" style={{ margin: '6px 0' }}>{head}</div>
    <ul className="mono" style={{ margin: '4px 0 8px', paddingLeft: 18, fontSize: 12 }}>{files.map((x: any) => <li key={x.path}>{x.path} <span className="muted">({x.size} B)</span></li>)}</ul>
    {md && <div className="f"><label>SKILL.md</label><pre className="code" style={{ maxHeight: 180, overflow: 'auto' }}>{md}</pre></div>}</div>
}

function WorkflowForm({ f, setF, wf, setW, params, setParams, paramTable, addParam, onSync }: any) {
  const plat = wf.platform
  const locked = plat !== 'generic' && wf.params_locked
  const om: { from: string; as: string }[] = wf.output_mapping
  const switchPlat = (p: string) => {
    setF((x: any) => {
      const w = { ...x.wf, platform: p, params_locked: p !== 'generic' }
      if (p === 'dify' && (!w.auth_env || w.auth_env === 'COZE_PAT')) w.auth_env = 'DIFY_APP_KEY'
      if (p === 'coze' && (!w.auth_env || w.auth_env === 'DIFY_APP_KEY')) w.auth_env = 'COZE_PAT'
      if (p === 'generic' && ['DIFY_APP_KEY', 'COZE_PAT'].includes(w.auth_env)) w.auth_env = ''
      return { ...x, wf: w }
    })
    if (p !== 'generic') setParams([])
  }
  return <>
    <div className="muted" style={{ margin: '0 0 10px' }}>对接工作流平台的原生 HTTP API，密钥只填环境变量名。若工作流已发布为 MCP 服务，也可改用类型「MCP」登记。</div>
    <div className="grid g2"><div className="f"><label>平台 *</label><select id="wPlat" value={plat} onChange={e => switchPlat(e.target.value)}>{Object.entries(WF_PLAT).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></div>
      <div className="f"><label>响应模式</label><select id="wMode" value={wf.response_mode} onChange={e => setW('response_mode', e.target.value)}><option value="blocking">同步</option><option value="streaming">流式</option></select></div></div>
    {plat === 'dify' && <>
      <div className="grid g2"><div className="f"><label>API 基址 *</label><input type="text" id="wBase" className="mono" value={wf.base_url} onChange={e => setW('base_url', e.target.value)} placeholder="https://api.dify.ai/v1" />
        <div className="muted">请求 POST {'{基址}'}/workflows/run；同步入参走 GET {'{基址}'}/parameters</div></div>
        <div className="f"><label>应用 API Key（仅填环境变量名）*</label><input type="text" id="wEnv" className="mono" value={wf.auth_env} onChange={e => setW('auth_env', e.target.value.toUpperCase())} placeholder="DIFY_APP_KEY" /></div></div>
      <div className="grid g2"><div className="f"><label>user 字段</label><select id="wUserMode" value={wf.user_mode} onChange={e => setW('user_mode', e.target.value)}><option value="constant">常量</option><option value="session">映射会话用户</option></select></div>
        {wf.user_mode === 'constant' ? <div className="f"><label>user 常量值</label><input type="text" id="wUserConst" className="mono" value={wf.user_value} onChange={e => setW('user_value', e.target.value)} placeholder="intent-bot" /></div>
          : <div className="muted" style={{ alignSelf: 'end', paddingBottom: 12 }}>Bot 调用时填当前会话用户</div>}</div></>}
    {plat === 'coze' && <>
      <div className="grid g2"><div className="f"><label>区域 *</label><select id="wReg" value={wf.region} onChange={e => setW('region', e.target.value)}><option value="coze.cn">api.coze.cn（国内）</option><option value="coze.com">api.coze.com（国际）</option></select></div>
        <div className="f"><label>工作流 ID *</label><input type="text" id="wId" className="mono" value={wf.workflow_id} onChange={e => setW('workflow_id', e.target.value.trim())} placeholder="7xxxxxxxxxxxx" /></div></div>
      <div className="grid g2"><div className="f"><label>访问令牌（仅填环境变量名）*</label><input type="text" id="wEnv" className="mono" value={wf.auth_env} onChange={e => setW('auth_env', e.target.value.toUpperCase())} placeholder="COZE_PAT" /></div>
        <div className="f"><label>workflow_version（可选）</label><input type="text" id="wVer" className="mono" value={wf.workflow_version} onChange={e => setW('workflow_version', e.target.value)} placeholder="不填则用最新" /></div></div>
      <div className="muted" style={{ marginBottom: 8 }}>同步模式走 POST /v1/workflow/run，流式走 /v1/workflow/stream_run。</div></>}
    {plat === 'generic' && <>
      <div className="grid g2"><div className="f"><label>请求地址 *</label><input type="text" id="wUrl" className="mono" value={wf.url} onChange={e => setW('url', e.target.value)} placeholder="https://..." /></div>
        <div className="f"><label>方法</label><select id="wMeth" value={wf.wmethod} onChange={e => setW('wmethod', e.target.value)}>{['POST', 'GET', 'PUT'].map(m => <option key={m}>{m}</option>)}</select></div></div>
      <div className="grid g2"><div className="f"><label>鉴权</label><select id="wAuth" value={wf.wauth} onChange={e => setW('wauth', e.target.value)}>{['无', 'API Key', 'Bearer Token'].map(x => <option key={x}>{x}</option>)}</select></div>
        <div className="f"><label>凭据环境变量名</label><input type="text" id="wEnv" className="mono" value={wf.auth_env} onChange={e => setW('auth_env', e.target.value.toUpperCase())} placeholder="WF_TOKEN" /></div></div></>}
    <div className="grid g2"><div className="f"><label>超时（秒，可选）</label><input type="text" id="wTo" className="mono" value={wf.timeout_sec} onChange={e => setW('timeout_sec', e.target.value)} placeholder="不填则用 Bot 默认" /></div>
      <div className="f" style={{ paddingTop: 22 }}><label><input type="checkbox" id="wCf" checked={f.confirm_required} onChange={e => setF({ ...f, confirm_required: e.target.checked })} /> 写操作，Bot 执行前需用户确认</label></div></div>
    <div className="sec" id="wParams"><div className="row"><b>入参</b><span className="sp" />
      {plat !== 'generic' ? <><Btn s id="wfSync" onClick={onSync}>{params.length ? '刷新' : '同步入参'}</Btn>{params.length > 0 && <Btn s id="wfTune" onClick={() => setW('params_locked', !wf.params_locked)}>{locked ? '微调' : '锁定'}</Btn>}
        <span className="muted">{locked ? '由同步填充，默认只读' : '正在微调入参定义（一般以平台同步结果为准）'}</span></> : <span className="muted">通用 HTTP 需手工维护入参</span>}</div>
      {paramTable(true, locked)}{!locked && addParam}</div>
    <div className="sec" id="wOut"><b>输出字段映射（可选）</b><div className="muted" style={{ margin: '4px 0 6px' }}>不填则由 Bot 自行解析工作流返回。</div>
      <table><thead><tr><th>工作流输出字段</th><th>映射为</th><th></th></tr></thead><tbody>
        {om.length ? om.map((m, i) => <tr key={i}><td><input type="text" id={'omF' + i} className="mono" value={m.from} onChange={e => setW('output_mapping', om.map((x, j) => j === i ? { ...x, from: e.target.value } : x))} placeholder="data.outputs.result" /></td>
          <td><input type="text" id={'omA' + i} className="mono" value={m.as} onChange={e => setW('output_mapping', om.map((x, j) => j === i ? { ...x, as: e.target.value } : x))} placeholder="result" /></td>
          <td><a onClick={() => setW('output_mapping', om.filter((_, j) => j !== i))}>删除</a></td></tr>) : <tr><td colSpan={3} className="muted">尚未配置</td></tr>}</tbody></table>
      <Btn s onClick={() => setW('output_mapping', [...om, { from: '', as: '' }])}>＋ 添加映射</Btn></div>
    {plat === 'coze' && <details className="adv" id="wAdv"><summary>高级</summary>
      <div className="f"><label><input type="checkbox" id="wAsync" checked={wf.is_async} onChange={e => setW('is_async', e.target.checked)} /> 异步运行（长任务，需 Bot 轮询结果）</label></div>
      <div className="grid g2"><div className="f"><label>bot_id（可选）</label><input type="text" id="wBot" className="mono" value={wf.bot_id} onChange={e => setW('bot_id', e.target.value)} /></div>
        <div className="f"><label>app_id（可选）</label><input type="text" id="wApp" className="mono" value={wf.app_id} onChange={e => setW('app_id', e.target.value)} /></div></div>
      <div className="f"><label>ext（可选，JSON 对象）</label><textarea id="wExt" className="mono" value={wf.ext} onChange={e => setW('ext', e.target.value)} placeholder='{"key":"value"}' /></div></details>}
  </>
}

/** 工作流同步入参：在线（临时提供密钥，不保存）或粘贴平台返回的 JSON */
function WfSyncModal({ config, onClose, onOk }: { config: any; onClose: () => void; onOk: (params: any[], om: any[]) => void }) {
  const [mode, setMode] = useState<'online' | 'paste'>('online')
  const [secret, setSecret] = useState('')
  const [raw, setRaw] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string[]>()
  const ref = useRef<HTMLInputElement>(null)
  const ep = config.platform === 'dify' ? `GET ${config.base_url || '{基址}'}/parameters` : `GET https://api.${config.region}/v1/workflows/${config.workflow_id || '{id}'}?include_input_output=true`
  const run = async () => {
    setBusy(true); setErr(undefined)
    try { const r = await api.post('/workflows/sync-inputs', mode === 'online' ? { config, secret } : { config, raw_json: raw }); onOk(r.params, r.output_mapping || []) } catch (e) { setErr(errLines(e)) }
    setBusy(false)
  }
  return <Modal title={`同步入参 · ${WF_PLAT[config.platform]}`} testId="wfSync" onClose={onClose} foot={<><Btn onClick={onClose}>取消</Btn> <Btn p id="wsRun" onClick={run} disabled={busy}>{busy ? '读取中…' : '读取入参'}</Btn></>}>
    <div className="row" style={{ marginBottom: 8 }}><Btn s={mode !== 'online'} p={mode === 'online'} onClick={() => setMode('online')}>在线读取</Btn> <Btn s={mode !== 'paste'} p={mode === 'paste'} onClick={() => setMode('paste')}>粘贴 JSON</Btn></div>
    <div className="muted mono" style={{ marginBottom: 8 }}>{ep}</div>
    {mode === 'online' ? <div className="f"><label>{config.platform === 'dify' ? '应用 API Key' : '访问令牌'}（仅本次使用，不保存）</label><input ref={ref} type="password" id="wsSecret" value={secret} onChange={e => setSecret(e.target.value)} autoComplete="new-password" />
      <div className="muted">平台不保存该密钥，也不会读取服务器上的环境变量。</div></div>
      : <div className="f"><label>粘贴上述接口的返回</label><textarea id="wsRaw" className="mono" style={{ minHeight: 160 }} value={raw} onChange={e => setRaw(e.target.value)} placeholder={config.platform === 'dify' ? '{"user_input_form":[{"text-input":{"variable":"query","label":"问题","required":true}}]}' : '{"code":0,"data":{"input":{"parameters":{...}},"output":{...}}}'} /></div>}
    <ErrBox lines={err} />
  </Modal>
}
