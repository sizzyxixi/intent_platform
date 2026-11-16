import { useEffect, useState } from 'react'
import { api, errLines, fmt, qs } from '../api'
import { useSession } from '../session'
import { Badge, Btn, ErrBox, Modal, Pager, ST_C, toast, useAsync } from '../ui'
import IntentEditor from './IntentEditor'
import ImportModal from './ImportModal'
import { SlotForm } from './Slots'
import { CatNode, Tax, leaves } from '../taxonomy'

const TABS: [string, string][] = [['list', '意图列表'], ['slots', '槽位'], ['cfg', '空间设置'], ['log', '导入记录']]

export default function IntentsPage({ tab }: { tab: string }) {
  const { go } = useSession()
  const [editing, setEditing] = useState<string | null>(null)
  const [ver, setVer] = useState(0)
  const bump = () => setVer(v => v + 1)
  const { space } = useSession()
  useEffect(() => { setEditing(null) }, [tab, space.id])
  return <>
    <div className="tabs" style={{ margin: '0 0 12px' }} id="imTabs">
      {TABS.map(([k, n]) => <div key={k} className={tab === k ? 'on' : ''} onClick={() => go('#/intents/' + k)}>{n}</div>)}
    </div>
    {tab === 'list' && <IntentList onEdit={setEditing} ver={ver} />}
    {tab === 'slots' && <SlotsTab onEdit={setEditing} ver={ver} />}
    {tab === 'cfg' && <RecognitionTab />}
    {tab === 'log' && <ImportLog />}
    {editing && <IntentEditor code={editing} onClose={() => setEditing(null)} onSaved={bump} />}
  </>
}

function IntentList({ onEdit, ver }: { onEdit: (c: string) => void; ver: number }) {
  const { space, canEdit, reloadSpaces } = useSession()
  const [f, setF] = useState({ category: '', q: '', status: '' })
  const [qIn, setQIn] = useState('')
  const [stIn, setStIn] = useState('')
  const [page, setPage] = useState(1)
  const [n, setN] = useState(0)
  const [newOpen, setNewOpen] = useState(false)
  const [imOpen, setImOpen] = useState(false)
  const [actionErr, setActionErr] = useState<string[]>()
  const size = 100
  const [tax] = useAsync<Tax>(() => api.get(`/spaces/${space.id}/taxonomy`), [space.id, ver, n])
  const [data] = useAsync(() => api.get(`/spaces/${space.id}/intents` + qs({ ...f, page, page_size: size })),
    [space.id, f.category, f.q, f.status, page, ver, n])
  const reload = () => { setN(x => x + 1); reloadSpaces() }
  const quick = async (code: string, status: string) => {
    try {
      await api.post(`/intents/${code}/status`, { status })
      setActionErr(undefined)
      toast(`${code} 已${status}`)
      reload()
    } catch (e: any) {
      const lines = errLines(e)
      setActionErr(lines)
      toast(lines.join('；'))
      if (status === '启用') onEdit(code)
    }
  }
  const catCols = tax ? tax.levels.slice(0, -1) : []
  const leafName = tax ? tax.levels[tax.levels.length - 1] : '意图'
  return <div className="split">
    <div className="tree" id="l1Tree">
      <div className={'tn l1' + (!f.category ? ' on' : '')} onClick={() => { setF({ ...f, category: '' }); setPage(1) }}><span>全部</span><span className="muted">{data?.all_count ?? ''}</span></div>
      {tax?.tree.map(a => <div key={a.id} className={'tn l1' + (f.category === String(a.id) ? ' on' : '')} onClick={() => { setF({ ...f, category: String(a.id) }); setPage(1) }}>
        <span>{a.name}</span><span className="muted">{a.count}</span></div>)}
    </div>
    <div>
      {actionErr?.length ? <div className="card" id="intentActionErr" style={{ marginBottom: 8 }}><ErrBox lines={actionErr} title="操作失败：" /></div> : null}
      <div className="card"><div className="row">
        <input type="text" id="fQ" placeholder="搜索编码 / 名称 / 例句" value={qIn} onChange={e => setQIn(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') { setF({ ...f, q: qIn, status: stIn }); setPage(1) } }} style={{ width: 240 }} />
        <select id="fSt" value={stIn} onChange={e => setStIn(e.target.value)}><option value="">全部状态</option>{['草稿', '启用', '停用'].map(s => <option key={s}>{s}</option>)}</select>
        <Btn id="fApply" onClick={() => { setF({ ...f, q: qIn, status: stIn }); setPage(1) }}>筛选</Btn>
        <span className="sp" />
        {canEdit && <Btn id="imOpen" onClick={() => setImOpen(true)}>导入意图</Btn>}
        {canEdit && <Btn p id="newIntent" onClick={() => setNewOpen(true)}>＋ 新建意图</Btn>}
      </div></div>
      <div className="card tblwrap" style={{ padding: 0 }}><table id="intentTbl" className="fixed" style={{ minWidth: 760 }}><colgroup><col style={{ width: 150 }} />{catCols.map(n => <col key={n} style={{ width: 76 }} />)}<col /><col style={{ width: 44 }} /><col style={{ width: 44 }} /><col style={{ width: 200 }} /><col style={{ width: 60 }} /><col style={{ width: 104 }} /></colgroup><thead><tr><th>编码</th>{catCols.map(n => <th key={n}>{n}</th>)}<th>{leafName}</th><th>例句</th><th>知识</th><th>工具</th><th>状态</th><th>操作</th></tr></thead>
        <tbody>{!data ? <tr><td colSpan={8 + catCols.length} className="loading">加载中…</td></tr> : data.items.length ? data.items.map((c: any) => <tr key={c.code}>
          <td className="mono">{c.code}</td>{catCols.map((n, i) => <td key={n} className="muted">{c.category_path[i]?.name || '—'}</td>)}
          <td><a onClick={() => onEdit(c.code)}>{c.name}</a>{c.handling && c.handling.mode && c.handling.mode !== 'tool_flow' ? <div className="muted">{c.handling.mode_label || c.handling.mode}</div> : null}<div className="muted clamp2" title={c.description}>{c.description || ''}</div></td>
          <td>{c.example_count}</td><td>{c.kb_count || <span className="muted">0</span>}</td>
          <td>{c.tools.length ? <div className="mono clamp3" style={{ fontSize: 11 }} title={c.tools.join('\n')}>{c.tools.map((t: string) => <div key={t}>{t}</div>)}</div> : <span className="muted">0</span>}</td>
          <td><Badge c={ST_C[c.status]}>{c.status}</Badge></td>
          <td style={{ whiteSpace: 'nowrap' }}><Btn s onClick={() => onEdit(c.code)}>{canEdit ? '编辑' : '查看'}</Btn>
            {canEdit && (c.status === '启用' ? <Btn s onClick={() => quick(c.code, '停用')}>停用</Btn> : <Btn s onClick={() => quick(c.code, '启用')}>启用</Btn>)}</td>
        </tr>) : <tr><td colSpan={8 + catCols.length} className="muted" style={{ textAlign: 'center', padding: 30 }}>没有符合条件的意图</td></tr>}</tbody></table>
        {data && <Pager page={page} total={data.total} size={size} onPage={setPage} />}
      </div>
    </div>
    {newOpen && tax && <NewIntent tax={tax} root={f.category} onClose={() => setNewOpen(false)} onDone={code => { setNewOpen(false); reload(); onEdit(code) }} />}
    {imOpen && <ImportModal onClose={() => setImOpen(false)} onDone={(st) => { setImOpen(false); reload(); if (st) { setF({ category: '', q: '', status: st }); setStIn(st) } }} />}
  </div>
}

function NewIntent({ tax, root, onClose, onDone }: { tax: Tax; root: string; onClose: () => void; onDone: (c: string) => void }) {
  const { space, go } = useSession()
  const goTax = () => { onClose(); go('#/intents/cfg'); setTimeout(() => document.getElementById('lvCard')?.scrollIntoView({ block: 'start' }), 120) }
  const opts = leaves(tax)
  const [p, setP] = useState(String(opts.find(o => String(o.root.id) === root)?.id || opts[0]?.id || ''))
  const [code, setCode] = useState('')
  const [name, setName] = useState('')
  const [err, setErr] = useState<string[]>()
  const cur = opts.find(o => String(o.id) === p)
  const eg = `${space.code_prefix}.${cur?.root.code ? cur.root.code + '.' : ''}XXX`
  const ok = async () => {
    try { const r = await api.post(`/spaces/${space.id}/intents`, { code: code.trim().toUpperCase(), name: name.trim(), category_id: p ? Number(p) : null }); onDone(r.code) }
    catch (e) { setErr(errLines(e)) }
  }
  return <Modal title="新建意图" onClose={onClose} foot={<><Btn onClick={onClose}>取消</Btn><Btn p id="newIntentDo" onClick={ok}>创建草稿并编辑</Btn></>}>
    <div className="muted" style={{ marginBottom: 8 }}>空间：{space.name}{tax.category_levels === 0 && <a id="niTax" onClick={goTax} style={{ marginLeft: 8 }}>管理分类</a>}</div>
    {tax.category_levels > 0 && <div className="f"><label>所属分类（{tax.levels.slice(0, -1).join(' / ')}）<a id="niTax" onClick={goTax} style={{ fontWeight: 'normal', marginLeft: 8 }}>管理分类</a></label><select id="niP" value={p} onChange={e => setP(e.target.value)}>{opts.map(o => <option key={o.id} value={o.id}>{o.label}</option>)}</select></div>}
    <div className="f"><label>编码（唯一，如 {eg}）</label><input type="text" id="niC" value={code} onChange={e => setCode(e.target.value)} placeholder={eg} /></div>
    <div className="f"><label>名称</label><input type="text" id="niN" value={name} onChange={e => setName(e.target.value)} /></div>
    <ErrBox lines={err} />
  </Modal>
}

/* ---------------- 槽位 ---------------- */
function SlotsTab({ onEdit, ver }: { onEdit: (c: string) => void; ver: number }) {
  const { space, canEdit } = useSession()
  const [list, reload] = useAsync(() => api.get(`/spaces/${space.id}/slots`), [space.id, ver])
  const [edit, setEdit] = useState<any>(null)
  const [uses, setUses] = useState<any>(null)
  const [blocked, setBlocked] = useState<any>(null)
  const del = async (s: any) => {
    if (s.used_by.length) { setBlocked(s); return }
    if (!window.confirm(`确认删除槽位「${s.name}」？`)) return
    try { await api.del(`/spaces/${space.id}/slots/${s.name}`); toast('已删除槽位 ' + s.name); reload() } catch (e) { toast(errLines(e)[0]) }
  }
  const useTable = (s: any) => <table><thead><tr><th>编码</th><th>名称</th></tr></thead><tbody>{s.used_by_detail.map((c: any) =>
    <tr key={c.code}><td><a className="mono" onClick={() => { setUses(null); setBlocked(null); onEdit(c.code) }}>{c.code}</a></td><td>{c.name}</td></tr>)}</tbody></table>
  return <>
    {canEdit && <div className="card"><div className="row"><span className="sp" /><Btn p id="slotNew" onClick={() => setEdit({})}>＋ 新建槽位</Btn></div></div>}
    <div className="card" style={{ padding: 0 }}><table id="slotTbl"><thead><tr><th>名称</th><th>类型</th><th>可选值</th><th>默认追问话术</th><th>被哪些意图使用</th><th>操作</th></tr></thead>
      <tbody>{!list ? <tr><td colSpan={6} className="loading">加载中…</td></tr> : list.length ? list.map((s: any) => <tr key={s.name}>
        <td><b>{s.display_name}</b><div className="mono">{s.name}</div></td><td>{s.type}</td>
        <td>{s.type === '枚举' ? s.options.join(',') : <span className="muted">—</span>}</td><td>{s.default_ask}</td>
        <td>{s.used_by.length ? <>{s.used_by.slice(0, 6).map((c: string) => <div key={c}><a className="mono" onClick={() => onEdit(c)}>{c}</a></div>)}
          {s.used_by.length > 6 && <a onClick={() => setUses(s)}>共 {s.used_by.length} 个 »</a>}</> : <span className="muted">未使用</span>}</td>
        <td style={{ whiteSpace: 'nowrap' }}>{canEdit && <><Btn s onClick={() => setEdit(s)}>编辑</Btn><Btn s d onClick={() => del(s)}>删除</Btn></>}</td>
      </tr>) : <tr><td colSpan={6} className="muted" style={{ textAlign: 'center', padding: 30 }}>本空间还没有槽位</td></tr>}</tbody></table></div>
    {edit && <SlotForm slot={edit.name ? edit : null} onClose={() => setEdit(null)} onDone={() => { setEdit(null); reload() }} />}
    {uses && <Modal title={`使用槽位 ${uses.name} 的意图（${uses.used_by.length}）`} onClose={() => setUses(null)} foot={<Btn onClick={() => setUses(null)}>关闭</Btn>}>{useTable(uses)}</Modal>}
    {blocked && <Modal title={`不能删除槽位 ${blocked.name}`} onClose={() => setBlocked(null)} foot={<Btn onClick={() => setBlocked(null)}>关闭</Btn>}>
      <div className="err">该槽位正被 {blocked.used_by.length} 个意图使用，请先在这些意图中移除该槽位后再删除。</div>{useTable(blocked)}</Modal>}
  </>
}

/* ---------------- 空间设置 ---------------- */
const FB: [string, string, string][] = [
  ['handoff_human', '转人工', '交给人工客服'],
  ['fixed_reply', '固定话术', '按配置的话术回复'],
  ['llm_free', '让 AI 自由发挥', '由 Bot 自行回答'],
]
const STRICT: [string, string][] = [['paraphrase', '按意思表达'], ['verbatim', '完全固定']]
function normFallback(r: any) {
  const fb = r.fallback || {}
  const mode = fb.mode || (r.final_action === 'decline' ? 'fixed_reply' : r.final_action === 'handoff_human' ? 'handoff_human' : 'llm_free')
  return {
    mode,
    message: fb.message || '',
    prompt: fb.prompt || '',
    repliesText: (fb.replies && fb.replies.length ? fb.replies : (r.final_message ? [r.final_message] : ['抱歉，这个问题我暂时处理不了。'])).join('\n'),
    reply_strictness: fb.reply_strictness || 'paraphrase',
  }
}
function RecognitionTab() {
  const { space, canEdit, reloadSpaces } = useSession()
  const [r, reload] = useAsync(() => api.get(`/spaces/${space.id}/recognition`), [space.id])
  const [f, setF] = useState<any>(null)
  const [err, setErr] = useState<string[]>()
  useEffect(() => { if (r) setF({ ...r, fallback: normFallback(r) }) }, [r])
  if (!r || !f) return <div className="loading">加载中…</div>
  const num = (k: string, lab: string, min: number, max: number, step: number, tip: string, id: string) => <tr><th style={{ width: 170 }}>{lab}</th><td>
    <input type="number" id={id} value={f[k]} min={min} max={max} step={step} style={{ width: 100 }} disabled={!canEdit}
      onChange={e => setF({ ...f, [k]: e.target.value })} /> <span className="muted">{tip}</span></td></tr>
  const th = parseFloat(f.threshold)
  const setFb = (k: string, v: any) => setF({ ...f, fallback: { ...f.fallback, [k]: v } })
  const save = async () => {
    const fb: any = { mode: f.fallback.mode }
    if (fb.mode === 'handoff_human' && f.fallback.message.trim()) fb.message = f.fallback.message.trim()
    if (fb.mode === 'fixed_reply') {
      fb.replies = f.fallback.repliesText.split('\n').map((x: string) => x.trim()).filter(Boolean)
      fb.reply_strictness = f.fallback.reply_strictness || 'paraphrase'
    }
    if (fb.mode === 'llm_free' && f.fallback.prompt.trim()) fb.prompt = f.fallback.prompt.trim()
    const body: any = { threshold: parseFloat(f.threshold), max_clarify: Number(f.max_clarify), max_slot_ask: Number(f.max_slot_ask),
      tool_retry: Number(f.tool_retry), clarify_prompt: f.clarify_prompt, fallback: fb }
    try {
      const res = await api.put(`/spaces/${space.id}/recognition`, body); setErr(undefined)
      toast(res.changed ? `空间设置已保存，配置版本 v${res.version}` : '没有改动'); reload(); reloadSpaces()
    } catch (e) { setErr(errLines(e)) }
  }
  const def = () => { setF({ ...f, threshold: 0.6, max_clarify: 2, max_slot_ask: 2, tool_retry: 1,
    clarify_prompt: '我想确认一下，您是想要以下哪一项？',
    fallback: { mode: space.id === 'cs' ? 'handoff_human' : 'llm_free', message: '', prompt: '', repliesText: '抱歉，这个问题我暂时处理不了。', reply_strictness: 'paraphrase' } }); toast('已填入默认值，点「保存」生效') }
  const faLabel = (r.fallback && r.fallback.mode_label) || FB.find(x => x[0] === (r.fallback?.mode))?.[1] || r.final_action_label
  return <>
    <div className="card" id="rcCard"><h3>识别与兜底<span className="sp" /><span className="muted" style={{ fontWeight: 'normal' }}>配置版本 v{r.version}{r.updated_at ? ` · 最近修改 ${fmt(r.updated_at, false)} ${r.updated_by || ''}` : ''}</span></h3>
      <table><tbody>
        {num('threshold', '① 识别阈值', 0, 1, 0.05, '0~1，默认 0.6。数值含义由 Bot 的识别模型决定，建议用单句测试和实际对话校准', 'rcTh')}
        <tr><th>② 未识别处理</th><td>Top1 置信度 &lt; 阈值时：列出不低于候选下限（阈值 ÷ 2 = <b id="rcCm">{isNaN(th) ? '—' : Math.round(th / 2 * 100) / 100}</b>）的最多 {r.max_candidates} 个候选让用户选；没有候选 → 执行空间兜底
          <div style={{ marginTop: 6 }}>澄清话术 <input type="text" id="rcCp" value={f.clarify_prompt} disabled={!canEdit} onChange={e => setF({ ...f, clarify_prompt: e.target.value })} style={{ width: 360 }} /></div></td></tr>
        {num('max_clarify', '③ 澄清最多次数', 0, 5, 1, '默认 2，超过 → 空间兜底', 'rcMc')}
        {num('max_slot_ask', '④ 槽位追问最多次数', 0, 5, 1, '每个必填槽位，默认 2，超过 → 空间兜底', 'rcMs')}
        <tr><th>⑤ 空间兜底</th><td>
          {FB.map(([v, l, tip]) => <label key={v} style={{ display: 'block', marginBottom: 4 }}>
            <input type="radio" name="rcFb" id={'rcFb_' + v} value={v} disabled={!canEdit} checked={f.fallback.mode === v} onChange={() => setFb('mode', v)} /> {l}
            <span className="muted"> — {v === 'llm_free' ? `由「${space.bot}」自行回答` : tip}</span></label>)}
          {f.fallback.mode === 'handoff_human' && <div className="f" style={{ marginTop: 6 }}><label>转人工说明（可选）</label>
            <input type="text" id="rcFbMsg" value={f.fallback.message} disabled={!canEdit} onChange={e => setFb('message', e.target.value)} style={{ width: 360 }} /></div>}
          {f.fallback.mode === 'fixed_reply' && <div style={{ marginTop: 6 }}>
            <div className="f"><label>话术（每行一条，至少 1 条）</label>
              <textarea id="rcFbReplies" rows={3} disabled={!canEdit} value={f.fallback.repliesText} onChange={e => setFb('repliesText', e.target.value)} style={{ width: 420 }} /></div>
            <div>{STRICT.map(([v, l]) => <label key={v} style={{ marginRight: 16 }}>
              <input type="radio" name="rcFbSt" id={'rcFbSt_' + v} value={v} disabled={!canEdit} checked={(f.fallback.reply_strictness || 'paraphrase') === v} onChange={() => setFb('reply_strictness', v)} /> {l}</label>)}
              <span className="muted">　按意思表达：可改写但须保持原意；完全固定：须原样输出</span></div>
          </div>}
          {f.fallback.mode === 'llm_free' && <div className="f" style={{ marginTop: 6 }}><label>发挥说明（可选）</label>
            <textarea id="rcFbPrompt" rows={2} disabled={!canEdit} value={f.fallback.prompt} onChange={e => setFb('prompt', e.target.value)} style={{ width: 420 }} /></div>}
        </td></tr>
        {num('tool_retry', '⑥ 工具调用失败重试', 0, 3, 1, '默认 1 次，仍失败 → 空间兜底', 'rcTr')}
      </tbody></table>
      {canEdit && <div style={{ textAlign: 'right', marginTop: 10 }}><Btn onClick={def}>恢复默认</Btn> <Btn p id="rcSave" onClick={save}>保存</Btn></div>}
      <ErrBox lines={err} />
    </div>
    <div className="card"><h3>Bot 运行流程</h3><div className="muted" style={{ lineHeight: 1.9 }}>识别 → 阈值判定（≥ 阈值命中该意图，按该意图的处理方式执行；否则澄清，最多 {r.max_clarify} 次）→ 若处理方式为「工具与知识流程」：槽位追问（每个必填槽位最多 {r.max_slot_ask} 次）→ 知识校验 → 调用工具（失败重试 {r.tool_retry} 次）。未命中、澄清/追问超限或工具失败时，执行空间兜底：<b>{faLabel}</b></div>
      <div className="muted">保存后配置版本 +1，Bot 拉取 runtime-config 中的 <span className="mono">recognition.fallback</span> 与每条意图的 <span className="mono">handling</span> 生效。</div></div>
    <TaxonomyCard />
  </>
}

function TaxonomyCard() {
  const { space, canEdit, reloadSpaces } = useSession()
  const [tax, reload] = useAsync<Tax>(() => api.get(`/spaces/${space.id}/taxonomy`), [space.id])
  const [names, setNames] = useState<string[]>([])
  const [pv, setPv] = useState<any>(null)
  const [adding, setAdding] = useState<{ parent: number | null; name: string; code: string } | null>(null)
  useEffect(() => { if (tax) setNames(tax.levels) }, [tax])
  if (!tax) return null
  const n = tax.category_levels
  const done = (msg: string) => { toast(msg); reload(); reloadSpaces() }
  const run = async (fn: () => Promise<any>, msg: string) => { try { await fn(); done(msg) } catch (e) { toast(errLines(e)[0]) } }
  const preview = async (change: any) => {
    try {
      const body = { change }
      setPv({ change, name: change.op === 'insert' ? '新层级' : '', r: await api.post(`/spaces/${space.id}/levels/preview`, body) })
    } catch (e) { toast(errLines(e)[0]) }
  }
  const apply = async () => {
    const { change, name } = pv
    const levels = change.op === 'insert' ? [...tax.levels.slice(0, change.index), name.trim(), ...tax.levels.slice(change.index)]
      : tax.levels.filter((_, i) => i !== change.index)
    try {
      const r = await api.put(`/spaces/${space.id}/levels`, { levels, change, confirm: true })
      setPv(null); done(`层级已调整，配置版本 v${r.version}`)
    } catch (e) { toast(errLines(e)[0]) }
  }
  const saveNames = () => run(() => api.put(`/spaces/${space.id}/levels`, { levels: names.map(x => x.trim()) }), '层级名称已保存')
  const addNode = () => adding && run(() => api.post(`/spaces/${space.id}/categories`, { parent_id: adding.parent, name: adding.name, code: adding.code || null }).then(() => setAdding(null)), '已新增分类')
  const rename = (c: CatNode) => { const v = window.prompt(`修改「${c.name}」的名称`, c.name); if (v && v.trim() !== c.name) run(() => api.patch(`/spaces/${space.id}/categories/${c.id}`, { name: v.trim() }), '已修改') }
  const del = (c: CatNode) => window.confirm(`删除分类「${c.name}」？`) && run(() => api.del(`/spaces/${space.id}/categories/${c.id}`), '已删除')
  const addForm = (parent: number | null, depth: number) => adding && adding.parent === parent && <span className="row" style={{ display: 'inline-flex', marginLeft: 6 }}>
    <input type="text" id="catNewName" autoFocus placeholder={`${tax.levels[depth - 1]}名称`} value={adding.name} onChange={e => setAdding({ ...adding, name: e.target.value })} style={{ width: 130 }} />
    {depth === 1 && <input type="text" id="catNewCode" placeholder="编码（大写，可选）" value={adding.code} onChange={e => setAdding({ ...adding, code: e.target.value.toUpperCase() })} style={{ width: 130 }} />}
    <Btn s p id="catNewOk" onClick={addNode}>添加</Btn><a onClick={() => setAdding(null)}>取消</a></span>
  const ops = (c: CatNode) => canEdit && <span className="catops">
    {c.depth < n && <a title={`新增${tax.levels[c.depth]}`} onClick={() => setAdding({ parent: c.id, name: '', code: '' })}>＋</a>}
    <a title="改名" onClick={() => rename(c)}>改名</a>
    {!c.count && !c.children.length && <a title="删除" onClick={() => del(c)}>✕</a>}</span>
  const node = (c: CatNode): any => c.depth === n
    ? <span key={c.id} className="chip">{c.name} <span className="muted">{c.count}</span>{ops(c)}</span>
    : <div key={c.id} className="catnode">
      <div className="catrow"><b>{c.name}</b> {c.code && <span className="mono muted">{c.code}</span>} <span className="muted">{c.count}</span>{ops(c)}{addForm(c.id, c.depth + 1)}</div>
      <div style={{ marginLeft: 18 }}>{c.children.map(node)}</div>
    </div>
  const r = pv?.r
  return <>
    <div className="card" id="lvCard"><h3>分类层级<span className="sp" /><span className="muted" style={{ fontWeight: 'normal' }}>最多 5 层分类 + 最末层意图；调整后配置版本 +1</span></h3>
      <table id="lvTbl"><tbody>
        {tax.levels.map((nm, i) => <tr key={i}>
          <th style={{ width: 110 }}>{i < n ? `第 ${i + 1} 层` : '最末层'}</th>
          <td><input type="text" id={'lvName' + i} value={names[i] ?? nm} disabled={!canEdit} maxLength={10} style={{ width: 140 }}
            onChange={e => setNames(names.map((x, j) => j === i ? e.target.value : x))} />
            {i === n && <span className="muted"> 意图挂在这一层，不能删除</span>}</td>
          <td style={{ width: 260, whiteSpace: 'nowrap' }}>{canEdit && <>
            {n < 5 && <Btn s id={'lvIns' + i} onClick={() => preview({ op: 'insert', index: i })}>在上方插入一层</Btn>}
            {i < n && <Btn s d id={'lvDel' + i} onClick={() => preview({ op: 'remove', index: i })}>删除这一层</Btn>}</>}</td></tr>)}
      </tbody></table>
      {canEdit && <div style={{ textAlign: 'right', marginTop: 8 }}><Btn p id="lvSave" disabled={names.join('|') === tax.levels.join('|')} onClick={saveNames}>保存名称</Btn></div>}
    </div>
    <div className="card" id="taxCard"><h3>分类（{tax.levels.slice(0, -1).join(' / ') || '无分类层'}）<span className="sp" /><span className="muted" style={{ fontWeight: 'normal' }}>数字为下属意图数；有下级或有意图的分类不能删除</span></h3>
      {n === 0 ? <div className="muted">本空间没有分类层，意图直接挂在空间下。可在上方「分类层级」里插入一层。</div> : <>
        {tax.tree.map(node)}
        {canEdit && <div className="row" style={{ marginTop: 8 }}>{adding?.parent === null ? addForm(null, 1)
          : <Btn s id="catAddRoot" onClick={() => setAdding({ parent: null, name: '', code: '' })}>＋ 新增{tax.levels[0]}</Btn>}</div>}</>}
    </div>
    {pv && <Modal title={pv.change.op === 'insert' ? '插入层级 · 影响预览' : '删除层级 · 影响预览'} testId="lvPreview" onClose={() => setPv(null)}
      foot={<><Btn onClick={() => setPv(null)}>取消</Btn><Btn p d={pv.change.op === 'remove'} id="lvConfirm" disabled={pv.change.op === 'insert' && !pv.name.trim()} onClick={apply}>确认调整</Btn></>}>
      {pv.change.op === 'insert' && <div className="f"><label>新层级名称</label><input type="text" id="lvNewName" maxLength={10} value={pv.name} onChange={e => setPv({ ...pv, name: e.target.value })} /></div>}
      <div className="note w" id="lvImpact">
        <div>层级：{r.levels_before.join(' / ')} → <b>{(pv.change.op === 'insert' ? [...tax.levels.slice(0, pv.change.index), pv.name || '新层级', ...tax.levels.slice(pv.change.index)] : r.levels_after).join(' / ')}</b></div>
        {pv.change.op === 'insert' ? <div>每个上级下新增一个「未分类」节点接住原有下级，共 <b>{r.created_uncategorized}</b> 个；之后可在分类里改名或调整。</div>
          : <div>这一层的分类会被删除，下级分类和意图并入上一层{r.merged.length ? <>；同名下级将合并：<b>{r.merged.map((m: any) => m.name).join('、')}</b></> : ''}。</div>}
        <div>分类节点 {r.categories_before} → {r.categories_after}；移动意图 <b>{r.intents_moved}</b> 个（其中已启用 {r.enabled_intents_moved} 个，Bot 拉到新版本后生效）。</div>
        {r.dropped_codes.length > 0 && <div>将不再使用的分类编码：<span className="mono">{r.dropped_codes.map((d: any) => d.code).join('、')}</span>（已有意图编码不变）</div>}
      </div>
      {r.samples.length > 0 && <table style={{ marginTop: 8 }}><thead><tr><th>意图</th><th>调整前</th><th>调整后</th></tr></thead><tbody>
        {r.samples.slice(0, 8).map((x: any) => <tr key={x.code}><td className="mono">{x.code}</td><td className="muted">{x.before}</td><td>{x.after}</td></tr>)}</tbody></table>}
      {r.samples.length > 8 && <div className="muted">… 共 {r.intents_moved} 个意图</div>}
    </Modal>}
  </>
}

/* ---------------- 导入记录 ---------------- */
function ImportLog() {
  const { space } = useSession()
  const [rs] = useAsync(() => api.get(`/spaces/${space.id}/imports`), [space.id])
  return <div className="card" style={{ padding: 0 }}><table id="imLog"><thead><tr><th>编号</th><th>文件 / 调用</th><th>来源</th><th>时间</th><th>操作人</th><th>结果</th></tr></thead>
    <tbody>{!rs ? <tr><td colSpan={6} className="loading">加载中…</td></tr> : rs.length ? rs.map((r: any) => <tr key={r.id}><td className="mono">{r.id}</td><td>{r.file}</td><td>{r.source}</td>
      <td className="muted">{fmt(r.at, false)}</td><td>{r.by}</td><td>{r.result}</td></tr>) : <tr><td colSpan={6} className="muted" style={{ textAlign: 'center', padding: 30 }}>暂无导入记录</td></tr>}</tbody></table></div>
}
