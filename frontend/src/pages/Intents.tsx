import { useEffect, useState } from 'react'
import { api, errLines, fmt, qs } from '../api'
import { useSession } from '../session'
import { Badge, Btn, ErrBox, Modal, Pager, ST_C, toast, useAsync } from '../ui'
import IntentEditor from './IntentEditor'
import ImportModal from './ImportModal'
import { SlotForm } from './Slots'
import { Tax, leaves, toCascade } from '../taxonomy'
import { Cascader } from '../picker'
import TaxonomyEditor from './TaxonomyEditor'

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
    {tab === 'cfg' && <SpaceSettings />}
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
      {tax?.tree.map(a => <div key={a.id} className={'tn l1' + (f.category === String(a.id) ? ' on' : '')} title={a.description || undefined} onClick={() => { setF({ ...f, category: String(a.id) }); setPage(1) }}>
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
          <td className="mono">{c.code}</td>{catCols.map((n, i) => <td key={n} className="muted" title={c.category_path[i]?.description || undefined}>{c.category_path[i]?.name || '—'}</td>)}
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
    {tax.category_levels > 0 && <div className="f"><label>所属分类（{tax.levels.slice(0, -1).join(' / ')}）<a id="niTax" onClick={goTax} style={{ fontWeight: 'normal', marginLeft: 8 }}>管理分类</a></label>
      <Cascader id="niP" value={p} tree={toCascade(tax.tree)} placeholder="请选择所属分类" onChange={setP} /></div>}
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
function SpaceSettings() {
  const { space, canEdit, reloadSpaces } = useSession()
  const [tax, reload] = useAsync<Tax>(() => api.get(`/spaces/${space.id}/taxonomy`), [space.id])
  const [names, setNames] = useState<string[]>([])
  const [pv, setPv] = useState<any>(null)
  const [info, setInfo] = useState({ name: space.name, description: space.description || '' })
  const [infoDirty, setInfoDirty] = useState(false)
  useEffect(() => { if (tax) setNames(tax.levels) }, [tax])
  useEffect(() => { setInfo({ name: space.name, description: space.description || '' }); setInfoDirty(false) }, [space.id, space.name, space.description])
  if (!tax) return null
  const n = tax.category_levels
  const done = (msg: string) => { toast(msg); reload(); reloadSpaces() }
  const run = async (fn: () => Promise<any>, msg: string) => { try { await fn(); done(msg) } catch (e) { toast(errLines(e)[0]) } }
  const saveInfo = () => run(async () => {
    await api.patch(`/spaces/${space.id}`, { name: info.name.trim(), description: info.description })
    setInfoDirty(false)
  }, '业务空间已保存')
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
  const r = pv?.r
  return <>
    <div className="card" id="spInfoCard"><h3>业务空间</h3>
      <table><tbody>
        <tr><th style={{ width: 110 }}>空间 ID</th><td className="mono">{space.id}</td></tr>
        <tr><th>编码前缀</th><td className="mono">{space.code_prefix}</td></tr>
        <tr><th>名称 *</th><td><input type="text" id="spInfoName" value={info.name} disabled={!canEdit} style={{ width: 320 }}
          onChange={e => { setInfo({ ...info, name: e.target.value }); setInfoDirty(true) }} /></td></tr>
        <tr><th>描述</th><td><textarea id="spInfoDesc" rows={3} maxLength={2000} disabled={!canEdit} value={info.description}
          placeholder="说明这个空间是做什么的，会出现在运行时配置里" style={{ width: 520, maxWidth: '100%' }}
          onChange={e => { setInfo({ ...info, description: e.target.value }); setInfoDirty(true) }} /></td></tr>
      </tbody></table>
      {canEdit && <div style={{ textAlign: 'right', marginTop: 8 }}><Btn p id="spInfoSave" disabled={!infoDirty || !info.name.trim()} onClick={saveInfo}>保存</Btn></div>}
      <div className="muted" style={{ marginTop: 6 }}>未命中任何意图时，由使用方智能体自行回答。若需要转人工或固定话术，请新建相应处理方式的意图（如「其他」「闲聊」）。</div>
    </div>
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
    <TaxonomyEditor tax={tax} reload={reload} />
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
