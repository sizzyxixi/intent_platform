import { useEffect, useState } from 'react'
import { api, download, errLines, fmt } from '../api'
import demo from '../demoContent.json'
import { useSession } from '../session'
import { Badge, Btn, ErrBox, Modal, toast, useAsync } from '../ui'
import { credText, serverEnd, syncText } from './McpServers'
import ToolEdit, { KINDS, TypeTag, WF_PLAT, pTypeLabel } from './ToolEdit'

const PAGE = 100
const API_ROWS = (demo as any).API_ROWS as string[][]
const MCP_ROWS = (demo as any).MCP_ROWS as string[][]

export default function ToolsPage({ tab }: { tab: string }) {
  const { go } = useSession()
  return <>
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
  if (t.type === 'cli') return <><div className="mono clamp2" style={s} title={t.cli.command_template}>{t.cli.command_template}</div>{t.cli.pack && <div className="muted" style={s}>{t.cli.pack_name || t.cli.pack}{t.cli.group && ` › ${t.cli.group}`}</div>}<div className="muted" style={s}>鉴权：{t.cli.auth}{t.cli.auth_env && <>（或 <span className="mono">${t.cli.auth_env}</span>）</>} · 输出 {t.cli.output_format === 'json' ? 'JSON' : '纯文本'}</div></>
  if (t.type === 'skill') return t.skill?.files?.length ? <div className="muted" style={s}>技能包 v{t.skill.version} · {t.skill.files.length} 个文件</div> : <div style={s}><Badge c="y">待上传技能包</Badge></div>
  if (t.type === 'workflow') { const w = t.workflow || {}; return <div className="muted" style={s}>{WF_PLAT[w.platform]}{w.platform === 'coze' ? ` · ${w.region} · ${w.workflow_id}` : w.platform === 'dify' ? ` · ${w.base_url}` : ` · ${w.url}`} · {w.response_mode === 'streaming' ? '流式' : '同步'}</div> }
  if (t.type === 'mcp') return <div className="mono" style={s}>tools/call {t.mcp_tool}</div>
  return <><Badge>{t.method}</Badge><div className="mono clamp2" style={s} title={t.url}>{t.url}</div></>
}

function ToolList() {
  const { canEdit } = useSession()
  const [data, reload] = useAsync(() => Promise.all([api.get('/tools'), api.get('/mcp-servers')]), [])
  const [sf, setSf] = useState('')
  const [tf, setTf] = useState('')
  const [edit, setEdit] = useState<{ tool?: any; server?: any } | null>(null)
  const [test, setTest] = useState<any>(null)
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const [q, setQ] = useState('')
  const [pk, setPk] = useState('')
  const [lim, setLim] = useState(PAGE)
  useEffect(() => setLim(PAGE), [q, pk, tf, sf])
  if (!data) return <div className="loading">加载中…</div>
  const [all, servers] = data as [any[], any[]]
  const nb = all.filter((t: any) => t.source === 'builtin').length
  const nbSrv = servers.filter((v: any) => v.source === 'builtin').length
  const flat = all.filter((t: any) => t.type !== 'mcp')
  const kw = q.trim().toLowerCase()
  const hit = (...xs: string[]) => !kw || xs.some(x => (x || '').toLowerCase().includes(kw))
  const packs: [string, string, number][] = Object.values(flat.filter((t: any) => t.cli?.pack).reduce((m: any, t: any) => {
    (m[t.cli.pack] ||= [t.cli.pack, t.cli.pack_name || t.cli.pack, 0])[2]++; return m }, {}))
  const ts = flat.filter((t: any) => (!tf || t.type === tf) && (!sf || t.source === sf) && (!pk || t.cli?.pack === pk)
    && hit(t.name, t.display_name, t.description, t.cli?.command_template, t.cli?.group))
  const ss = servers.filter((v: any) => (!tf || tf === 'mcp') && (!sf || v.source === sf) && !pk && hit(v.name, v.display_name, v.description))
  const subs = (v: any) => all.filter((t: any) => t.server === v.name)
  const cnt = (k: string) => k === 'mcp' ? servers.length : flat.filter((t: any) => t.type === k).length
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
    try { await api.patch('/tools/' + t.name, body); reload(); toast('已更新 ' + t.name) } catch (e) { toast(errLines(e)[0]) }
  }
  const usedBy = (list: any[]) => list?.length ? list.map((u: any) => <div key={u.space + u.code}><span className="mono" style={{ color: '#2563eb' }}>{u.code}</span><div><span className="tg">{u.space_name}</span></div></div>) : <span className="muted">未使用</span>
  const srcCell = (t: any) => t.source === 'builtin' ? <><Badge c="g">平台内置</Badge><div style={{ marginTop: 4 }}>{t.maintenance ? <Badge c="y">维护类</Badge> : <Badge>可在意图中使用</Badge>}</div></> : <span className="muted">外部</span>
  const paramCell = (t: any) => t.type === 'skill' ? <span className="muted">无（槽位作上下文）</span> : <div className="mono clamp3" title={t.params.map((p: any) => p.name + (p.required ? '*' : '')).join(', ')}>{t.params.length ? t.params.map((p: any) => p.name + (p.required ? '*' : '')).join(', ') : <span className="muted">无</span>}</div>
  return <>
    <div className="card"><div className="row"><span className="sp" />
      {canEdit && <Btn p id="toolNew" onClick={() => setEdit({})}>＋ 注册工具</Btn>}</div>
      {fb('toolSF', '按来源：', [['', '全部', flat.length + servers.length], ['external', '外部', flat.length - nb + servers.length - nbSrv], ['builtin', '平台内置', nb + nbSrv]], sf, setSf)}
      {fb('toolTF', '按类型：', [['', '全部', flat.length + servers.length], ...KINDS.map(([k, l]) => [k, l, cnt(k)] as [string, string, number])], tf, setTf)}
      <div className="row" style={{ marginTop: 8 }}>
        {packs.length > 0 && <select id="toolPack" value={pk} onChange={e => setPk(e.target.value)}><option value="">全部 CLI 命令包</option>
          {packs.map(([k, l, n]) => <option key={k} value={k}>{l}（{n}）</option>)}</select>}
        <input type="text" id="toolQ" value={q} onChange={e => setQ(e.target.value)} placeholder="搜索工具名 / 描述 / 命令" style={{ width: 240 }} />
        <span className="muted">{ts.length + ss.length} 个</span></div></div>
    <div className="card tblwrap" style={{ padding: 0 }}><table id="toolTbl" className="fixed"><colgroup><col style={{ width: 190 }} /><col style={{ width: 80 }} /><col style={{ width: 64 }} /><col /><col style={{ width: 180 }} /><col style={{ width: 110 }} /><col style={{ width: 90 }} /><col style={{ width: 92 }} /></colgroup><thead><tr><th>工具</th><th>类型</th><th>来源</th><th>描述</th><th>接入</th><th>入参</th><th>被意图使用</th><th>操作</th></tr></thead><tbody>
      {ts.slice(0, lim).map((t: any) => <tr key={t.name} data-tool={t.name}><td><b>{t.display_name}</b><div className="mono">{t.name}</div></td><td><TypeTag type={t.type} />{t.confirm_required && <div style={{ marginTop: 4 }}><Badge c="y">写操作需确认</Badge></div>}</td>
        <td>{srcCell(t)}</td>
        <td className="muted"><div className="clamp3" title={t.description}>{t.description}</div></td><td><End t={t} /></td><td>{paramCell(t)}</td>
        <td>{usedBy(t.used_by)}</td>
        <td><Btn s onClick={() => setTest(t)}>测试调用</Btn> {t.source === 'builtin' ? <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>平台提供<br />不可编辑 / 删除</div>
          : canEdit && <><Btn s onClick={() => setEdit({ tool: t })}>编辑</Btn><div style={{ marginTop: 4 }}><a style={{ color: '#dc2626', fontSize: 12 }} onClick={() => del(t)}>删除</a></div></>}</td></tr>)}
      {ts.length > lim && <tr id="toolMore"><td colSpan={8} className="muted" style={{ textAlign: 'center' }}>已显示 {lim} / {ts.length} 个，可用上方搜索或命令包缩小范围 · <a onClick={() => setLim(lim + PAGE)}>再显示 {Math.min(PAGE, ts.length - lim)} 个</a></td></tr>}
      {ss.map((v: any) => { const sub = subs(v); const on = !!open[v.name]; const users = new Set(sub.flatMap((t: any) => (t.used_by || []).map((u: any) => u.code))); return [
        <tr key={'s' + v.name} className="srvrow" data-server={v.name}><td onClick={() => setOpen({ ...open, [v.name]: !on })}><span className="caret">{on ? '▾' : '▸'}</span><b>{v.display_name}</b><div className="mono" style={{ paddingLeft: 14 }}>{v.name}</div></td>
          <td><Badge c="p">MCP 服务</Badge><div className="muted" style={{ marginTop: 4 }}><a onClick={() => setOpen({ ...open, [v.name]: !on })}>{v.tool_count} 个工具</a></div></td>
          <td>{v.source === 'builtin' ? <Badge c="g">平台内置</Badge> : <span className="muted">外部</span>}{v.example_data && <div style={{ marginTop: 4 }}><Badge c="y" title="当前工具列表为示例数据，真实同步可拉取完整列表">示例数据</Badge></div>}</td>
          <td className="muted"><div className="clamp3" title={v.description}>{v.description || '—'}</div><div>启用 {v.enabled_count} / {v.tool_count}{v.missing_count > 0 && <> · <b style={{ color: '#b91c1c' }}>{v.missing_count} 个远端已消失</b></>}</div></td>
          <td><div className="muted" style={{ fontSize: 11 }}>{v.transport_label} · {credText(v)}</div><div className="mono" style={{ fontSize: 11 }}>{serverEnd(v)}</div><div className="muted" style={{ fontSize: 11 }}>同步：{syncText(v)}</div></td>
          <td className="muted">按工具</td><td>{users.size ? <span>{users.size} 个意图</span> : <span className="muted">未使用</span>}</td>
          <td>{v.source === 'builtin' ? <div className="muted" style={{ fontSize: 11 }}>平台提供<br />不可编辑 / 同步</div>
            : canEdit && <><Btn s onClick={() => setEdit({ server: v })}>同步工具</Btn><div style={{ marginTop: 4, fontSize: 12 }}><a onClick={() => setEdit({ server: v })}>编辑</a>　<a style={{ color: '#dc2626' }} onClick={() => delSrv(v)}>删除</a></div></>}</td></tr>,
        ...(on ? sub.length ? sub.map((t: any) => <tr key={t.name} className={'subrow' + (t.remote_missing ? ' missing' : '')} data-tool={t.name}>
          <td><b>{t.display_name}</b><div className="mono">{t.name}</div>{t.remote_missing && <Badge c="r">远端已消失</Badge>}</td>
          <td><TypeTag type="mcp" /></td>
          <td>{v.source === 'builtin' ? <Badge c="g">平台内置</Badge> : <>
            <label style={{ display: 'block', whiteSpace: 'nowrap' }}><input type="checkbox" className="mEn" checked={t.enabled} disabled={!canEdit} onChange={e => patch(t, { enabled: e.target.checked })} /> 启用</label>
            <label style={{ display: 'block', whiteSpace: 'nowrap' }}><input type="checkbox" className="mCf" checked={t.confirm_required} disabled={!canEdit} onChange={e => patch(t, { confirm_required: e.target.checked })} /> 写操作需确认</label></>}</td>
          <td className="muted"><div className="clamp3" title={t.description}>{t.description}</div></td><td className="mono" style={{ fontSize: 11 }}>tools/call {t.mcp_tool}</td>
          <td>{paramCell(t)}</td>
          <td>{usedBy(t.used_by)}</td><td><Btn s onClick={() => setTest(t)}>测试调用</Btn></td></tr>)
          : [<tr key={'e' + v.name} className="subrow"><td colSpan={8} className="muted">还没有工具，点「同步工具」读取</td></tr>] : [])] })}
      {!ts.length && !ss.length && <tr><td colSpan={8} className="muted" style={{ textAlign: 'center', padding: 30 }}>没有符合条件的工具</td></tr>}
    </tbody></table></div>
    {edit && <ToolEdit tool={edit.tool} server={edit.server} onClose={() => setEdit(null)} onDone={n => { reload(); if (n) setOpen(o => ({ ...o, [n]: true })) }} />}
    {test && <ToolTest t={test} onClose={() => setTest(null)} />}
  </>
}

function Pre({ v }: { v: any }) {
  const txt = typeof v === 'string' ? v : JSON.stringify(v, null, 2)
  return <pre className="code" style={{ maxHeight: 320 }}>{txt}</pre>
}

function ToolTest({ t, onClose }: { t: any; onClose: () => void }) {
  const { space, isAdmin } = useSession()
  const [v, setV] = useState<Record<string, string>>(() => Object.fromEntries(t.params.map((p: any) => [p.name, p.default || ''])))
  const [r, setR] = useState<any>(null)
  const [err, setErr] = useState<string[]>()
  const [busy, setBusy] = useState(false)
  const [ok, setOk] = useState(false)
  const w = t.workflow || {}
  const write = t.confirm_required || (t.source === 'builtin' && t.maintenance)
  const hd = t.type === 'cli' ? <>CLI：<span className="mono">{t.cli.bin}</span> · 鉴权：{t.cli.auth}</>
    : t.type === 'skill' ? <>技能包 v{t.skill?.version || 0} · {t.skill?.files?.length || 0} 个文件</>
    : t.type === 'workflow' ? <>{WF_PLAT[w.platform]} · {w.response_mode === 'streaming' ? '流式' : '同步'}{w.auth_env && <> · 凭据 <span className="mono">${w.auth_env}</span></>}</>
    : t.type === 'mcp' ? <>MCP 服务：{t.mcp_server.display_name} <span className="mono">{serverEnd(t.mcp_server)}</span> · tools/call <span className="mono">{t.mcp_tool}</span></> : <><Badge c="b">{t.method}</Badge> <span className="mono">{t.url}</span> · 鉴权：{t.auth}</>
  const run = async (dry: boolean) => {
    setErr(undefined); setBusy(true); setR(null)
    try { setR(await api.post(`/tools/${t.name}/test`, { values: v, space: space.id, confirm: ok, dry_run: dry })) } catch (e) { setErr(errLines(e)) } finally { setBusy(false) }
  }
  const out = r?.stdout_json ?? r?.response_json ?? r?.stdout ?? r?.response
  return <Modal title={<>测试调用 · {t.display_name} <span className="mono">{t.name}</span></>} testId="toolTest" onClose={onClose} foot={<>
    <Btn onClick={onClose}>关闭</Btn> {t.type !== 'skill' && <Btn id="ttPrev" onClick={() => run(true)} disabled={busy}>只看请求</Btn>}{' '}
    {isAdmin ? <Btn p id="ttRun" onClick={() => run(false)} disabled={busy || (write && !ok)}>{busy ? '执行中…' : t.type === 'skill' ? '查看技能包' : '执行'}</Btn> : <span className="muted">执行仅管理员可用</span>}</>}>
    <div className="muted" style={{ marginBottom: 8 }}><TypeTag type={t.type} /> {hd}{write && <> · <Badge c="y">写操作需确认</Badge></>}</div>
    {t.type === 'skill' && <div className="note" style={{ marginTop: 0 }}>技能不执行：Bot 把 SKILL.md 交给 AI 使用，这里只展示技能包内容。</div>}
    {t.params.map((p: any) => <div className="f" key={p.name}><label>{p.name}{p.required ? ' *' : ''} <span className="muted">{pTypeLabel(p.type)} {p.description}{p.default ? ` · 默认 ${p.default}` : ''}</span></label>
      <input type="text" value={v[p.name]} placeholder={p.default || (p.type === 'file_list' || p.type === 'string[]' ? '多个值用逗号分隔' : '')} onChange={e => setV({ ...v, [p.name]: e.target.value })} /></div>)}
    {write && <label id="ttConfirm" style={{ display: 'block', margin: '6px 0' }}><input type="checkbox" checked={ok} onChange={e => setOk(e.target.checked)} /> 我确认这是写操作，会真实修改数据</label>}
    <ErrBox lines={err} />
    {r && <div id="ttOut">
      {r.executed === false && !r.skill && <div className="muted">请求预览（未执行）</div>}
      {r.executed && <div className="row" id="ttStatus" style={{ margin: '4px 0' }}><Badge c={r.ok ? 'g' : 'r'}>{r.ok ? '成功' : '失败'}</Badge>
        {r.exit_code !== undefined && <span className="muted">退出码 {r.exit_code}</span>}{r.status !== undefined && <span className="muted">HTTP {r.status}</span>}
        {r.duration_ms !== undefined && <span className="muted">耗时 {r.duration_ms} ms</span>}{r.error_code && <span className="mono">{r.error_code}</span>}{r.truncated && <span className="muted">输出过长已截断</span>}</div>}
      {r.error && <div className="note w" id="ttErr" style={{ margin: '6px 0' }}>{r.error}{r.hint && <div style={{ marginTop: 4 }}>{r.hint}</div>}</div>}
      {r.request && <><div className="muted">请求</div><Pre v={r.request.command || r.request} /></>}
      {out !== undefined && out !== '' && <><div className="muted">{r.stdout !== undefined ? '输出（stdout）' : '响应'}</div><Pre v={out} /></>}
      {r.stderr && <><div className="muted">stderr</div><Pre v={r.stderr} /></>}
      {r.skill && <><div className="muted">文件</div><Pre v={r.skill.files.map((f: any) => `${f.path}  ${f.size} B`).join('\n')} /><div className="muted">SKILL.md</div><Pre v={r.skill.skill_md} /></>}
      {r.response_example && <><div className="muted">登记的响应示例</div><Pre v={(() => { try { return JSON.parse(r.response_example) } catch { return r.response_example } })()} /></>}</div>}
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
    <p className="desc" id="accDesc">平台内置工具的调用方式：供运营的 AI 工具、维护用 AI 以及各 Bot / 智能体读写本平台。意图、槽位、分类、运行时配置按业务空间处理，请求头带 <span className="mono">X-Space-Id</span>（或 query 参数 <span className="mono">space</span>），必须是 Key 绑定的空间之一；知识库与工具接口不需要 X-Space-Id。</p>
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
