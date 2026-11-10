import { useEffect, useState } from 'react'
import { api, download, errLines, fmt } from '../api'
import demo from '../demoContent.json'
import { useSession } from '../session'
import { Badge, Btn, Drawer, ErrBox, Modal, toast, useAsync } from '../ui'
import { ServerEdit, SyncModal, serverEnd, syncText } from './McpServers'

const TYPES: [string, string][] = [['http', 'API 接口'], ['mcp', 'MCP 工具'], ['cli', 'CLI 命令'], ['skill', '技能/工作流']]
const API_ROWS = (demo as any).API_ROWS as string[][]
const MCP_ROWS = (demo as any).MCP_ROWS as string[][]
const MOCKV: Record<string, string> = { sku: 'SKU-88231', need_qty: '500', qty: '500', order_id: '202611090012345', new_price: '4800', reason: '破损', session_id: 'ww-session-7781', product_image: 'https://img.example.com/demo/sku-88231.jpg', platform: 'TikTok', site: 'US', style: '简约', title: 'Women Summer Dress No.1 Best Seller', text: 'No.1 Best ever summer dress, 100% effective', language: 'en', locale: 'en', query: 'TikTok 主图要求', kb_id: 'kb_spec', mode: 'merge', since_version: '6', task: 'main', prompt: '白底主图，产品居中，柔和阴影', ratio: '1:1', image_url: 'https://img.example.com/demo/sku-88231.jpg', color: '白色', target_language: 'en' }

export default function ToolsPage({ tab }: { tab: string }) {
  const { go } = useSession()
  return <>
    <h2>工具中心</h2>
    <p className="desc">工具全平台通用，不区分业务空间。技术把其他平台的能力注册为外部工具（API 接口 / MCP 工具 / CLI 命令 / 技能·工作流）；平台自身的接口也作为「平台内置」工具登记，可在意图里自循环使用，也开放给其他 AI 调用。平台只登记工具，实际执行在各 Bot / 智能体。</p>
    <div className="tabs" id="toolTabs" style={{ margin: '0 0 14px' }}>
      <div className={tab !== 'access' ? 'on' : ''} onClick={() => go('#/tools/list')}>工具目录</div>
      <div className={tab === 'access' ? 'on' : ''} onClick={() => go('#/tools/access')}>接入与密钥</div></div>
    {tab === 'access' ? <Access /> : <ToolList />}
  </>
}

function End({ t }: { t: any }) {
  const s = { fontSize: 11 }
  if (t.source === 'builtin') return <><div className="mono" style={s}>{t.endpoints.map((e: string) => <div key={e}>{e}</div>)}</div>
    {t.mcp_tools.length > 0 && <div className="muted" style={s}>MCP：{t.mcp_tools.join('、')}</div>}
    <div className="muted" style={s}>{t.space_scoped ? '按空间：需 X-Space-Id' : '全局：不需要 X-Space-Id'}</div></>
  if (t.type === 'cli') return <><div className="mono" style={s}>{t.cli.command_template}</div><div className="muted" style={s}>鉴权：{t.cli.auth} · 输出 {t.cli.output_format === 'json' ? 'JSON' : '纯文本'}</div></>
  if (t.type === 'skill') return <><div className="mono" style={s}>{t.skill_id}</div><div className="muted" style={s}>{t.platform}</div></>
  if (t.type === 'mcp') return <div className="mono" style={s}>tools/call {t.mcp_tool}</div>
  return <><Badge>{t.method}</Badge><div className="mono" style={s}>{t.url}</div></>
}

function ToolList() {
  const { canEdit } = useSession()
  const [data, reload] = useAsync(() => Promise.all([api.get('/tools'), api.get('/mcp-servers')]), [])
  const [sf, setSf] = useState('')
  const [tf, setTf] = useState('')
  const [edit, setEdit] = useState<any>(null)
  const [test, setTest] = useState<any>(null)
  const [srvEdit, setSrvEdit] = useState<any>(null)
  const [sync, setSync] = useState<any>(null)
  const [open, setOpen] = useState<Record<string, boolean>>({})
  if (!data) return <div className="loading">加载中…</div>
  const [all, servers] = data as [any[], any[]]
  const nb = all.filter((t: any) => t.source === 'builtin').length
  const ts = all.filter((t: any) => t.type !== 'mcp' && (!tf || t.type === tf) && (!sf || t.source === sf))
  const ss = servers.filter((v: any) => (!tf || tf === 'mcp') && (!sf || v.source === sf))
  const subs = (v: any) => all.filter((t: any) => t.server === v.name)
  const fb = (id: string, lab: string, list: [string, string, number][], cur: string, set: (x: string) => void) =>
    <div className="row" id={id} style={{ marginTop: 8 }}><span className="muted">{lab}</span>{list.map(x => <Btn key={x[0] || 'all'} s={cur !== x[0]} p={cur === x[0]} onClick={() => set(x[0])}>{x[1]} {x[2]}</Btn>)}</div>
  const del = async (t: any) => {
    if (!window.confirm(t.used_by?.length ? `工具 ${t.name} 被 ${t.used_by.length} 个意图使用（${t.used_by.map((u: any) => u.code).join('、')}），删除后这些意图的关联会被移除，已启用意图若因此校验不通过会转为草稿。确认删除？` : `确认删除工具 ${t.name}？`)) return
    try { const r = await api.del('/tools/' + t.name); reload(); toast('已删除' + (r.downgraded?.length ? `；${r.downgraded.length} 个启用意图转为草稿` : '')) } catch (e) { toast(errLines(e)[0]) }
  }
  const delSrv = async (v: any) => {
    if (!window.confirm(`确认删除 MCP 服务 ${v.name} 及其 ${v.tool_count} 个工具？`)) return
    try { await api.del('/mcp-servers/' + v.name); reload(); toast('已删除') } catch (e) { toast(errLines(e)[0]) }
  }
  const patch = async (t: any, body: any) => {
    try { const r = await api.patch('/tools/' + t.name, body); reload(); toast('已更新 ' + t.name + (r.downgraded?.length ? '' : '')) } catch (e) { toast(errLines(e)[0]) }
  }
  const usedBy = (list: any[]) => list?.length ? list.map((u: any) => <div key={u.space + u.code}><span className="mono" style={{ color: '#2563eb' }}>{u.code}</span><div><span className="tg">{u.space_name}</span></div></div>) : <span className="muted">未使用</span>
  const srcCell = (t: any) => t.source === 'builtin' ? <><Badge c="g">平台内置</Badge><div style={{ marginTop: 4 }}>{t.maintenance ? <Badge c="y">维护类</Badge> : <Badge>可在意图中使用</Badge>}</div></> : <span className="muted">外部</span>
  const nMcp = all.filter((t: any) => t.type === 'mcp').length
  return <>
    <div className="card"><div className="row"><span className="muted">全平台共 {all.length} 个工具（外部 {all.length - nb} · 平台内置 {nb}；其中 {nMcp} 个来自 {servers.length} 个 MCP 服务），所有业务空间通用</span><span className="sp" />
      {canEdit && <><Btn id="mcpNew" onClick={() => setSrvEdit({})}>＋ 注册 MCP 服务</Btn> <Btn p id="toolNew" onClick={() => setEdit({})}>＋ 注册工具</Btn></>}</div>
      {fb('toolSF', '按来源：', [['', '全部', all.length], ['external', '外部', all.length - nb], ['builtin', '平台内置', nb]], sf, setSf)}
      {fb('toolTF', '按类型：', [['', '全部', all.length], ...TYPES.map(([k, l]) => [k, l, all.filter((t: any) => t.type === k).length] as [string, string, number])], tf, setTf)}</div>
    <div className="card" style={{ padding: 0 }}><table id="toolTbl"><thead><tr><th>工具</th><th>类型</th><th>来源</th><th>描述</th><th>接入</th><th>入参</th><th>被意图使用</th><th>操作</th></tr></thead><tbody>
      {ts.map((t: any) => <tr key={t.name}><td><b>{t.display_name}</b><div className="mono">{t.name}</div></td><td><Badge c="b">{t.type_label}</Badge></td>
        <td>{srcCell(t)}</td>
        <td className="muted">{t.description}</td><td><End t={t} /></td><td>{t.params.map((p: any, i: number) => <span key={p.name} className="mono">{i > 0 && ', '}{p.name}{p.required ? '*' : ''}</span>)}</td>
        <td>{usedBy(t.used_by)}</td>
        <td style={{ whiteSpace: 'nowrap' }}><Btn s onClick={() => setTest(t)}>测试调用</Btn> {t.source === 'builtin' ? <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>平台提供<br />不可编辑 / 删除</div>
          : canEdit && <><Btn s onClick={() => setEdit(t)}>编辑</Btn><div style={{ marginTop: 4, textAlign: 'right' }}><a style={{ color: '#dc2626', fontSize: 12 }} onClick={() => del(t)}>删除</a></div></>}</td></tr>)}
      {ss.map((v: any) => { const sub = subs(v); const on = !!open[v.name]; const users = new Set(sub.flatMap((t: any) => (t.used_by || []).map((u: any) => u.code))); return [
        <tr key={'s' + v.name} className="srvrow" data-server={v.name}><td onClick={() => setOpen({ ...open, [v.name]: !on })}><span className="caret">{on ? '▾' : '▸'}</span><b>{v.display_name}</b><div className="mono" style={{ paddingLeft: 14 }}>{v.name}</div></td>
          <td><Badge c="p">MCP 服务</Badge><div className="muted" style={{ marginTop: 4 }}><a onClick={() => setOpen({ ...open, [v.name]: !on })}>{v.tool_count} 个工具</a></div></td>
          <td>{v.source === 'builtin' ? <Badge c="g">平台内置</Badge> : <span className="muted">外部</span>}</td>
          <td className="muted">{v.description || '—'}<div>启用 {v.enabled_count} / {v.tool_count}{v.missing_count > 0 && <> · <b style={{ color: '#b91c1c' }}>{v.missing_count} 个远端已消失</b></>}</div></td>
          <td><div className="muted" style={{ fontSize: 11 }}>{v.transport_label} · 鉴权：{v.auth.type}</div><div className="mono" style={{ fontSize: 11 }}>{serverEnd(v)}</div><div className="muted" style={{ fontSize: 11 }}>同步：{syncText(v)}</div></td>
          <td className="muted">按工具</td><td>{users.size ? <span>{users.size} 个意图</span> : <span className="muted">未使用</span>}</td>
          <td style={{ whiteSpace: 'nowrap' }}>{v.source === 'builtin' ? <div className="muted" style={{ fontSize: 11 }}>平台提供<br />不可编辑 / 同步</div>
            : canEdit && <><Btn s onClick={() => setSync(v)}>同步工具</Btn><div style={{ marginTop: 4, textAlign: 'right', fontSize: 12 }}><a onClick={() => setSrvEdit(v)}>编辑</a>　<a style={{ color: '#dc2626' }} onClick={() => delSrv(v)}>删除</a></div></>}</td></tr>,
        ...(on ? sub.length ? sub.map((t: any) => <tr key={t.name} className={'subrow' + (t.remote_missing ? ' missing' : '')} data-tool={t.name}>
          <td><b>{t.display_name}</b><div className="mono">{t.name}</div>{t.remote_missing && <Badge c="r">远端已消失</Badge>}</td>
          <td><Badge c="b">MCP 工具</Badge></td>
          <td>{v.source === 'builtin' ? <Badge c="g">平台内置</Badge> : <>
            <label style={{ display: 'block', whiteSpace: 'nowrap' }}><input type="checkbox" className="mEn" checked={t.enabled} disabled={!canEdit} onChange={e => patch(t, { enabled: e.target.checked })} /> 启用</label>
            <label style={{ display: 'block', whiteSpace: 'nowrap' }}><input type="checkbox" className="mCf" checked={t.confirm_required} disabled={!canEdit} onChange={e => patch(t, { confirm_required: e.target.checked })} /> 写操作需确认</label></>}</td>
          <td className="muted">{t.description}</td><td className="mono" style={{ fontSize: 11 }}>tools/call {t.mcp_tool}</td>
          <td>{t.params.map((p: any, i: number) => <span key={p.name} className="mono">{i > 0 && ', '}{p.name}{p.required ? '*' : ''}</span>)}</td>
          <td>{usedBy(t.used_by)}</td><td><Btn s onClick={() => setTest(t)}>测试调用</Btn></td></tr>)
          : [<tr key={'e' + v.name} className="subrow"><td colSpan={8} className="muted">还没有工具，点「同步工具」读取</td></tr>] : [])] })}
      {!ts.length && !ss.length && <tr><td colSpan={8} className="muted" style={{ textAlign: 'center', padding: 30 }}>没有符合条件的工具</td></tr>}
    </tbody></table></div>
    {edit && <ToolEdit t={edit.name ? edit : null} onClose={() => setEdit(null)} onDone={() => { setEdit(null); reload() }} />}
    {test && <ToolTest t={test} onClose={() => setTest(null)} />}
    {srvEdit && <ServerEdit sv={srvEdit.name ? srvEdit : null} onClose={() => setSrvEdit(null)} onDone={n => { const isNew = !srvEdit.name; setSrvEdit(null); reload(); setOpen(o => ({ ...o, [n]: true })); if (isNew) api.get('/mcp-servers/' + n).then(setSync) }} />}
    {sync && <SyncModal sv={sync} onClose={() => setSync(null)} onDone={() => { setOpen(o => ({ ...o, [sync.name]: true })); setSync(null); reload() }} />}
  </>
}

type P = { name: string; type: string; required: boolean; description: string }
function ToolEdit({ t, onClose, onDone }: { t: any | null; onClose: () => void; onDone: () => void }) {
  const { user } = useSession()
  const [f, setF] = useState<any>(() => t ? {
    type: t.type, name: t.name, display_name: t.display_name, description: t.description, owner: t.owner,
    url: t.url || '', method: t.method || 'GET', auth: t.auth || '无', output_example: t.output_example || (t.type === 'http' ? '{}' : ''),
        skill_id: t.skill_id || '', platform: t.platform || '', output_description: t.output_description || t.cli?.output_description || '',
    bin: t.cli?.bin || '', command_template: t.cli?.command_template || '', cli_auth: t.cli?.auth || '无', login_command: t.cli?.login_command || '',
    install: t.cli?.install || '', verified_version: t.cli?.verified_version || '', output_format: t.cli?.output_format || 'json', confirm_required: !!t.cli?.confirm_required, secret: '',
  } : { type: 'http', name: '', display_name: '', description: '', owner: user.display_name, url: '', method: 'GET', auth: '无', output_example: '{}',
    skill_id: '', platform: '', output_description: '', bin: '', command_template: '', cli_auth: '无', login_command: '', install: '', verified_version: '', output_format: 'json', confirm_required: false, secret: '' })
  const [params, setParams] = useState<P[]>(() => t ? t.params.map((p: any) => ({ ...p, description: p.description || '' })) : [{ name: '', type: 'string', required: true, description: '' }])
  const [err, setErr] = useState<string[]>()
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value })
  const save = async () => {
    let config: any
    if (f.type === 'http') config = { url: f.url, method: f.method, auth: f.auth, output_example: f.output_example }
    else if (f.type === 'cli') config = { bin: f.bin, command_template: f.command_template, auth: f.cli_auth, login_command: f.login_command, install: f.install, verified_version: f.verified_version, output_format: f.output_format, confirm_required: f.confirm_required, output_description: f.output_description }
    else config = { skill_id: f.skill_id, platform: f.platform, output_description: f.output_description }
    const body: any = { type: f.type, name: f.name.trim(), display_name: f.display_name, description: f.description, owner: f.owner, params, config }
    if (f.secret) body.secret = f.secret
    try {
      const r = t ? await api.put('/tools/' + t.name, body) : await api.post('/tools', body)
      toast(t ? '工具已更新' + (r.warnings?.length ? '（' + r.warnings.join('；') + '）' : '') : '工具已注册，运营可在意图中选用'); onDone()
    } catch (e) { setErr(errLines(e)) }
  }
  const sp = (i: number, k: keyof P, v: any) => setParams(params.map((p, j) => j === i ? { ...p, [k]: v } : p))
  const outLabel = f.type === 'skill' ? '输出说明' : f.type === 'cli' ? '输出说明 / 示例' : '出参示例（JSON）'
  return <Drawer testId="toolEdit" title={t ? `编辑工具 · ${t.name}` : '注册工具'} onClose={onClose} foot={<><Btn onClick={onClose}>取消</Btn> <Btn p id="toolSave" onClick={save}>保存</Btn></>}>
    <ErrBox lines={err} />
    <div className="grid g2"><div className="f"><label>工具类型 *</label><select id="tTy" value={f.type} disabled={!!t} onChange={set('type')}>{TYPES.filter(x => x[0] !== 'mcp').map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></div><div className="muted" style={{ alignSelf: 'end', paddingBottom: 12 }}>MCP 工具请用「注册 MCP 服务」登记后同步</div></div>
    <div className="grid g2"><div className="f"><label>工具名（英文，唯一）*</label><input type="text" id="tN" value={f.name} disabled={!!t} onChange={set('name')} placeholder="query_stock" /></div>
      <div className="f"><label>显示名 *</label><input type="text" id="tD" value={f.display_name} onChange={set('display_name')} placeholder="查库存" /></div></div>
    <div className="f"><label>描述 *（运营据此判断该挂到哪个意图）</label><input type="text" id="tDs" value={f.description} onChange={set('description')} /></div>
    {f.type === 'http' && <div className="grid g2"><div className="f"><label>接口地址 *</label><input type="text" id="tU" value={f.url} onChange={set('url')} placeholder="https://..." /></div>
      <div className="f"><label>方法</label><select id="tM" value={f.method} onChange={set('method')}>{['GET', 'POST', 'PUT', 'DELETE'].map(m => <option key={m}>{m}</option>)}</select></div></div>}
    {f.type === 'skill' && <div className="grid g2"><div className="f"><label>技能 / 工作流 ID *</label><input type="text" id="tK" value={f.skill_id} onChange={set('skill_id')} placeholder="skill_img_main_v2" /></div>
      <div className="f"><label>所属平台 *</label><input type="text" id="tPf" value={f.platform} onChange={set('platform')} placeholder="Moonclaw 技能平台" /></div></div>}
    {f.type === 'cli' && <>
      <div className="grid g2"><div className="f"><label>可执行程序 *</label><input type="text" id="tBin" value={f.bin} onChange={set('bin')} placeholder="xclaw-cli" /></div>
        <div className="f"><label>已验证版本</label><input type="text" id="tCv" value={f.verified_version} onChange={set('verified_version')} placeholder="2.2.10" /></div></div>
      <div className="f"><label>命令 / 子命令模板 *</label><input type="text" id="tCmd" className="mono" value={f.command_template} onChange={set('command_template')} placeholder="xclaw-cli mall mall-product detail get --sku {sku} --json" />
        <div className="muted">用 {'{参数名}'} 引用下方入参；选填参数为空时，连同它前面的 --选项 一起省略</div></div>
      <div className="grid g2"><div className="f"><label>鉴权方式</label><select id="tCa" value={f.cli_auth} onChange={set('cli_auth')}>{['无', 'CLI 内置登录（OAuth）', '环境变量 Token'].map(x => <option key={x}>{x}</option>)}</select></div>
        <div className="f"><label>登录 / 鉴权命令</label><input type="text" id="tLg" className="mono" value={f.login_command} onChange={set('login_command')} placeholder="xclaw-cli auth login" /></div></div>
      <div className="grid g2"><div className="f"><label>安装方式（所需环境）</label><input type="text" id="tIn" className="mono" value={f.install} onChange={set('install')} placeholder="curl -fsSL https://.../install | bash" /></div>
        <div className="f"><label>输出格式</label><select id="tOf" value={f.output_format} onChange={set('output_format')}><option value="json">JSON</option><option value="text">纯文本</option></select></div></div>
      <div className="f"><label><input type="checkbox" id="tCf" checked={f.confirm_required} onChange={set('confirm_required')} /> 写操作，Bot 执行前需用户确认</label></div></>}
    {<div className="sec"><b>入参</b><table><thead><tr><th>参数名</th><th>类型</th><th>必填</th><th>说明</th><th></th></tr></thead><tbody>
      {params.map((p, i) => <tr key={i}><td><input type="text" id={'pN' + i} value={p.name} onChange={e => sp(i, 'name', e.target.value)} style={{ width: 120 }} /></td>
        <td><select id={'pT' + i} value={p.type} onChange={e => sp(i, 'type', e.target.value)}>{['string', 'number', 'boolean'].map(x => <option key={x}>{x}</option>)}</select></td>
        <td><input type="checkbox" id={'pR' + i} checked={p.required} onChange={e => sp(i, 'required', e.target.checked)} /></td>
        <td><input type="text" id={'pD' + i} value={p.description} onChange={e => sp(i, 'description', e.target.value)} style={{ width: '100%' }} /></td>
        <td><a onClick={() => setParams(params.filter((_, j) => j !== i))}>删除</a></td></tr>)}</tbody></table>
      <Btn s onClick={() => setParams([...params, { name: '', type: 'string', required: false, description: '' }])}>＋ 添加入参</Btn></div>}
    <div className="f"><label>{outLabel}</label><textarea id="tO" className="mono" value={f.type === 'http' ? f.output_example : f.output_description} onChange={set(f.type === 'http' ? 'output_example' : 'output_description')} /></div>
    <div className="grid g2">{f.type === 'http' ? <div className="f"><label>鉴权方式</label><select id="tA" value={f.auth} onChange={set('auth')}>{['无', 'API Key', 'Bearer Token', 'OAuth2'].map(x => <option key={x}>{x}</option>)}</select></div> : <div />}
      <div className="f"><label>负责人 *</label><input type="text" id="tW" value={f.owner} onChange={set('owner')} /></div></div>
    {f.type === 'http' && f.auth !== '无' && <div className="f"><label>密钥（加密托管，只写不读{t?.has_secret ? '；已设置，留空保持不变' : ''}）</label><input type="password" id="tSec" value={f.secret} onChange={set('secret')} autoComplete="new-password" /></div>}
  </Drawer>
}

function ToolTest({ t, onClose }: { t: any; onClose: () => void }) {
  const { space } = useSession()
  const [v, setV] = useState<Record<string, string>>(() => Object.fromEntries(t.params.map((p: any) => [p.name, MOCKV[p.name] || ''])))
  const [r, setR] = useState<any>(null)
  const [err, setErr] = useState<string[]>()
  const hd = t.type === 'cli' ? <>CLI：<span className="mono">{t.cli.bin}</span> · 鉴权：{t.cli.auth}</> : t.type === 'skill' ? <>技能：<span className="mono">{t.skill_id}</span> · {t.platform}</>
    : t.type === 'mcp' ? <>MCP 服务：{t.mcp_server.display_name} <span className="mono">{serverEnd(t.mcp_server)}</span> · tools/call <span className="mono">{t.mcp_tool}</span>{t.confirm_required && <> · <Badge c="y">写操作需确认</Badge></>}</> : <><Badge c="b">{t.method}</Badge> <span className="mono">{t.url}</span> · 鉴权：{t.auth}</>
  const run = async () => {
    setErr(undefined)
    try { setR(await api.post(`/tools/${t.name}/test`, { values: v, space: space.id })) } catch (e) { setR(null); setErr(errLines(e)) }
  }
  return <Modal title={<>测试调用 · {t.display_name} <span className="mono">{t.name}</span></>} testId="toolTest" onClose={onClose} foot={<><Btn onClick={onClose}>关闭</Btn> <Btn p id="ttRun" onClick={run}>生成请求预览</Btn></>}>
    <div className="muted" style={{ marginBottom: 8 }}><Badge c="b">{t.type_label}</Badge> {hd}</div>
    {t.params.map((p: any) => <div className="f" key={p.name}><label>{p.name}{p.required ? ' *' : ''} <span className="muted">{p.type} {p.description}</span></label>
      <input type="text" value={v[p.name]} onChange={e => setV({ ...v, [p.name]: e.target.value })} /></div>)}
    <ErrBox lines={err} />
    {r && <div id="ttOut"><div className="ok">{r.note}</div><div className="muted">请求</div>
      <pre className="code">{t.type === 'cli' ? r.request.command : JSON.stringify(r.request, null, 2)}</pre>
      {r.response_example && <><div className="muted">登记的响应示例</div><pre className="code">{(() => { try { return JSON.stringify(JSON.parse(r.response_example), null, 2) } catch { return r.response_example } })()}</pre></>}</div>}
  </Modal>
}

function Access() {
  const { spaces, space, isAdmin } = useSession()
  const [sid, setSid] = useState(space.id)
  const sp = spaces.find(s => s.id === sid) || space
  const [keys, reloadKeys] = useAsync(() => isAdmin ? api.get('/api-keys') : Promise.resolve(null), [isAdmin])
  const [meta] = useAsync(() => api.get('/meta'), [])
  const [docs, setDocs] = useState<Record<string, string>>({})
  const [newKey, setNewKey] = useState(false)
  useEffect(() => {
    ['ai-guide', 'openapi'].forEach(n => api.get<string>('/docs/' + n).then(t => setDocs(d => ({ ...d, [n]: t }))).catch(e => setDocs(d => ({ ...d, [n]: '加载失败：' + errLines(e)[0] }))))
  }, [])
  const base = meta?.public_base_url || window.location.origin
  const spName = (id: string) => spaces.find(s => s.id === id)?.name || id
  const eg = sid === 'mc' ? { code: 'M.VIDEO.GIF', name: '商品动图 GIF', category_path: ['VIDEO', '商品视频'], description: '用户想把商品图做成循环播放的 GIF 动图，用于详情页或社媒；"做个动图""GIF"算；要 MP4 视频不算（→ 图生视频）', examples: ['这张图做成 GIF 动图', '详情页放个循环动图', '把三张图做成轮播 GIF'], slots: [{ name: 'product_image', required: true }, { name: 'ratio', required: false, ask: '动图用什么比例？' }] }
    : { code: 'B.STOCK.PREORDER', name: '预售/订货', category_path: ['STOCK', '现货'], description: '用户想在缺货时先下单预订；问能否预定、先拍后发算；问何时补货不算（→ 补货/到货时间）', examples: ['没货能先拍吗', '可以预定吗', '预售什么时候发'], slots: [{ name: 'sku', required: true, ask: '请问是哪一款？' }] }
  const revoke = async (k: any) => {
    if (!window.confirm('吊销后使用该 Key 的 AI 工具将无法访问，确认？')) return
    try { await api.post(`/api-keys/${k.id}/revoke`); reloadKeys(); toast('已吊销') } catch (e) { toast(errLines(e)[0]) }
  }
  const rtDl = async () => {
    try { const j = await api.get(`/spaces/${sid}/runtime-config`); const fn = `runtime-config_${sid}_v${j.version}.json`; download(fn, JSON.stringify(j, null, 2)); toast('已下载 ' + fn) } catch (e) { toast(errLines(e)[0]) }
  }
  const copy = (n: string, label: string) => {
    const t = docs[n] || ''
    const fb = () => { const ta = document.createElement('textarea'); ta.value = t; document.body.appendChild(ta); ta.select(); try { document.execCommand('copy'); toast('已复制 ' + label) } catch { toast('复制失败，请手动选择文本') } ta.remove() }
    if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(t).then(() => toast('已复制 ' + label), fb); else fb()
  }
  const en = sp.enabled_count
  return <>
    <p className="desc" id="accDesc">平台内置工具的调用方式：供运营的 AI 工具、维护用 AI 以及各 Bot / 智能体读写本平台。意图、槽位、分类、运行时配置按业务空间处理，请求头带 <span className="mono">X-Space-Id</span>（或 query 参数 <span className="mono">space</span>），必须是 Key 绑定的空间之一；知识库与工具全局通用，不需要 X-Space-Id。</p>
    <div className="card"><div className="row"><span className="muted">下方示例与运行时配置使用的空间：</span><select id="acSp" value={sid} onChange={e => setSid(e.target.value)}>{spaces.map(x => <option key={x.id} value={x.id}>{x.name}</option>)}</select><span className="muted">对应 {sp.bot}</span></div></div>
    <div className="card"><h3>API Key<span className="sp" />{isAdmin && <Btn p id="keyNew" onClick={() => setNewKey(true)}>＋ 新建 Key</Btn>}</h3>
      {!isAdmin ? <div className="muted">API Key 由管理员管理。</div> : <table id="keyTbl"><thead><tr><th>名称</th><th>Key</th><th>绑定空间（意图 / 槽位）</th><th>归属</th><th>权限</th><th>创建</th><th>最近调用</th><th>状态</th><th>操作</th></tr></thead><tbody>
        {keys?.length ? keys.map((k: any) => <tr key={k.id}><td>{k.name}</td><td className="mono">{k.key_hint}</td><td>{k.spaces.map((x: string) => <span key={x} className="tg">{spName(x)}</span>)}</td><td>{k.owner}</td>
          <td>{k.scopes.map((s: string) => <Badge key={s} c={s.includes('写') ? 'y' : 'n'}>{s}</Badge>)}</td><td className="muted">{fmt(k.created_at, false).slice(0, 10)}</td><td className="muted">{k.last_used_at ? fmt(k.last_used_at, false) : '—'}</td>
          <td><Badge c={k.status === '有效' ? 'g' : 'n'}>{k.status}</Badge></td><td>{k.status === '有效' && <Btn s d onClick={() => revoke(k)}>吊销</Btn>}</td></tr>)
          : <tr><td colSpan={9} className="muted" style={{ textAlign: 'center' }}>{keys ? '还没有 Key' : '加载中…'}</td></tr>}</tbody></table>}
      <div className="muted" style={{ marginTop: 6 }}>通过接口写入意图会在「意图管理 › 导入记录」留下「AI 工具（API）」记录；Key 只在创建时完整显示一次，平台只保存哈希。知识与工具类权限对所有空间生效；意图类权限只对绑定的空间生效。</div></div>
    <div className="card"><h3>REST 接口</h3><div className="muted" style={{ marginBottom: 6 }}>Base URL：{base} · Header：<span className="mono">Authorization: Bearer ipk_xxx</span> · <span className="mono">X-Space-Id: {sid}</span>（仅按空间的接口需要；不在 Key 绑定空间内返回 403）· 写接口需带 <span className="mono">Idempotency-Key</span>。全部接口已合并登记为「工具目录」里的平台内置工具。</div>
      <table id="apiTbl"><thead><tr><th>方法</th><th>路径</th><th>权限</th><th>空间</th><th>说明</th></tr></thead><tbody>{API_ROWS.map(r => { const g = /^\/api\/v1\/(kbs|tools|imports)/.test(r[1]); return <tr key={r[0] + r[1]}>
        <td><Badge c={r[0] === 'GET' ? 'g' : 'b'}>{r[0]}</Badge></td><td className="mono">{r[1]}</td><td><Badge c={r[2].includes('写') ? 'y' : 'n'}>{r[2]}</Badge></td><td>{g ? <span className="muted">全局</span> : <Badge c="b">X-Space-Id</Badge>}</td><td>{r[3]}</td></tr> })}</tbody></table>
      <div className="grid g2" style={{ marginTop: 10 }}><div><b>示例：AI 工具写回意图</b><pre className="code">{`POST /api/v1/intents:batchUpsert\nX-Space-Id: ${sid}\nIdempotency-Key: 7f3a-20261110-0001\n` + JSON.stringify({ mode: 'merge', intents: [eg] }, null, 2)}</pre></div>
        <div><b>返回</b><pre className="code">{JSON.stringify({ space: sid, created: 1, updated: 0, skipped: 0, items: [{ code: eg.code, result: 'created', status: '草稿' }], new_slots: [], conflicts: [], import_id: 'IMP-1004' }, null, 2)}</pre></div></div></div>
    <div className="card"><h3>导出运行时配置<span className="sp" /><Btn p id="rtDl" onClick={rtDl}>下载当前配置 JSON</Btn></h3>
      <div className="muted" style={{ marginBottom: 6 }}>供「{sp.bot}」拉取本空间已启用意图的完整配置（含空间设置 recognition、本空间槽位定义、知识用途、校验集全文、工具类型）。</div>
      <pre className="code">{`GET /api/v1/runtime-config?since_version=${sp.version - 1}\nX-Space-Id: ${sid}\nAuthorization: Bearer ipk_xxx\n\n→ 200 { "space": "${sid}", "version": ${sp.version}, "slots": [...], "intents": [...], "validation_sets": [...], "tools": [...] }\n→ 304 since_version 与当前 version 相同，无变化`}</pre>
      <div className="muted">当前空间 version：<b className="mono" id="rtVer">{sp.version}</b> · 已启用意图 {en} 个</div>
      <div className="note w" style={{ margin: '8px 0 0' }}>本空间的意图启用、停用或编辑后，version 会递增（各空间版本独立）；Bot 发现版本变化后应重新拉取，并清空意图识别缓存。</div></div>
    <div className="card"><h3>给 AI 的接入说明</h3><div className="muted" style={{ marginBottom: 8 }}>把下面两个文件交给运营使用的 AI 工具：先读说明，再按 OpenAPI 调用。</div>
      {([['ai-guide', 'AI接入说明.md', 'text/markdown'], ['openapi', 'openapi.yaml', 'application/yaml']] as const).map(([n, label, mt]) => <div key={n} style={{ marginBottom: 12 }}>
        <div className="row"><b className="mono">{label}</b><span className="sp" /><Btn s onClick={() => copy(n, label)}>复制</Btn> <Btn s onClick={() => { download(label, docs[n] || '', mt); toast('已下载 ' + label) }}>下载</Btn></div>
        <pre className="code" id={'doc_' + n} style={{ maxHeight: 260 }}>{docs[n] ?? '加载中…'}</pre></div>)}</div>
    <div className="card"><h3>MCP 服务</h3><div className="muted" style={{ marginBottom: 6 }}>支持 MCP 的 AI 工具（如 Claude、Cursor 等）添加以下配置即可直接调用（意图类工具可传 space 参数，默认取 Key 绑定的第一个空间；知识与工具类不需要）：</div>
      <pre className="code">{JSON.stringify({ mcpServers: { 'intent-platform': { url: base + '/mcp', headers: { Authorization: 'Bearer ipk_xxx', 'X-Space-Id': sid } } } }, null, 2)}</pre>
      <table><thead><tr><th>MCP 工具</th><th>说明</th></tr></thead><tbody>{MCP_ROWS.map(r => <tr key={r[0]}><td className="mono">{r[0]}</td><td>{r[1]}</td></tr>)}</tbody></table></div>
    {newKey && <NewKey onClose={() => setNewKey(false)} onDone={reloadKeys} />}
  </>
}

function NewKey({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const { spaces, space } = useSession()
  const SC = ['意图:读', '意图:写', '知识:读', '知识:写', '工具:读']
  const [name, setName] = useState('')
  const [sps, setSps] = useState<string[]>([space.id])
  const [scs, setScs] = useState<string[]>(SC.filter(s => s.includes('读')))
  const [err, setErr] = useState<string[]>()
  const [tok, setTok] = useState<any>(null)
  const tg = (a: string[], v: string, on: boolean) => on ? [...a, v] : a.filter(x => x !== v)
  const ok = async () => {
    if (!name.trim() || !sps.length || !scs.length) { setErr(['请填写名称，并至少选择一项权限和一个绑定空间']); return }
    try { const r = await api.post('/api-keys', { name: name.trim(), spaces: sps, scopes: scs }); setTok(r); onDone() } catch (e) { setErr(errLines(e)) }
  }
  return <Modal title="新建 API Key" testId="keyModal" onClose={onClose} foot={tok ? <Btn p id="keyDone" onClick={onClose}>完成</Btn> : <><Btn onClick={onClose}>取消</Btn> <Btn p id="keyDo" onClick={ok}>创建</Btn></>}>
    <div className="f"><label>名称（用于识别调用方）</label><input type="text" id="kN" value={name} disabled={!!tok} onChange={e => setName(e.target.value)} placeholder="如：林夏的 AI 助手" /></div>
    <div className="f"><label>绑定空间（可多选；用于意图 / 槽位 / 运行时配置，知识与工具不受限）</label><div className="row">{spaces.map(x => <label key={x.id}><input type="checkbox" className="kSpc" disabled={!!tok} checked={sps.includes(x.id)} onChange={e => setSps(tg(sps, x.id, e.target.checked))} /> {x.name}</label>)}</div></div>
    <div className="f"><label>权限</label>{SC.map(s => <label key={s} style={{ display: 'inline-block', marginRight: 12 }}><input type="checkbox" className="kS" disabled={!!tok} checked={scs.includes(s)} onChange={e => setScs(tg(scs, s, e.target.checked))} /> {s}</label>)}</div>
    <ErrBox lines={err} />
    {tok && <div className="ok" id="kOut">已创建（绑定空间：{tok.spaces.map((x: string) => spaces.find(s => s.id === x)?.name || x).join('、')}）。请立即复制，关闭后不再显示完整 Key：<div className="mono" id="kTok" style={{ marginTop: 4, userSelect: 'all', wordBreak: 'break-all' }}>{tok.token}</div></div>}
  </Modal>
}
