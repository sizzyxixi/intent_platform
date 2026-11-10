import { useEffect, useState } from 'react'
import { api, errLines, fmt } from '../api'
import demo from '../demoContent.json'
import { useSession } from '../session'
import { Badge, Btn, ErrBox, Modal, TAGK, TagBadges, splitT, toast, useAsync } from '../ui'

const TYPE_EXT = (demo as any).TYPE_EXT as Record<string, string[]>
const KB_SAMPLES = (demo as any).KB_SAMPLES as Record<string, [string, string | null][]>
const short = (t: string) => t === '表格知识库' || t === '表格' ? '表格' : '图文'
const stC: Record<string, string> = { 构建成功: 'g', 构建中: 'b', 构建失败: 'r' }
const StDot = ({ s }: { s: string }) => <span><span style={{ display: 'inline-block', width: 7, height: 7, borderRadius: 4, marginRight: 5, background: s === '构建成功' ? '#16a34a' : s === '构建失败' ? '#dc2626' : '#2563eb' }} />{s}</span>
const KbBadge = ({ s }: { s: string }) => <Badge c={stC[s] || 'n'}>{s}</Badge>
const emptyTags = () => Object.fromEntries(TAGK.map(([k]) => [k, [] as string[]]))
const refsText = (a: any[]) => a.map(x => `${x.code}${x.space_name ? '（' + x.space_name + '）' : ''}`).join('、')

export default function KbPage({ kbId }: { kbId: string }) {
  return kbId ? <KbDetail id={kbId} /> : <KbList />
}

function TagForm({ tags, setTags, inh }: { tags: Record<string, string[]>; setTags: (t: Record<string, string[]>) => void; inh?: Record<string, string[]> }) {
  const ph: Record<string, string> = { platform: 'TikTok,Shopee', site: 'US,VN', language: 'en,vi', category: '通用' }
  return <div className="f"><label>适用范围标签（多个值用逗号分隔{inh ? '；留空＝继承知识库标签' : ''}）</label><div className="grid g2">
    {TAGK.map(([k, l]) => <div className="row" key={k}><span className="muted" style={{ width: 36 }}>{l}</span>
      <input type="text" id={'tg_' + k} style={{ flex: 1 }} defaultValue={(tags[k] || []).join(',')}
        placeholder={inh ? '继承：' + ((inh[k] || []).join(',') || '不限') : '如 ' + ph[k]}
        onChange={e => setTags({ ...tags, [k]: splitT(e.target.value) })} /></div>)}</div></div>
}

function KbList() {
  const { canEdit, go } = useSession()
  const [qIn, setQIn] = useState('')
  const [q, setQ] = useState('')
  const [quick, setQuick] = useState<string[]>(() => JSON.parse(localStorage.getItem('kbQuick') || '[]'))
  const [sort, setSort] = useState<[string, number]>(['updated_at', -1])
  const [kbs, reload] = useAsync(() => api.get('/kbs' + (q ? '?name=' + encodeURIComponent(q) : '')), [q])
  const [cfg, setCfg] = useState(false)
  const [create, setCreate] = useState(false)
  const [edit, setEdit] = useState<any>(null)
  const list = (kbs || []).slice().sort((a: any, b: any) => (a[sort[0]] < b[sort[0]] ? -1 : a[sort[0]] > b[sort[0]] ? 1 : 0) * sort[1])
  const ar = (f: string) => sort[0] === f ? (sort[1] < 0 ? ' ↓' : ' ↑') : ' ↕'
  const srt = (f: string) => setSort(sort[0] === f ? [f, -sort[1]] : [f, -1])
  const saveQuick = () => {
    const v = qIn.trim(); if (!v) { toast('请先输入知识库名称'); return }
    const n = quick.includes(v) ? quick : [...quick, v]; setQuick(n); localStorage.setItem('kbQuick', JSON.stringify(n)); setQ(v); toast('已保存为快捷查询')
  }
  const del = async (k: any) => {
    const imp = await api.get(`/kbs/${k.kb_id}/impact`)
    const msg = imp.affected.length ? `该知识库被 ${imp.affected.length} 个意图关联（${refsText(imp.affected)}），删除后关联将失效。确认删除？` : `确认删除知识库「${k.name}」？`
    if (!window.confirm(msg)) return
    try { const r = await api.del(`/kbs/${k.kb_id}`); reload(); toast('已删除' + (r.downgraded?.length ? `；${r.downgraded.length} 个启用意图转为草稿` : '')) } catch (e) { toast(errLines(e)[0]) }
  }
  return <>
    <div className="row" style={{ alignItems: 'flex-start' }}><div><h2>AI知识库</h2><p className="desc">AI知识库是检索增强生成（RAG）服务，支持全模态数据：富文本、表格、图片等，导入后自动解析构建为知识切片，供意图引用与检索。知识库全平台通用，不区分业务空间，任何空间的意图都可以关联；用场景标签（平台 / 站点 / 语言 / 类目）区分适用范围。</p></div><span className="sp" /><Btn id="kbSetting" onClick={() => setCfg(true)}>设置</Btn></div>
    <div className="card"><div className="row"><label className="muted" style={{ fontSize: 13, color: '#4b5563' }}>知识库名称</label>
      <input type="text" id="kq" value={qIn} onChange={e => setQIn(e.target.value)} onKeyDown={e => e.key === 'Enter' && setQ(qIn.trim())} placeholder="请输入" style={{ width: 240 }} />
      <Btn p onClick={() => setQ(qIn.trim())}>查询</Btn><Btn onClick={() => { setQIn(''); setQ('') }}>重置</Btn><Btn onClick={saveQuick}>另存为快捷查询</Btn></div>
      {quick.length > 0 && <div style={{ marginTop: 8 }}><span className="muted">快捷查询：</span>{quick.map(x => <a key={x} className="chip" onClick={() => { setQIn(x); setQ(x) }}>{x}</a>)}</div>}</div>
    <div className="card">{canEdit && <div style={{ marginBottom: 10 }}><Btn p id="kbNew" onClick={() => setCreate(true)}>＋新建</Btn></div>}
      <table id="kbTbl"><thead><tr><th>序号</th><th>知识名称</th><th>知识库类型</th><th>状态</th><th>关联意图</th><th><a onClick={() => srt('updated_at')}>最新更新时间{ar('updated_at')}</a></th><th><a onClick={() => srt('created_at')}>创建时间{ar('created_at')}</a></th><th>操作</th></tr></thead><tbody>
        {!kbs ? <tr><td colSpan={8} className="loading">加载中…</td></tr> : list.length ? list.map((k: any, i: number) => <tr key={k.kb_id}>
          <td>{i + 1}</td><td>{k.name}<TagBadges tags={k.tags} /></td><td>{k.type}</td><td><StDot s={k.status} /></td><td>{k.linked_count || <span className="muted">—</span>}</td>
          <td>{fmt(k.updated_at)}</td><td>{fmt(k.created_at)}</td>
          <td style={{ whiteSpace: 'nowrap' }}><a className="lk" onClick={() => go('#/kb/' + k.kb_id)}>查看</a>{canEdit && <><a className="lk" onClick={() => setEdit(k)}>编辑</a><a className="lk" onClick={() => del(k)}>删除</a></>}</td></tr>)
          : <tr><td colSpan={8} className="muted" style={{ textAlign: 'center', padding: 30 }}>暂无数据</td></tr>}
      </tbody></table></div>
    {cfg && <SettingsModal onClose={() => setCfg(false)} />}
    {create && <KiModal onClose={() => setCreate(false)} onDone={id => { setCreate(false); go('#/kb/' + id) }} />}
    {edit && <EditKb kb={edit} onClose={() => setEdit(null)} onDone={() => { setEdit(null); reload() }} />}
  </>
}

function SettingsModal({ onClose }: { onClose: () => void }) {
  const { canEdit } = useSession()
  const [v] = useAsync(() => api.get('/kb-settings'), [])
  const [k, setK] = useState(''); const [t, setT] = useState(''); const [err, setErr] = useState<string[]>()
  useEffect(() => { if (v) { setK(String(v.top_k)); setT(String(v.threshold)) } }, [v])
  const ok = async () => { try { await api.put('/kb-settings', { top_k: +k, threshold: +t }); toast('设置已保存'); onClose() } catch (e) { setErr(errLines(e)) } }
  return <Modal title="设置" onClose={onClose} foot={<><Btn onClick={onClose}>取消</Btn> {canEdit && <Btn p id="cfOk" onClick={ok}>确定</Btn>}</>}>
    <div className="f"><label>检索返回切片数（TopK，1～20）</label><input type="number" id="cfK" value={k} onChange={e => setK(e.target.value)} /></div>
    <div className="f"><label>相似度阈值（0～1）</label><input type="number" step="0.05" id="cfT" value={t} onChange={e => setT(e.target.value)} /></div>
    <div className="muted">对知识库检索（控制台与开放接口未显式传参时）全局生效。</div><ErrBox lines={err} />
  </Modal>
}

function EditKb({ kb, onClose, onDone }: { kb: any; onClose: () => void; onDone: () => void }) {
  const [name, setName] = useState(kb.name); const [tags, setTags] = useState<Record<string, string[]>>(kb.tags || emptyTags()); const [err, setErr] = useState<string[]>()
  const ok = async () => {
    if (!name.trim()) { setErr(['请输入知识库名称']); return }
    try { await api.patch(`/kbs/${kb.kb_id}`, { name: name.trim(), tags }); toast('已保存'); onDone() } catch (e) { setErr(errLines(e)) }
  }
  return <Modal title="编辑知识库" onClose={onClose} foot={<><Btn onClick={onClose}>取消</Btn> <Btn p id="keOk" onClick={ok}>确定</Btn></>}>
    <div className="f"><label>* 知识库名称</label><input type="text" id="keN" value={name} onChange={e => setName(e.target.value)} /></div>
    <div className="f"><label>知识库类型</label><input type="text" value={kb.type} disabled /></div>
    <TagForm tags={tags} setTags={setTags} /><ErrBox lines={err} />
  </Modal>
}

/** 新建知识库 / 导入文档（同一套导入逻辑；覆盖同名文档需二次确认） */
function KiModal({ kb, onClose, onDone }: { kb?: any; onClose: () => void; onDone: (id: string) => void }) {
  const [name, setName] = useState('')
  const [type, setType] = useState(kb ? short(kb.type) : '图文')
  const [tags, setTags] = useState<Record<string, string[]>>(emptyTags())
  const [files, setFiles] = useState<File[]>([])
  const [err, setErr] = useState<string[]>()
  const [over, setOver] = useState<any>(null)
  const [ack, setAck] = useState(false)
  const [busy, setBusy] = useState(false)
  const [createdId, setCreatedId] = useState(kb?.kb_id || '')
  const ext = TYPE_EXT[type]
  const pick = (fs: File[]) => { setFiles(fs); setOver(null); setAck(false); setErr(undefined) }
  const ok = async () => {
    const e: string[] = []
    if (!kb && !createdId && !name.trim()) e.push('请输入知识库名称')
    if (!files.length) e.push('请上传文件')
    if (over && !ack) e.push('覆盖会影响已关联意图，请勾选确认')
    if (e.length) { setErr(e); return }
    setBusy(true)
    try {
      let id = createdId
      if (!id) { const k = await api.post('/kbs', { name: name.trim(), type: type === '表格' ? '表格知识库' : '图文知识库-富文本', tags }); id = k.kb_id; setCreatedId(id) }
      const fd = new FormData(); files.forEach(f => fd.append('files', f)); fd.append('overwrite', over ? 'true' : 'false')
      await api.post(`/kbs/${id}/documents:import`, fd)
      toast('已提交导入，正在构建…'); onDone(id)
    } catch (x: any) {
      if (x.code === 'OVERWRITE_REQUIRED') { setOver(x.details); setErr(undefined) } else setErr(errLines(x))
    } finally { setBusy(false) }
  }
  const title = kb ? `导入文档 · ${kb.name}` : '创建' + (type === '表格' ? '表格知识库' : '图文知识库')
  return <Modal wide={false} testId="kiModal" title={title} onClose={onClose} foot={<><Btn onClick={onClose}>取消</Btn> <Btn p id="kiOk" disabled={busy} onClick={ok}>{over ? '确认覆盖并导入' : '确定，并导入'}</Btn></>}>
    {!kb && <>
      <div className="f"><label><span style={{ color: '#dc2626' }}>*</span> 知识库名称</label><input type="text" id="kiN" value={name} disabled={!!createdId} onChange={e => setName(e.target.value)} placeholder="请输入" /></div>
      <div className="f"><label><span style={{ color: '#dc2626' }}>*</span> 导入知识库</label><div className="grid g2">
        {([['表格', '表格知识库', '列表格式的数据，比如问答对、用户数据等结构清晰、规范化的文档'], ['图文', '图文知识库', '支持图文文档内容数据，如图片、文档等，内容丰富、版面复杂的内容']] as const).map(x =>
          <div key={x[0]} id={'kiType_' + x[0]} onClick={() => { if (!createdId && type !== x[0]) { setType(x[0]); pick([]) } }} style={{ cursor: 'pointer', border: '1px solid ' + (type === x[0] ? '#2563eb' : '#e5e7eb'), background: type === x[0] ? '#eff6ff' : '#fff', borderRadius: 8, padding: '10px 12px' }}>
            <b>{x[1]}</b><div className="muted">{x[2]}</div><div className="muted">支持 {TYPE_EXT[x[0]].map(e => '.' + e).join(' ')}</div></div>)}</div></div>
      <TagForm tags={tags} setTags={setTags} />
    </>}
    {kb && <div className="muted" style={{ marginBottom: 8 }}>目标知识库：<b>{kb.name}</b>（{kb.type}）{kb.headers?.length ? '　表头：' + kb.headers.join(' | ') : ''}</div>}
    <div className="f"><label><span style={{ color: '#dc2626' }}>*</span> 导入文件</label>
      <input type="file" id="kiF" multiple accept={ext.map(e => '.' + e).join(',')} onChange={e => pick(Array.from(e.target.files || []))} /> <span className="muted">或使用示例：</span>
      {(KB_SAMPLES[type] || []).filter(s => s[1] !== null && /\.(csv|json|jsonl|md|txt|html?)$/i.test(s[0])).map((s, i) => <span key={i}><Btn s onClick={() => pick([new File([s[1] as string], s[0], { type: 'text/plain' })])}>{s[0]}</Btn> </span>)}
      <div className="muted" style={{ marginTop: 6 }}>支持扩展名：{ext.map(e => '.' + e).join(' ')}{type === '表格' ? '，请保证表头完全一致！' : ''}<br />上传后在后台解析构建，文件较大时需要等待。<br />若导入的文档与已有文档名称或内容相同，将覆盖并更新原文档（需确认影响）。</div></div>
    {files.length > 0 && <table><thead><tr><th>待导入文件</th><th>大小</th></tr></thead><tbody>{files.map(f => <tr key={f.name}><td>{f.name}</td><td className="muted">{f.size} 字节</td></tr>)}</tbody></table>}
    {over && <div className="note w" style={{ marginTop: 8 }}>⚠ 与已有文档同名或同内容：{over.documents.join('、')}。{over.affected?.length ? <>覆盖后以下已关联意图的切片将不再存在，关联会被移除（已启用意图若因此校验不通过会转为草稿）：<br />
      {over.affected.map((a: any, i: number) => <div key={i}><span className="mono">{a.code}</span> {a.name}（{a.status}）← {a.doc} #{a.order}</div>)}</> : '覆盖不影响已关联意图。'}
      <div style={{ marginTop: 6 }}><label><input type="checkbox" id="kiAck" checked={ack} onChange={e => setAck(e.target.checked)} /> 我已知晓影响，确认覆盖</label></div></div>}
    <ErrBox lines={err} />
  </Modal>
}

function KbDetail({ id }: { id: string }) {
  const { canEdit, go } = useSession()
  const [tab, setTab] = useState('docs')
  const [k, reload, err] = useAsync(() => api.get(`/kbs/${id}`), [id])
  const [imp, setImp] = useState(false)
  const [chunkDoc, setChunkDoc] = useState('')
  useEffect(() => {
    if (!k) return
    if (k.status === '构建中' || k.documents.some((d: any) => d.status === '构建中')) {
      const t = setTimeout(reload, 1500); return () => clearTimeout(t)
    }
  }, [k, reload])
  if (err) return <div className="err">{errLines(err)[0]} <a onClick={() => go('#/kb')}>返回列表</a></div>
  if (!k) return <div className="loading">加载中…</div>
  return <>
    <div style={{ marginBottom: 8 }}><a onClick={() => go('#/kb')}>← 返回知识库列表</a></div>
    <div className="card"><div className="row"><span style={{ fontSize: 26 }}>{short(k.type) === '表格' ? '▦' : '📄'}</span><div>
      <div className="row"><b style={{ fontSize: 16 }} id="kbName">{k.name}</b><KbBadge s={k.status} /></div>
      <div className="muted">更新时间：{fmt(k.updated_at)}　　知识库类型：{k.type}　　标签：{TAGK.some(([t]) => (k.tags[t] || []).length) ? <TagBadges tags={k.tags} /> : '不限'}　　关联意图：{k.linked.length}</div></div></div></div>
    <div className="card"><div className="tabs">{[['docs', '导入文档'], ['chunks', '知识库详情'], ['search', '知识库检索']].map(([t, l]) =>
      <div key={t} className={tab === t ? 'on' : ''} onClick={() => setTab(t)}>{l}</div>)}</div>
      {tab === 'docs' && <DocsTab k={k} reload={reload} onImport={() => setImp(true)} toChunks={d => { setChunkDoc(d); setTab('chunks') }} canEdit={canEdit} />}
      {tab === 'chunks' && <ChunksTab k={k} doc={chunkDoc} setDoc={setChunkDoc} reload={reload} />}
      {tab === 'search' && <SearchTab k={k} />}
    </div>
    {imp && <KiModal kb={k} onClose={() => setImp(false)} onDone={() => { setImp(false); setTab('docs'); reload() }} />}
  </>
}

function DocsTab({ k, reload, onImport, toChunks, canEdit }: { k: any; reload: () => void; onImport: () => void; toChunks: (d: string) => void; canEdit: boolean }) {
  const [q, setQ] = useState('')
  const docs = k.documents.filter((d: any) => !q || d.name.includes(q))
  const del = async (d: any) => {
    const imp = await api.get(`/kbs/${k.kb_id}/impact?doc_id=${encodeURIComponent(d.doc_id)}`)
    if (!window.confirm(imp.affected.length ? `文档「${d.name}」的切片被 ${imp.affected.length} 个意图关联（${refsText(imp.affected)}），删除后关联将失效。确认删除？` : `确认删除文档「${d.name}」？`)) return
    try { const r = await api.del(`/kbs/${k.kb_id}/documents/${d.doc_id}`); reload(); toast('已删除' + (r.downgraded?.length ? `；${r.downgraded.length} 个启用意图转为草稿` : '')) } catch (e) { toast(errLines(e)[0]) }
  }
  return <>
    <div className="row" style={{ marginBottom: 10 }}>{canEdit && <Btn p id="kbImportOpen" onClick={onImport}>导入文档</Btn>}<span className="sp" /><input type="text" id="kdq" value={q} onChange={e => setQ(e.target.value)} placeholder="请输入文档名称" style={{ width: 220 }} /></div>
    <table id="docTbl"><thead><tr><th>序号</th><th>文档名称</th><th>构建状态</th><th>数据量</th><th>上传时间</th><th>更新时间</th><th>操作</th></tr></thead><tbody>
      {docs.length ? docs.map((d: any, i: number) => <tr key={d.doc_id}><td>{i + 1}</td><td>{d.name}</td><td><KbBadge s={d.status} />{d.error && <div className="muted" style={{ color: '#b91c1c' }}>{d.error}</div>}</td><td>{d.data_size}</td>
        <td>{fmt(d.uploaded_at)}</td><td>{fmt(d.updated_at)}</td><td style={{ whiteSpace: 'nowrap' }}><a className="lk" onClick={() => toChunks(d.doc_id)}>切片详情</a>{canEdit && <a className="lk" onClick={() => del(d)}>删除</a>}</td></tr>)
        : <tr><td colSpan={7} className="muted" style={{ textAlign: 'center', padding: 30 }}>暂无数据</td></tr>}
    </tbody></table></>
}

function ChunksTab({ k, doc, setDoc, reload }: { k: any; doc: string; setDoc: (d: string) => void; reload: () => void }) {
  const [page, setPage] = useState(1)
  const [data, reloadC] = useAsync(() => api.get(`/kbs/${k.kb_id}/chunks?page_size=100&page=${page}` + (doc ? '&doc_id=' + encodeURIComponent(doc) : '')), [k.kb_id, doc, page, k.updated_at])
  const [view, setView] = useState<any>(null)
  const pages = data ? Math.max(1, Math.ceil(data.total / 100)) : 1
  return <>
    <div className="row" style={{ marginBottom: 10 }}><select id="kcd" value={doc} onChange={e => { setDoc(e.target.value); setPage(1) }}><option value="">全部文档</option>
      {k.documents.map((d: any) => <option key={d.doc_id} value={d.doc_id}>{d.name}</option>)}</select><span className="muted">共 {data?.total ?? '…'} 个切片</span></div>
    <table id="chunkTbl"><thead><tr><th>序号</th><th>所属文档</th><th>知识切片</th><th>关联意图</th><th>更新时间</th><th>操作</th></tr></thead><tbody>
      {!data ? <tr><td colSpan={6} className="loading">加载中…</td></tr> : data.items.length ? data.items.map((c: any, i: number) => <tr key={c.chunk_id}>
        <td>{(page - 1) * 100 + i + 1}</td><td className="muted">{c.doc_name}</td>
        <td style={{ whiteSpace: 'pre-wrap', maxWidth: 420 }}>{c.content.slice(0, 120)}{TAGK.some(([t]) => (c.tags[t] || []).length) && <div><TagBadges tags={c.tags} /></div>}</td>
        <td>{c.linked.length ? c.linked.map((x: any) => <div key={x.code}><span className="mono">{x.code}</span> <span className="muted">{x.space_name}</span></div>) : <span className="muted">—</span>}</td>
        <td className="muted">{fmt(c.updated_at)}</td><td><a className="lk" onClick={() => setView(c)}>查看</a></td></tr>)
        : <tr><td colSpan={6} className="muted" style={{ textAlign: 'center', padding: 30 }}>暂无数据</td></tr>}
    </tbody></table>
    {pages > 1 && <div className="pager"><Btn s disabled={page <= 1} onClick={() => setPage(page - 1)}>上一页</Btn><span className="muted">{page} / {pages}</span><Btn s disabled={page >= pages} onClick={() => setPage(page + 1)}>下一页</Btn></div>}
    {view && <ChunkModal k={k} c={view} onClose={() => setView(null)} onSaved={() => { setView(null); reloadC(); reload() }} />}
  </>
}

function ChunkModal({ k, c, onClose, onSaved }: { k: any; c: any; onClose: () => void; onSaved: () => void }) {
  const { canEdit } = useSession()
  const heads: string[] = c.cells ? (k.headers?.length ? k.headers : Object.keys(c.cells)) : []
  const [cells, setCells] = useState<Record<string, string>>(c.cells || {})
  const [content, setContent] = useState(c.content)
  const [tags, setTags] = useState<Record<string, string[]>>(c.tags || emptyTags())
  const [err, setErr] = useState<string[]>()
  const save = async () => {
    if (!c.cells && !content.trim()) { setErr(['切片内容不能为空']); return }
    try { await api.put(`/kbs/${k.kb_id}/chunks/${c.chunk_id}`, c.cells ? { cells, tags } : { content, tags }); toast('切片已保存'); onSaved() } catch (e) { setErr(errLines(e)) }
  }
  return <Modal title="查看切片" testId="chunkModal" onClose={onClose} foot={<><Btn onClick={onClose}>关闭</Btn> {canEdit && <Btn p id="ccSave" onClick={save}>保存修改</Btn>}</>}>
    <div className="muted" style={{ marginBottom: 8 }}>所属文档：{c.doc_name}　·　切片 #{c.order}　·　更新时间：{fmt(c.updated_at)}</div>
    <div style={{ marginBottom: 8 }}>关联意图：{c.linked.length ? c.linked.map((x: any) => `${x.code} ${x.name}`).join('、') : <span className="muted">暂无</span>}</div>
    {c.cells ? heads.map((h, j) => <div className="f" key={h}><label>{h}</label><textarea id={'cc' + j} style={{ minHeight: 44 }} value={cells[h] || ''} disabled={!canEdit} onChange={e => setCells({ ...cells, [h]: e.target.value })} /></div>)
      : <div className="f"><label>知识切片</label><textarea id="cc" style={{ minHeight: 160 }} value={content} disabled={!canEdit} onChange={e => setContent(e.target.value)} /></div>}
    <TagForm tags={tags} setTags={setTags} inh={k.tags} /><ErrBox lines={err} />
  </Modal>
}

function SearchTab({ k }: { k: any }) {
  const [q, setQ] = useState('')
  const [tf, setTf] = useState<Record<string, string>>({})
  const [res, setRes] = useState<any[] | null>(null)
  const [cfg] = useAsync(() => api.get('/kb-settings'), [])
  const [vals] = useAsync(() => api.get(`/kbs/${k.kb_id}/chunks?page_size=500`), [k.kb_id])
  const tagVals = (t: string) => Array.from(new Set([...(k.tags[t] || []), ...((vals?.items || []).flatMap((c: any) => c.effective_tags[t] || []))]))
  const run = async (f = tf) => {
    if (!q.trim()) { toast('请输入问题'); return }
    try { setRes(await api.post(`/kbs/${k.kb_id}/search`, { query: q.trim(), ...f })) } catch (e) { toast(errLines(e)[0]) }
  }
  return <>
    <div className="row" style={{ marginBottom: 10 }}><input type="text" id="ksq" value={q} onChange={e => setQ(e.target.value)} onKeyDown={e => e.key === 'Enter' && run()} placeholder="请输入问题，如：有现货吗 / 主图背景" style={{ flex: 1 }} /><Btn p id="kbSearch" onClick={() => run()}>检索</Btn></div>
    <div className="row" style={{ marginBottom: 10 }}><span className="muted">按标签筛选</span>{TAGK.map(([t, l]) => <select key={t} id={'ksT_' + t} value={tf[t] || ''} onChange={e => { const n = { ...tf, [t]: e.target.value }; setTf(n); if (q.trim()) run(n) }}>
      <option value="">{l}：全部</option>{tagVals(t).map(v => <option key={v}>{v}</option>)}</select>)}</div>
    {res && (res.length ? <><table id="ksRes"><thead><tr><th>排名</th><th>相似度</th><th>所属文档</th><th>命中切片</th><th>标签</th></tr></thead><tbody>
      {res.map((r: any, i: number) => <tr key={r.chunk.chunk_id}><td>{i + 1}</td><td>{r.score.toFixed(2)}</td><td className="muted">{r.doc_name}</td><td style={{ whiteSpace: 'pre-wrap' }}>{r.chunk.content}</td><td><TagBadges tags={r.chunk.effective_tags} /></td></tr>)}</tbody></table>
      <div className="muted" style={{ marginTop: 6 }}>检索后端：PostgreSQL 全文检索 + pg_trgm 相似度；TopK={cfg?.top_k}，阈值={cfg?.threshold}（列表页右上角「设置」可调）</div></>
      : <div className="muted">未命中切片（相似度低于阈值 {cfg?.threshold}）</div>)}
  </>
}
