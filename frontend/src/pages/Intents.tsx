import { useEffect, useState } from 'react'
import { api, errLines, fmt, qs } from '../api'
import { useSession } from '../session'
import { Badge, Btn, ErrBox, Modal, Pager, ST_C, toast, useAsync } from '../ui'
import IntentEditor from './IntentEditor'
import ImportModal from './ImportModal'
import { SlotForm } from './Slots'

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
  const [f, setF] = useState({ l1: '', q: '', status: '' })
  const [qIn, setQIn] = useState('')
  const [stIn, setStIn] = useState('')
  const [page, setPage] = useState(1)
  const [n, setN] = useState(0)
  const [newOpen, setNewOpen] = useState(false)
  const [imOpen, setImOpen] = useState(false)
  const size = 100
  const [tax] = useAsync(() => api.get(`/spaces/${space.id}/taxonomy`), [space.id, ver, n])
  const [data] = useAsync(() => api.get(`/spaces/${space.id}/intents` + qs({ ...f, page, page_size: size })),
    [space.id, f.l1, f.q, f.status, page, ver, n])
  const reload = () => { setN(x => x + 1); reloadSpaces() }
  const quick = async (code: string, status: string) => {
    try {
      await api.post(`/intents/${code}/status`, { status }); toast(`${code} 已${status}`); reload()
    } catch (e: any) {
      if (e.code === 'ENABLE_CHECK_FAILED') { toast('启用前校验未通过，请补全'); onEdit(code) } else toast(errLines(e)[0])
    }
  }
  const l1name = (c: string) => tax?.l1.find((a: any) => a.code === c)?.name || c
  return <div className="split">
    <div className="tree" id="l1Tree">
      <div className={'tn l1' + (!f.l1 ? ' on' : '')} onClick={() => { setF({ ...f, l1: '' }); setPage(1) }}><span>全部</span><span className="muted">{data?.all_count ?? ''}</span></div>
      {tax?.l1.map((a: any) => <div key={a.code} className={'tn l1' + (f.l1 === a.code ? ' on' : '')} onClick={() => { setF({ ...f, l1: a.code }); setPage(1) }}>
        <span>{a.name}</span><span className="muted">{data?.l1_counts?.[a.code] || 0}</span></div>)}
    </div>
    <div>
      <div className="card"><div className="row">
        <input type="text" id="fQ" placeholder="搜索编码 / 名称 / 例句" value={qIn} onChange={e => setQIn(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') { setF({ ...f, q: qIn, status: stIn }); setPage(1) } }} style={{ width: 240 }} />
        <select id="fSt" value={stIn} onChange={e => setStIn(e.target.value)}><option value="">全部状态</option>{['草稿', '启用', '停用'].map(s => <option key={s}>{s}</option>)}</select>
        <Btn id="fApply" onClick={() => { setF({ ...f, q: qIn, status: stIn }); setPage(1) }}>筛选</Btn>
        <span className="sp" />
        {canEdit && <Btn id="imOpen" onClick={() => setImOpen(true)}>导入意图</Btn>}
        {canEdit && <Btn p id="newIntent" onClick={() => setNewOpen(true)}>＋ 新建意图</Btn>}
      </div></div>
      <div className="card" style={{ padding: 0 }}><table id="intentTbl"><thead><tr><th>编码</th><th>L1 大类</th><th>L2 问题桶</th><th>L3 意图</th><th>例句</th><th>知识</th><th>工具</th><th>状态</th><th>操作</th></tr></thead>
        <tbody>{!data ? <tr><td colSpan={9} className="loading">加载中…</td></tr> : data.items.length ? data.items.map((c: any) => <tr key={c.code}>
          <td className="mono">{c.code}</td><td className="muted">{l1name(c.l1)}</td><td className="muted">{c.l2}</td>
          <td><a onClick={() => onEdit(c.code)}>{c.name}</a><div className="muted">{(c.description || '').slice(0, 34)}</div></td>
          <td>{c.example_count}</td><td>{c.kb_count || <span className="muted">0</span>}</td>
          <td>{c.tools.length ? c.tools.map((t: string) => <div key={t} className="mono">{t}</div>) : <span className="muted">0</span>}</td>
          <td><Badge c={ST_C[c.status]}>{c.status}</Badge></td>
          <td style={{ whiteSpace: 'nowrap' }}><Btn s onClick={() => onEdit(c.code)}>{canEdit ? '编辑' : '查看'}</Btn>
            {canEdit && (c.status === '启用' ? <Btn s onClick={() => quick(c.code, '停用')}>停用</Btn> : <Btn s onClick={() => quick(c.code, '启用')}>启用</Btn>)}</td>
        </tr>) : <tr><td colSpan={9} className="muted" style={{ textAlign: 'center', padding: 30 }}>没有符合条件的意图</td></tr>}</tbody></table>
        {data && <Pager page={page} total={data.total} size={size} onPage={setPage} />}
      </div>
    </div>
    {newOpen && tax && <NewIntent tax={tax} l1={f.l1} onClose={() => setNewOpen(false)} onDone={code => { setNewOpen(false); reload(); onEdit(code) }} />}
    {imOpen && <ImportModal onClose={() => setImOpen(false)} onDone={(st) => { setImOpen(false); reload(); if (st) { setF({ l1: '', q: '', status: st }); setStIn(st) } }} />}
  </div>
}

function NewIntent({ tax, l1, onClose, onDone }: { tax: any; l1: string; onClose: () => void; onDone: (c: string) => void }) {
  const { space } = useSession()
  const opts = tax.l1.flatMap((a: any) => a.l2.map((b: any) => ({ v: a.code + '|' + b.name, label: `${a.name} / ${b.name}`, l1: a.code })))
  const [p, setP] = useState(opts.find((o: any) => o.l1 === l1)?.v || opts[0]?.v || '')
  const [code, setCode] = useState('')
  const [name, setName] = useState('')
  const [err, setErr] = useState<string[]>()
  const eg = `${space.code_prefix}.${(p.split('|')[0] || 'GEN')}.XXX`
  const ok = async () => {
    const [a, b] = p.split('|')
    try { const r = await api.post(`/spaces/${space.id}/intents`, { code: code.trim().toUpperCase(), name: name.trim(), l1: a, l2: b }); onDone(r.code) }
    catch (e) { setErr(errLines(e)) }
  }
  return <Modal title="新建意图" onClose={onClose} foot={<><Btn onClick={onClose}>取消</Btn><Btn p id="newIntentDo" onClick={ok}>创建草稿并编辑</Btn></>}>
    <div className="muted" style={{ marginBottom: 8 }}>空间：{space.name}</div>
    <div className="f"><label>所属 L1 大类 / L2 问题桶</label><select id="niP" value={p} onChange={e => setP(e.target.value)}>{opts.map((o: any) => <option key={o.v} value={o.v}>{o.label}</option>)}</select></div>
    <div className="f"><label>编码（唯一，如 {eg}）</label><input type="text" id="niC" value={code} onChange={e => setCode(e.target.value)} placeholder="前缀.大类.意图" /></div>
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
const FA: [string, string][] = [['handoff_human', '转人工'], ['general_assistant', '交给通用助手'], ['decline', '告知无法处理']]
function RecognitionTab() {
  const { space, canEdit, reloadSpaces } = useSession()
  const [r, reload] = useAsync(() => api.get(`/spaces/${space.id}/recognition`), [space.id])
  const [f, setF] = useState<any>(null)
  const [err, setErr] = useState<string[]>()
  useEffect(() => { if (r) setF({ ...r, final_message: r.final_message || '抱歉，这个问题我暂时处理不了。' }) }, [r])
  if (!r || !f) return <div className="loading">加载中…</div>
  const num = (k: string, lab: string, min: number, max: number, step: number, tip: string, id: string) => <tr><th style={{ width: 170 }}>{lab}</th><td>
    <input type="number" id={id} value={f[k]} min={min} max={max} step={step} style={{ width: 100 }} disabled={!canEdit}
      onChange={e => setF({ ...f, [k]: e.target.value })} /> <span className="muted">{tip}</span></td></tr>
  const th = parseFloat(f.threshold)
  const save = async () => {
    const body: any = { threshold: parseFloat(f.threshold), max_clarify: Number(f.max_clarify), max_slot_ask: Number(f.max_slot_ask),
      tool_retry: Number(f.tool_retry), final_action: f.final_action, clarify_prompt: f.clarify_prompt }
    if (f.final_action === 'decline') body.final_message = f.final_message
    try {
      const res = await api.put(`/spaces/${space.id}/recognition`, body); setErr(undefined)
      toast(res.changed ? `空间设置已保存，配置版本 v${res.version}` : '没有改动'); reload(); reloadSpaces()
    } catch (e) { setErr(errLines(e)) }
  }
  const def = () => { setF({ ...f, threshold: 0.6, max_clarify: 2, max_slot_ask: 2, tool_retry: 1, final_action: space.id === 'cs' ? 'handoff_human' : 'general_assistant', clarify_prompt: '我想确认一下，您是想要以下哪一项？' }); toast('已填入默认值，点「保存」生效') }
  const faLabel = FA.find(x => x[0] === r.final_action)?.[1]
  return <>
    <div className="card" id="rcCard"><h3>识别与兜底<span className="sp" /><span className="muted" style={{ fontWeight: 'normal' }}>配置版本 v{r.version}{r.updated_at ? ` · 最近修改 ${fmt(r.updated_at, false)} ${r.updated_by || ''}` : ''}</span></h3>
      <table><tbody>
        {num('threshold', '① 识别阈值', 0, 1, 0.05, '0~1，默认 0.6。数值含义由 Bot 的识别模型决定，建议用单句测试和实际对话校准', 'rcTh')}
        <tr><th>② 未识别处理</th><td>Top1 置信度 &lt; 阈值时：列出不低于候选下限（阈值 ÷ 2 = <b id="rcCm">{isNaN(th) ? '—' : Math.round(th / 2 * 100) / 100}</b>）的最多 {r.max_candidates} 个候选让用户选；没有候选 → 执行最终动作
          <div style={{ marginTop: 6 }}>澄清话术 <input type="text" id="rcCp" value={f.clarify_prompt} disabled={!canEdit} onChange={e => setF({ ...f, clarify_prompt: e.target.value })} style={{ width: 360 }} /></div></td></tr>
        {num('max_clarify', '③ 澄清最多次数', 0, 5, 1, '默认 2，超过 → 最终动作', 'rcMc')}
        {num('max_slot_ask', '④ 槽位追问最多次数', 0, 5, 1, '每个必填槽位，默认 2，超过 → 最终动作', 'rcMs')}
        <tr><th>⑤ 最终动作</th><td><select id="rcFa" value={f.final_action} disabled={!canEdit} onChange={e => setF({ ...f, final_action: e.target.value })}>{FA.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
          {f.final_action === 'decline' && <span id="rcFmW"> 回复 <input type="text" id="rcFm" value={f.final_message} onChange={e => setF({ ...f, final_message: e.target.value })} style={{ width: 300 }} /></span>}
          {' '}<span className="muted">转人工：交给人工客服；交给通用助手：由「{space.bot}」的通用对话能力接手；告知无法处理：回复固定话术</span></td></tr>
        {num('tool_retry', '⑥ 工具调用失败重试', 0, 3, 1, '默认 1 次，仍失败 → 最终动作', 'rcTr')}
      </tbody></table>
      {canEdit && <div style={{ textAlign: 'right', marginTop: 10 }}><Btn onClick={def}>恢复默认</Btn> <Btn p id="rcSave" onClick={save}>保存</Btn></div>}
      <ErrBox lines={err} />
    </div>
    <div className="card"><h3>Bot 运行流程</h3><div className="muted" style={{ lineHeight: 1.9 }}>识别 → 阈值判定（≥ 阈值命中；否则澄清，最多 {r.max_clarify} 次）→ 槽位追问（每个必填槽位最多 {r.max_slot_ask} 次）→ 知识校验 → 调用工具（失败重试 {r.tool_retry} 次）→ 最终动作：<b>{faLabel}</b>（任一环节超限或失败时）</div>
      <div className="muted">保存后配置版本 +1，Bot 拉取 runtime-config 中的 <span className="mono">recognition</span> 生效。</div></div>
    <TaxonomyCard />
  </>
}

function TaxonomyCard() {
  const { space, canEdit } = useSession()
  const [tax, reload] = useAsync(() => api.get(`/spaces/${space.id}/taxonomy`), [space.id])
  const [l1, setL1] = useState({ code: '', name: '' })
  const [l2, setL2] = useState<Record<string, string>>({})
  const run = async (fn: () => Promise<any>, msg: string) => { try { await fn(); toast(msg); reload() } catch (e) { toast(errLines(e)[0]) } }
  if (!tax) return null
  return <div className="card" id="taxCard"><h3>意图分类（L1 大类 / L2 问题桶）<span className="sp" /><span className="muted" style={{ fontWeight: 'normal' }}>被意图使用的分类不能删除</span></h3>
    <table><thead><tr><th style={{ width: 200 }}>L1 大类</th><th>L2 问题桶</th>{canEdit && <th style={{ width: 260 }}>新增 L2</th>}</tr></thead><tbody>
      {tax.l1.map((a: any) => <tr key={a.code}><td><b>{a.name}</b> <span className="mono muted">{a.code}</span> <span className="muted">{a.count}</span>
        {canEdit && !a.count && <a style={{ marginLeft: 6 }} onClick={() => window.confirm(`删除 L1「${a.name}」？`) && run(() => api.del(`/spaces/${space.id}/taxonomy/l1/${a.code}`), '已删除')}>删除</a>}</td>
        <td>{a.l2.map((b: any) => <span key={b.name} className="chip">{b.name} <span className="muted">{b.count}</span>
          {canEdit && !b.count && <a onClick={() => run(() => api.del(`/spaces/${space.id}/taxonomy/l1/${a.code}/l2/${encodeURIComponent(b.name)}`), '已删除')}>✕</a>}</span>)}</td>
        {canEdit && <td><input type="text" value={l2[a.code] || ''} onChange={e => setL2({ ...l2, [a.code]: e.target.value })} placeholder="问题桶名称" style={{ width: 150 }} />
          <Btn s onClick={() => run(() => api.post(`/spaces/${space.id}/taxonomy/l1/${a.code}/l2`, { name: l2[a.code] || '' }).then(() => setL2({ ...l2, [a.code]: '' })), '已新增 L2')}>添加</Btn></td>}
      </tr>)}</tbody></table>
    {canEdit && <div className="row" style={{ marginTop: 8 }}><span className="muted">新增 L1</span>
      <input type="text" placeholder="编码（大写，如 PAY）" value={l1.code} onChange={e => setL1({ ...l1, code: e.target.value.toUpperCase() })} style={{ width: 160 }} />
      <input type="text" placeholder="名称" value={l1.name} onChange={e => setL1({ ...l1, name: e.target.value })} style={{ width: 160 }} />
      <Btn s onClick={() => run(() => api.post(`/spaces/${space.id}/taxonomy/l1`, l1).then(() => setL1({ code: '', name: '' })), '已新增 L1')}>添加</Btn></div>}
  </div>
}

/* ---------------- 导入记录 ---------------- */
function ImportLog() {
  const { space } = useSession()
  const [rs] = useAsync(() => api.get(`/spaces/${space.id}/imports`), [space.id])
  return <div className="card" style={{ padding: 0 }}><table id="imLog"><thead><tr><th>编号</th><th>文件 / 调用</th><th>来源</th><th>时间</th><th>操作人</th><th>结果</th></tr></thead>
    <tbody>{!rs ? <tr><td colSpan={6} className="loading">加载中…</td></tr> : rs.length ? rs.map((r: any) => <tr key={r.id}><td className="mono">{r.id}</td><td>{r.file}</td><td>{r.source}</td>
      <td className="muted">{fmt(r.at, false)}</td><td>{r.by}</td><td>{r.result}</td></tr>) : <tr><td colSpan={6} className="muted" style={{ textAlign: 'center', padding: 30 }}>暂无导入记录</td></tr>}</tbody></table></div>
}
