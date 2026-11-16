import { useState } from 'react'
import { api, download, errLines } from '../api'
import demo from '../demoContent.json'
import { useSession } from '../session'
import { Badge, Btn, ErrBox, Modal, toast } from '../ui'
import { SearchSelect } from '../picker'

const TC: Record<string, string> = { 新增: 'g', 更新: 'b', 覆盖: 'y', 重复: 'n', 冲突: 'r' }
type Sample = { file: string; src: string; text: string }

export default function ImportModal({ onClose, onDone }: { onClose: () => void; onDone: (status?: string) => void }) {
  const { space, reloadSpaces } = useSession()
  const [step, setStep] = useState(1)
  const [err, setErr] = useState<string[]>()
  const [pv, setPv] = useState<any>(null)
  const [ch, setCh] = useState<Record<number, { action: string; category_id?: number }>>({})
  const [result, setResult] = useState('')
  const [busy, setBusy] = useState(false)
  const samples: Record<string, Sample> = (space.id === 'mc' ? (demo as any).SAMPLES_MC : (demo as any).SAMPLES) || {}
  const tpl = (demo as any).IM_TPL as Record<string, string>
  const preview = async (files: File[], source: string) => {
    setErr(undefined); setBusy(true)
    const fd = new FormData(); files.forEach(f => fd.append('files', f)); fd.append('source', source)
    try {
      const r = await api.post(`/spaces/${space.id}/imports/preview`, fd)
      setPv(r); setCh({}); setStep(2)
    } catch (e) { setErr(errLines(e)) } finally { setBusy(false) }
  }
  const sample = (k: string) => { const s = samples[k]; preview([new File([s.text], s.file.replace(/\.xlsx?$/i, '.csv'), { type: 'text/plain' })], s.src) }
  const confirm = async () => {
    setBusy(true)
    try {
      const choices: Record<string, any> = {}
      pv.rows.forEach((r: any) => { const c = ch[r.index]; choices[r.index] = { action: c?.action || r.action, category_id: c?.category_id || (r.path_invalid ? r.path_options[0].id : undefined) } })
      const r = await api.post(`/spaces/${space.id}/imports/apply`, { file: pv.file, source: pv.source, items: pv.items, choices })
      setResult(r.result + (r.new_slots?.length ? `；自动新建文本槽位：${r.new_slots.join('、')}` : '')); setStep(3)
      toast('导入完成：' + r.result); reloadSpaces()
    } catch (e) { setErr(errLines(e)) } finally { setBusy(false) }
  }
  const back = () => { setStep(1); setPv(null); setErr(undefined) }
  const cnt: Record<string, number> = {}
  pv?.rows.forEach((r: any) => { cnt[r.type] = (cnt[r.type] || 0) + 1 })
  return <Modal wide testId="importModal" title="导入意图" onClose={() => { if (step === 3) onDone(); onClose() }} foot={step === 2 ? undefined : <Btn onClick={() => { if (step === 3) onDone(); onClose() }}>关闭</Btn>}>
    {step === 1 && <>
      <div className="note">支持 <b>JSON</b>（AI 工具产出，推荐）、<b>Excel/CSV</b>（表头：编码,名称,分类路径,意图描述,例句,槽位；分类路径从第一层写到最末层，用 / 分隔，每段填编码或名称；例句用 ｜ 分隔，槽位用 ； 分隔，本空间没有的槽位会自动新建）、<b>Markdown</b>（每个意图一个「## 编码 名称」小节）。导入到当前空间「{space.name}」，一律为「草稿」，关联知识/工具并通过校验后再启用。</div>
      <div className="drop"><input type="file" id="imFile" multiple accept=".json,.csv,.xlsx,.xls,.md,.txt" disabled={busy}
        onChange={e => { const fs = Array.from(e.target.files || []); if (fs.length) preview(fs, '文件上传'); e.target.value = '' }} />
        <div style={{ marginTop: 10 }}>或使用示例：{([['aiJson', 'AI 工具产出 JSON'], ['csv', 'Excel 模板'], ['md', 'Markdown']] as const).filter(x => samples[x[0]]).map(x =>
          <span key={x[0]}><Btn s id={'imSample_' + x[0]} onClick={() => sample(x[0])}>{x[1]}</Btn> </span>)}</div>
        <div style={{ marginTop: 8 }}>下载模板：{['csv', 'json', 'md'].map(k => <span key={k}><Btn s onClick={() => { download('意图导入模板.' + k, tpl[k], 'text/plain'); toast('已下载 意图导入模板.' + k) }}>意图导入模板.{k}</Btn> </span>)}</div>
      </div>
      {busy && <div className="loading">解析中…</div>}
      <div style={{ marginTop: 10 }}><ErrBox lines={err} /></div>
    </>}
    {step === 2 && pv && <>
      <div className="row" style={{ marginBottom: 8 }}><b>{pv.file}</b><Badge>{pv.source}</Badge><span className="sp" /><Btn s onClick={back}>重新上传</Btn></div>
      <div className="row" style={{ marginBottom: 8 }}>{Object.keys(cnt).map(t => <Badge key={t} c={TC[t]}>{t} {cnt[t]}</Badge>)}</div>
      <table id="imRows"><thead><tr><th>判定</th><th>编码</th><th>意图</th><th>分类路径</th><th>意图描述</th><th>例句</th><th>说明</th><th>处理方式</th></tr></thead><tbody>
        {pv.rows.map((r: any) => { const x = r.data; const c = ch[r.index] || {}; return <tr key={r.index}>
          <td><Badge c={TC[r.type]}>{r.type}</Badge></td><td className="mono">{x.code}</td><td>{x.name}</td>
          <td>{r.path_invalid && r.type !== '冲突' && r.type !== '重复' && r.path_options.length
            ? <SearchSelect id={'imCat' + r.index} value={String(c.category_id || r.path_options[0].id)}
                options={r.path_options.map((o: any) => ({ value: String(o.id), label: o.label }))}
                onChange={v => setCh({ ...ch, [r.index]: { ...c, action: c.action || r.action, category_id: Number(v) } })} />
            : (x.category_path || []).join(' / ')}</td>
          <td className="muted">{x.description}</td><td>{x.examples.length}</td>
          <td>{r.issues.length > 0 && <span style={{ color: '#b91c1c' }}>{r.issues.join('；')}</span>}
            {r.notes.length > 0 && <span style={{ color: '#b45309' }}>{r.notes.map((n: string, i: number) => <div key={i}>{n}</div>)}</span>}
            {r.diff.length > 0 && <span className="muted">与现有差异：{r.diff.join('、')}</span>}
            {x.ignored_fields?.length > 0 && <div className="muted">已忽略字段：{x.ignored_fields.join('、')}</div>}</td>
          <td><select id={'imA' + r.index} value={c.action || r.action} onChange={e => setCh({ ...ch, [r.index]: { ...c, action: e.target.value } })}>{r.actions.map((o: string) => <option key={o}>{o}</option>)}</select></td></tr> })}
      </tbody></table>
      <div className="muted" style={{ marginTop: 6 }}>合并：补充新例句/槽位，非空字段不覆盖；覆盖：用文件内容替换名称、意图描述、例句、槽位（已关联的知识与工具保留）。文件里的兜底/转人工条件一律忽略。未命中任何意图时，由使用方智能体自行回答。</div>
      <ErrBox lines={err} />
      <div style={{ textAlign: 'right', marginTop: 10 }}><Btn onClick={back}>取消</Btn> <Btn p id="imConfirm" disabled={busy} onClick={confirm}>确认入库</Btn></div>
    </>}
    {step === 3 && <>
      <div className="ok" id="imResult">导入完成：{result}</div>
      <div className="row"><Btn p id="imGo" onClick={() => { onDone('草稿'); onClose() }}>查看草稿意图</Btn><span className="muted">新导入的意图为草稿，关联知识/工具并通过校验后再启用</span> <Btn onClick={back}>继续导入</Btn></div>
    </>}
  </Modal>
}
