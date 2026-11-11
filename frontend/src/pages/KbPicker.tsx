import { useEffect, useState } from 'react'
import { api, qs } from '../api'
import { Btn, Modal, TAGK, TagBadges, useAsync } from '../ui'

type Ref = { kb_id: string; doc_id: string; chunk_id: string; usage: string; label?: string; valid?: boolean }
const key = (r: { kb_id: string; doc_id: string; chunk_id: string }) => `${r.kb_id}|${r.doc_id}|${r.chunk_id}`

export default function KbPicker({ code, selected, onClose, onOk }: { code: string; selected: Ref[]; onClose: () => void; onOk: (r: Ref[]) => void }) {
  const [kbs] = useAsync(() => api.get('/kbs'), [])
  const [kbId, setKbId] = useState('')
  const [doc, setDoc] = useState('')
  const [qIn, setQIn] = useState('')
  const [q, setQ] = useState('')
  const [tag, setTag] = useState<Record<string, string>>({})
  const [sel, setSel] = useState<Record<string, Ref>>(() => Object.fromEntries(selected.map(r => [key(r), r])))
  useEffect(() => { if (kbs?.length && !kbId) setKbId(kbs[0].kb_id) }, [kbs, kbId])
  const [detail] = useAsync(() => kbId ? api.get(`/kbs/${kbId}`) : Promise.resolve(null), [kbId])
  const [chunks] = useAsync(() => kbId ? api.get(`/kbs/${kbId}/chunks` + qs({ page_size: 500 })) : Promise.resolve(null), [kbId])
  if (!kbs) return <Modal title="关联知识" onClose={onClose}><div className="loading">加载中…</div></Modal>
  if (!kbs.length) return <Modal title="关联知识" onClose={onClose} foot={<Btn onClick={onClose}>关闭</Btn>}><div className="muted">平台还没有知识库，请先在「知识库」中新建。</div></Modal>
  const kb = kbs.find((k: any) => k.kb_id === kbId)
  const items: any[] = chunks?.items || []
  const tagVals: Record<string, string[]> = {}
  TAGK.forEach(([k]) => { tagVals[k] = Array.from(new Set(items.flatMap(c => c.effective_tags[k] || []))) })
  const hit = (eff: any) => TAGK.every(([k]) => !tag[k] || !(eff[k] || []).length || eff[k].includes(tag[k]))
  const docs = (detail?.documents || []).filter((d: any) => !doc || d.doc_id === doc)
  const toggle = (r: Ref, on: boolean) => setSel(s => { const n = { ...s }; if (on) n[key(r)] = s[key(r)] || r; else delete n[key(r)]; return n })
  const ok = () => onOk(Object.values(sel))
  return <Modal wide testId="kbPicker" title={`关联知识 · ${code}`} onClose={onClose} foot={<><Btn onClick={onClose}>取消</Btn> <Btn p id="pkOk" onClick={ok}>确定</Btn></>}>
    <div className="row" style={{ marginBottom: 8 }}>
      <select id="pkKb" value={kbId} onChange={e => { setKbId(e.target.value); setDoc(''); setQ(''); setQIn(''); setTag({}) }}>
        {kbs.map((k: any) => <option key={k.kb_id} value={k.kb_id}>{k.name}（{k.type}）</option>)}</select>
      <select id="pkDoc" value={doc} onChange={e => setDoc(e.target.value)}><option value="">全部文档（{detail?.documents.length ?? 0}）</option>
        {(detail?.documents || []).map((d: any) => <option key={d.doc_id} value={d.doc_id}>{d.name}</option>)}</select>
      <input type="text" id="pkQ" value={qIn} onChange={e => setQIn(e.target.value)} placeholder="搜索切片内容" onKeyDown={e => e.key === 'Enter' && setQ(qIn.trim())} />
      <Btn s onClick={() => setQ(qIn.trim())}>搜索</Btn><span className="sp" /><span className="muted">已选 {Object.keys(sel).length} 项</span>
    </div>
    <div className="row" style={{ marginBottom: 8 }}><span className="muted">按标签筛选</span>
      {TAGK.map(([k, l]) => <select key={k} value={tag[k] || ''} onChange={e => setTag({ ...tag, [k]: e.target.value })}><option value="">{l}：全部</option>
        {tagVals[k].map(v => <option key={v}>{v}</option>)}</select>)}</div>
    {!chunks ? <div className="loading">加载中…</div> : <table><thead><tr><th style={{ width: 30 }}></th><th>所属文档</th><th>知识切片</th><th>标签</th></tr></thead><tbody>
      {docs.map((d: any) => {
        const cs = items.filter(c => c.doc_id === d.doc_id && (!q || c.content.includes(q)) && hit(c.effective_tags))
        if (!cs.length) return null
        const whole: Ref = { kb_id: kbId, doc_id: d.doc_id, chunk_id: '*', usage: 'validate', label: `${kb.name} / ${d.name} / 整篇（${items.filter(c => c.doc_id === d.doc_id).length} 条）`, valid: true }
        return [<tr key={d.doc_id} style={{ background: '#f9fafb' }}><td><input type="checkbox" checked={!!sel[key(whole)]} onChange={e => toggle(whole, e.target.checked)} /></td>
          <td className="muted">{d.name}</td><td><b>整篇文档</b> <span className="muted">（{items.filter(c => c.doc_id === d.doc_id).length} 条，适合作为校验集）</span></td><td></td></tr>,
        ...cs.map(c => {
          const h0 = kb.headers?.[0]
          const title = c.cells ? String((h0 && c.cells[h0]) || Object.values(c.cells)[0] || '') : c.content
          const r: Ref = { kb_id: kbId, doc_id: d.doc_id, chunk_id: c.chunk_id, usage: 'reference', label: `${kb.name} / ${d.name} / #${c.order} ${title.slice(0, 18)}`, valid: true }
          return <tr key={c.chunk_id}><td><input type="checkbox" className="pkc" checked={!!sel[key(r)]} onChange={e => toggle(r, e.target.checked)} /></td>
            <td className="muted">{d.name}</td><td style={{ whiteSpace: 'pre-wrap' }}>{c.content.slice(0, 90)}</td><td><TagBadges tags={c.effective_tags} /></td></tr>
        })]
      })}</tbody></table>}
  </Modal>
}
