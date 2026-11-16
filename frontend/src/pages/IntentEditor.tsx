import { useEffect, useRef, useState, useCallback } from 'react'
import { api, errLines, fmt, qs } from '../api'
import { useSession } from '../session'
import { Badge, Btn, Drawer, ErrBox, ST_C, toast, useAsync } from '../ui'
import KbPicker from './KbPicker'
import { SlotForm } from './Slots'
import { Tax, leaves, toCascade } from '../taxonomy'
import { Cascader, SearchSelect } from '../picker'
import { shortPackName } from '../packLabel'
import { McpPicker } from './McpServers'
import { TypeTag, isFileParam, pTypeLabel } from './ToolEdit'

type Slot = { name: string; required: boolean; ask: string }
type Ref = { kb_id: string; doc_id: string; chunk_id: string; usage: string; label?: string; valid?: boolean }
type Bind = { tool: string; param_map: Record<string, { source: string; value: string }> }
type Handling = { mode: string; message?: string; repliesText?: string; reply_strictness?: string; prompt?: string }
const HMODES: [string, string, string][] = [
  ['tool_flow', '工具与知识流程', '按槽位追问，查知识、调用绑定的工具'],
  ['handoff_human', '转人工', '把对话交给人工处理，不必绑工具或知识'],
  ['fixed_reply', '固定话术', '按配置的话术回复，不必绑工具或知识'],
  ['llm_free', '让 AI 自由发挥', '不调工具、不查知识，由 AI 自行回答'],
]
const STRICT: [string, string, string][] = [
  ['paraphrase', '按意思表达', '可以改写措辞，但必须保持原意'],
  ['verbatim', '完全固定', '必须原样输出其中一条话术'],
]
const CTX = ['session_id', 'buyer_id', 'order_id', 'sku']
const FILE_SLOT = ['文件', '图片']
const PICK_TYPES: [string, string][] = [['http', 'API 接口'], ['mcp', 'MCP'], ['cli', 'CLI 命令'], ['skill', '技能'], ['workflow', '工作流'], ['builtin', '平台内置']]

export default function IntentEditor({ code, onClose, onSaved }: { code: string; onClose: () => void; onSaved: () => void }) {
  const { space, canEdit, reloadSpaces } = useSession()
  const [d, setD] = useState<any>(null)
  const [E, setE] = useState<any>(null)
  const [errs, setErrs] = useState<string[]>()
  const [pick, setPick] = useState(false)
  const [slotNew, setSlotNew] = useState(false)
  const [toolSel, setToolSel] = useState('')
  const [pickTy0, setPickTy] = useState(() => sessionStorage.getItem('pickToolType') || '')
  const [mcpPick, setMcpPick] = useState<string | null>(null)
  const [cliGrp, setCliGrp] = useState('')
  const body = useRef<HTMLDivElement>(null)
  const [loadErr, setLoadErr] = useState<string[]>()
  const [slots, reloadSlots, slotsErr] = useAsync(() => api.get(`/spaces/${space.id}/slots`), [space.id])
  const [tax, , taxErr] = useAsync<Tax>(() => api.get(`/spaces/${space.id}/taxonomy`), [space.id])
  const [extraTools, setExtraTools] = useState<any[]>([])
  useEffect(() => { setToolSel('') }, [pickTy0, cliGrp])
  const boundKey = E ? (E.tools || []).map((b: Bind) => b.tool).join(',') : ''
  const [boundResp, , boundErr] = useAsync(
    () => !E ? Promise.resolve({ items: [] as any[], names: '' }) : boundKey
      ? api.get('/tools' + qs({ names: boundKey })).then((r: any) => ({ ...r, names: boundKey }))
      : Promise.resolve({ items: [] as any[], names: '' }),
    [boundKey, !!E])
  const [facetsResp] = useAsync(() => api.get('/tools' + qs({ page: 1, page_size: 1, maintenance: false })), [])
  const [mcpServers] = useAsync(() => api.get('/mcp-servers'), [])
  const packOf = cliGrp.includes('|') ? cliGrp.split('|')[0] : cliGrp
  const groupOf = cliGrp.includes('|') ? cliGrp.slice(cliGrp.indexOf('|') + 1) : ''
  useEffect(() => {
    setLoadErr(undefined); setD(null); setE(null)
    api.get(`/intents/${code}`).then(x => {
      setD(x)
      const h = x.handling || { mode: 'tool_flow' }
      setE({ name: x.name, category_id: x.category_id, description: x.description || '', ex: (x.examples || []).join('\n'), status: x.status,
        slots: (x.slots || []).map((s: any) => ({ name: s.name, required: s.required, ask: s.ask })),
        kb_refs: x.kb_refs || [], tools: x.tools || [],
        handling: { mode: h.mode || 'tool_flow', message: h.message || '', prompt: h.prompt || '',
          repliesText: (h.replies || []).join('\n'), reply_strictness: h.reply_strictness || 'paraphrase' } })
      if (x.problems?.length) setErrs(Array.isArray(x.problems) ? x.problems : [String(x.problems)])
    }).catch(e => { const lines = errLines(e); setLoadErr(lines); toast(lines.join('；')) })
  // eslint-disable-next-line
  }, [code])
  const requiredErrs = [
    ...(loadErr || []),
    ...(slotsErr ? errLines(slotsErr) : []),
    ...(taxErr ? errLines(taxErr) : []),
    ...(boundErr ? errLines(boundErr) : []),
  ]
  if (requiredErrs.length) {
    return <Drawer testId="intentEditor" title="加载失败" onClose={onClose}>
      <div id="intentLoadErr" className="loading"><ErrBox lines={requiredErrs} title="无法加载意图详情：" /></div>
    </Drawer>
  }
  // 工具目录的 facets / MCP 服务列表只用于「关联工具」下拉，失败或未返回时不应挡住详情。
  const boundPending = !!E && !!boundKey && boundResp?.names !== boundKey
  if (!d || !E || !slots || !tax || boundPending) return <Drawer testId="intentEditor" title="加载中…" onClose={onClose}><div className="loading">加载中…</div></Drawer>
  const set = (k: string, v: any) => setE((e: any) => ({ ...e, [k]: v }))
  const opts = leaves(tax)
  const curRoot = opts.find(o => o.id === d.category_id)?.root
  const cur = opts.find(o => o.id === E.category_id)
  const lvNames = tax.levels
  const facets = facetsResp?.facets || {}
  const catalog: any[] = [...(boundResp.items || []), ...extraTools,
    ...(Array.isArray(mcpServers) ? mcpServers : []).flatMap((v: any) => v.tools || [])]
  const toolOf = (name: string) => catalog.find((t: any) => t.name === name)
  const slotDef = (name: string) => slots.find((s: any) => s.name === name)
  const handlingPayload = () => {
    const h = E.handling as Handling
    const out: any = { mode: h.mode }
    if (h.mode === 'handoff_human' && (h.message || '').trim()) out.message = h.message!.trim()
    if (h.mode === 'fixed_reply') {
      out.replies = (h.repliesText || '').split('\n').map((x: string) => x.trim()).filter(Boolean)
      out.reply_strictness = h.reply_strictness || 'paraphrase'
    }
    if (h.mode === 'llm_free' && (h.prompt || '').trim()) out.prompt = h.prompt!.trim()
    return out
  }
  const payload = () => ({ name: E.name.trim(), category_id: E.category_id, description: E.description.trim(),
    examples: E.ex.split('\n').map((x: string) => x.trim()).filter(Boolean), slots: E.slots,
    kb_refs: E.kb_refs.map((r: Ref) => ({ kb_id: r.kb_id, doc_id: r.doc_id, chunk_id: r.chunk_id, usage: r.usage })),
    tools: E.tools, handling: handlingPayload(), status: E.status })
  const setH = (k: string, v: any) => set('handling', { ...E.handling, [k]: v })
  const changeMode = (mode: string) => {
    if (mode === E.handling.mode) return
    const label = HMODES.find(x => x[0] === mode)?.[1] || mode
    if (mode !== 'tool_flow' && (E.tools.length || E.kb_refs.length)) {
      const n = E.tools.length, m = E.kb_refs.length
      if (!window.confirm(`改成「${label}」会解绑已关联的 ${n} 个工具和 ${m} 项知识，确定吗？`)) return
      setE((e: any) => ({ ...e, tools: [], kb_refs: [], handling: { ...e.handling, mode } }))
      return
    }
    setH('mode', mode)
  }
  const showErrs = (l: string[]) => { setErrs(l); if (body.current) body.current.scrollTop = 0 }
  const fail = (e: any) => { const lines = errLines(e); showErrs(lines); toast(lines.join('；')) }
  const check = async () => {
    try {
      const r = await api.post(`/intents/${code}/validate`, payload())
      if (r.problems.length) showErrs(r.problems); else { setErrs(undefined); toast('校验通过，可以启用') }
    } catch (e) { fail(e) }
  }
  const save = async () => {
    try {
      const r = await api.put(`/intents/${code}`, payload())
      setErrs(undefined)
      toast('已保存' + (r.warnings?.length ? '（提示：' + r.warnings.join('；') + '）' : ''))
      onSaved(); reloadSpaces(); onClose()
    } catch (e: any) { fail(e) }
  }
  const bindOf = (t: any): Bind => {
    const map: Bind['param_map'] = {}
    if (t.type !== 'skill') (t.params || []).forEach((p: any) => {
      const s = E.slots.find((x: Slot) => x.name === p.name && (!isFileParam(p) || FILE_SLOT.includes(slotDef(x.name)?.type)))
      map[p.name] = s ? { source: '槽位', value: s.name } : p.name === 'session_id' ? { source: '上下文', value: 'session_id' } : { source: '槽位', value: '' }
    })
    return { tool: t.name, param_map: map }
  }
  const servers: any[] = Array.isArray(mcpServers) ? mcpServers : []
  const bound = new Set<string>(E.tools.map((b: Bind) => b.tool))
  const typeCounts: Record<string, number> = {
    http: facets.by_type?.http || 0, cli: facets.by_type?.cli || 0, skill: facets.by_type?.skill || 0,
    workflow: facets.by_type?.workflow || 0, builtin: facets.builtin_bindable || 0,
    mcp: servers.filter((v: any) => (v.tools || []).some((x: any) => x.enabled && !x.remote_missing && !bound.has(x.name))).length,
  }
  const pickTy = pickTy0
  const addTool = () => {
    if (!toolSel) { toast('请先选择工具'); return }
    if (toolSel.startsWith('srv:')) { setMcpPick(toolSel.slice(4)); return }
    const t = toolOf(toolSel)
    if (!t) { toast('未找到该工具，请重新选择'); return }
    setExtraTools(xs => xs.some(x => x.name === t.name) ? xs : [...xs, t])
    set('tools', [...E.tools, bindOf(t)]); setToolSel(''); toast(t.type === 'skill' ? `已关联技能 ${toolSel}` : `已关联 ${toolSel}，请确认入参映射`)
  }
  const setMap = (ti: number, p: string, src: string, v: string) => {
    const tl = E.tools.map((b: Bind, i: number) => i !== ti ? b : { ...b, param_map: { ...b.param_map, [p]: { source: src, value: v } } })
    set('tools', tl)
  }
  const ro = !canEdit
  const exCount = E.ex.split('\n').filter((x: string) => x.trim()).length
  const foot = <>
    {errs?.length ? <div id="eSaveErr" style={{ textAlign: 'left', margin: '0 0 8px' }}><ErrBox lines={errs} title={`校验未通过（${errs.length} 项）：`} /></div> : null}
    <span className="muted" style={{ float: 'left', lineHeight: '28px' }}>状态 <select id="eSt" value={E.status} disabled={ro} onChange={e => set('status', e.target.value)}>
      {['草稿', '启用', '停用'].map(s => <option key={s}>{s}</option>)}</select>　设为「启用」时会做必填校验</span>
    <Btn onClick={onClose}>取消</Btn> <Btn id="eCheck" onClick={check}>校验</Btn> {canEdit && <Btn p id="eSave" onClick={save}>保存</Btn>}
  </>
  return <>
    <Drawer testId="intentEditor" onClose={onClose} foot={foot}
      title={<>编辑意图 · <span className="mono">{d.code}</span> <Badge c={ST_C[d.status]}>{d.status}</Badge></>}>
      <div ref={body}>
        <ErrBox lines={errs} title={errs?.length ? `校验未通过（${errs.length} 项）：` : undefined} />
        <div className="sec"><h4 style={{ margin: '0 0 8px' }}>① 基本信息</h4>
          <div className="lvpath" id="eLv">归属：{(cur?.names || []).map((nm, i) => <span key={i}><span className="lv">{lvNames[i]}</span>{nm}{cur?.descs?.[i] ? <span className="muted" title={cur.descs[i]}> {cur.descs[i]}</span> : null} <span className="muted">/</span> </span>)}<span className="lv">{lvNames[lvNames.length - 1]}</span><b>{E.name || '（未命名）'}</b></div>
          <div className="grid g2"><div className="f"><label>{lvNames[lvNames.length - 1]}名称 *</label><input type="text" id="eN" value={E.name} disabled={ro} onChange={e => set('name', e.target.value)} /></div>
            <div className="f"><label>编码 *</label><input type="text" id="eC" value={d.code} disabled /></div></div>
          {tax.category_levels > 0 && <div className="f"><label>所属分类（{lvNames.slice(0, -1).join(' / ')}）{curRoot?.code ? <span className="muted">　编码依赖第一层编码 {curRoot.code}，只能在「{curRoot.name}」内移动</span> : null}</label>
            <Cascader id="eCat" value={E.category_id != null ? String(E.category_id) : ''} disabled={ro}
              placeholder="请选择所属分类"
              tree={toCascade(curRoot?.code ? tax.tree.filter(r => r.id === curRoot.id) : tax.tree)}
              onChange={v => set('category_id', v ? Number(v) : null)} /></div>}
          <div className="f"><label>意图描述 *</label><textarea id="eD" style={{ minHeight: 56 }} value={E.description} disabled={ro} onChange={e => set('description', e.target.value)} />
            <div className="muted" style={{ marginTop: 3 }}>这段描述会被大模型分类和语义匹配使用。请写清：用户想做什么；什么情况算这个意图；什么情况不算（容易和哪个意图混淆）。<br />示例：用户想为商品生成符合平台规范的主图；"做主图""首图"算；只要换白底不算（→ 白底图）。</div></div>
          <div className="f"><label>例句 *（每行一条，至少 3 条，当前 {exCount} 条）</label><textarea id="eEx" style={{ minHeight: 90 }} value={E.ex} disabled={ro} onChange={e => set('ex', e.target.value)} /></div>
        </div>

        <div className="sec" id="eHandling"><h4 style={{ margin: '0 0 8px' }}>② 处理方式</h4>
          <div className="muted" style={{ marginBottom: 8 }}>闲聊、其他等不需要查知识或调工具的意图，请选择转人工、固定话术或让 AI 自由发挥。</div>
          {HMODES.map(([v, l, tip]) => <label key={v} style={{ display: 'block', marginBottom: 6 }}>
            <input type="radio" name="eHMode" id={'eH_' + v} value={v} disabled={ro} checked={E.handling.mode === v} onChange={() => changeMode(v)} /> {l}
            <span className="muted"> — {tip}</span></label>)}
          {E.handling.mode === 'handoff_human' && <div className="f"><label>转人工说明（可选）</label>
            <textarea id="eHMsg" rows={2} maxLength={2000} disabled={ro} value={E.handling.message} onChange={e => setH('message', e.target.value)} placeholder="可选，告诉使用方转人工时要说的话" /></div>}
          {E.handling.mode === 'fixed_reply' && <>
            <div className="f"><label>话术（每行一条，至少 1 条；多条时由使用方选一条）</label>
              <textarea id="eHReplies" rows={3} disabled={ro} value={E.handling.repliesText} onChange={e => setH('repliesText', e.target.value)} placeholder="例如：您好，这个问题我先帮您转给人工客服。" /></div>
            <div className="f"><label>表达要求</label>
              {STRICT.map(([v, l, tip]) => <label key={v} style={{ display: 'inline-block', marginRight: 16 }}>
                <input type="radio" name="eHStrict" id={'eHS_' + v} value={v} disabled={ro} checked={(E.handling.reply_strictness || 'paraphrase') === v} onChange={() => setH('reply_strictness', v)} /> {l}
                <span className="muted"> — {tip}</span></label>)}</div></>}
          {E.handling.mode === 'llm_free' && <div className="f"><label>发挥说明（可选）</label>
            <textarea id="eHPrompt" rows={3} maxLength={2000} disabled={ro} value={E.handling.prompt} onChange={e => setH('prompt', e.target.value)} placeholder="可选，给 AI 的发挥边界或语气要求" /></div>}
        </div>

        <div className="sec"><h4 style={{ margin: '0 0 8px' }}>③ 槽位（从本空间槽位列表选择；处理前需要拿到的信息）</h4>
          <table><thead><tr><th>槽位</th><th>必填</th><th>缺信息时追问（留空＝继承槽位默认话术）</th><th></th></tr></thead><tbody>
            {E.slots.map((s: Slot, i: number) => { const sd = slotDef(s.name); return <tr key={i}>
              <td><select id={'sN' + i} value={s.name} disabled={ro} onChange={e => set('slots', E.slots.map((x: Slot, j: number) => j === i ? { ...x, name: e.target.value } : x))}>
                <option value="">— 选择槽位 —</option>{slots.map((x: any) => <option key={x.name} value={x.name}>{x.display_name}（{x.name}）</option>)}
                {s.name && !sd && <option value={s.name}>{s.name}（本空间不存在）</option>}</select></td>
              <td><select id={'sR' + i} value={s.required ? '必填' : '选填'} disabled={ro} onChange={e => set('slots', E.slots.map((x: Slot, j: number) => j === i ? { ...x, required: e.target.value === '必填' } : x))}><option>必填</option><option>选填</option></select></td>
              <td><input type="text" id={'sA' + i} value={s.ask} disabled={ro} placeholder={sd ? '继承：' + sd.default_ask : ''} style={{ width: '100%' }}
                onChange={e => set('slots', E.slots.map((x: Slot, j: number) => j === i ? { ...x, ask: e.target.value } : x))} /></td>
              <td>{canEdit && <a onClick={() => set('slots', E.slots.filter((_: any, j: number) => j !== i))}>删除</a>}</td></tr> })}
          </tbody></table>
          {canEdit && <div style={{ marginTop: 6 }}><Btn s id="slotAdd" onClick={() => set('slots', [...E.slots, { name: '', required: false, ask: '' }])}>＋ 添加槽位</Btn> <Btn s id="slotNewInline" onClick={() => setSlotNew(true)}>＋ 新建槽位</Btn></div>}
        </div>

        {E.handling.mode === 'tool_flow' ? <><div className="sec"><h4 style={{ margin: '0 0 8px' }}>④ 关联知识（参考：检索后用于回答/生成；校验：生成后比对，硬约束）</h4>
          {E.kb_refs.length ? <table id="eRefs"><tbody>{E.kb_refs.map((r: Ref, i: number) => <tr key={r.kb_id + r.doc_id + r.chunk_id}>
            <td>{r.valid === false ? <span style={{ color: '#b91c1c' }}>{r.label}</span> : r.label}</td>
            <td style={{ width: 110 }}><select value={r.usage} disabled={ro} onChange={e => set('kb_refs', E.kb_refs.map((x: Ref, j: number) => j === i ? { ...x, usage: e.target.value } : x))}>
              <option value="reference">参考</option><option value="validate">校验</option></select></td>
            <td style={{ width: 40 }}>{canEdit && <a onClick={() => set('kb_refs', E.kb_refs.filter((_: any, j: number) => j !== i))}>删除</a>}</td></tr>)}</tbody></table>
            : <div className="muted">尚未关联知识</div>}
          {canEdit && <div style={{ marginTop: 6 }}><Btn s id="kbPick" onClick={() => setPick(true)}>从知识库选择…</Btn> <span className="muted">知识库全平台通用，可按标签筛选</span></div>}
        </div>

        <div className="sec"><h4 style={{ margin: '0 0 8px' }}>⑤ 关联工具（来自工具中心）与入参映射</h4>
          {E.tools.map((b: Bind, ti: number) => {
            const tl = toolOf(b.tool)
            if (!tl) return <div key={ti} className="err">工具 {b.tool} 未在工具中心注册 <a onClick={() => set('tools', E.tools.filter((_: any, j: number) => j !== ti))}>移除</a></div>
            const kindCls = ({ skill: 'skill', workflow: 'wf', mcp: 'mcp', cli: 'cli' } as any)[tl.type] || 'api'
            const isWf = tl.type === 'workflow'
            return <div key={b.tool} className={'bound-tool is-' + kindCls} data-bound={b.tool}>
              <div className="row"><b>{tl.display_name}</b><span className="mono">{tl.name}</span>{tl.source === 'builtin' ? <Badge c="g">平台内置</Badge> : <TypeTag type={tl.type} />}{tl.type === 'mcp' && <span className="tg">{tl.mcp_server?.display_name}</span>}{tl.confirm_required && <Badge c="y">写操作需确认</Badge>}
                {tl.remote_missing ? <Badge c="r">远端已消失，请更换</Badge> : tl.type === 'mcp' && !tl.enabled ? <Badge c="r">已停用</Badge> : null}<span className="sp" />
                {canEdit && <a onClick={() => set('tools', E.tools.filter((_: any, j: number) => j !== ti))}>移除</a>}</div>
              {tl.description && <div className="muted" style={{ margin: '4px 0' }}>{tl.description}</div>}
              {tl.type === 'skill' ? <>
                <div className="note" style={{ margin: '6px 0' }}>本意图抽取的槽位会作为上下文交给 AI，无需在此做入参映射。输出格式在意图的回复要求中约定。</div>
                {tl.skill?.files?.length ? <><div className="muted">技能文件（v{tl.skill.version}）</div><ul className="mono" style={{ margin: '4px 0', paddingLeft: 18, fontSize: 12 }}>{tl.skill.files.map((f: any) => <li key={f.path}>{f.path}</li>)}</ul></>
                  : <div className="err">该技能尚未上传技能包，请先在工具中心上传</div>}</>
              : <>
                {isWf && <div className="muted" style={{ margin: '4px 0 6px' }}>入参来自已同步的工作流定义。必填项须映射或已有默认值；文件类入参只能映射到「文件 / 图片」槽位。</div>}
                {!(tl.params || []).length ? <div className="muted">无入参</div> :
                <table style={{ marginTop: 6 }}><thead><tr><th>入参</th><th>类型</th><th>必填</th>{isWf && <th>默认值</th>}<th>取值来源</th><th>值</th></tr></thead><tbody>
                  {tl.params.map((p: any) => { const m = b.param_map[p.name] || { source: '槽位', value: '' }; const file = isFileParam(p)
                    const slotList = E.slots.filter((s: Slot) => s.name && (!file || FILE_SLOT.includes(slotDef(s.name)?.type)))
                    return <tr key={p.name}>
                    <td className="mono">{p.name}</td><td className="muted">{pTypeLabel(p.type)}</td><td>{p.required ? <Badge c="r">必填</Badge> : <span className="muted">选填</span>}</td>
                    {isWf && <td className="mono muted">{p.default || '—'}</td>}
                    <td><select value={m.source} disabled={ro} onChange={e => setMap(ti, p.name, e.target.value, e.target.value === '上下文' ? 'session_id' : '')}>{['槽位', '常量', '上下文'].map(x => <option key={x}>{x}</option>)}</select></td>
                    <td>{m.source === '槽位' ? <select value={m.value} disabled={ro} onChange={e => setMap(ti, p.name, '槽位', e.target.value)}>
                      <option value="">{file ? (slotList.length ? '— 选择文件/图片槽位 —' : '— 暂无文件/图片槽位 —') : p.default ? '— 不映射，用默认值 —' : '— 选择槽位 —'}</option>
                      {slotList.map((s: Slot) => <option key={s.name} value={s.name}>{s.name}{file ? `（${slotDef(s.name)?.type}）` : ''}</option>)}
                      {m.value && !slotList.some((s: Slot) => s.name === m.value) && <option value={m.value}>{m.value}（不可用）</option>}</select>
                      : m.source === '上下文' ? <select value={m.value} disabled={ro} onChange={e => setMap(ti, p.name, '上下文', e.target.value)}>{CTX.map(x => <option key={x}>{x}</option>)}</select>
                        : <input type="text" value={m.value} disabled={ro} placeholder={p.default ? '默认 ' + p.default : '固定值'} onChange={e => setMap(ti, p.name, '常量', e.target.value)} />}</td></tr> })}
                </tbody></table>}</>}
            </div>
          })}
          {canEdit && (() => {
            const mcpAvail = servers.map((v: any) => {
              const avail = (v.tools || []).filter((x: any) => x.enabled && !x.remote_missing && !bound.has(x.name)).length
              return { ...v, avail }
            }).filter((v: any) => v.avail)
            const packs: { pack: string; pack_name: string; count: number }[] = facets.packs || []
            const groups: { pack: string; group: string; count: number }[] = facets.groups || []
            const cliGroups: [string, string, number][] = pickTy !== 'cli' ? [] : [
              ...packs.map((x: any) => [x.pack, shortPackName(x.pack_name, x.pack), x.count] as [string, string, number]),
              ...groups.map((x: any) => {
                const pack = shortPackName(packs.find((p: any) => p.pack === x.pack)?.pack_name, x.pack)
                return [x.pack + '|' + x.group, pack ? `${x.group} · ${pack}` : x.group, x.count] as [string, string, number]
              }),
            ]
            const grp = cliGroups.some(g => g[0] === cliGrp) ? cliGrp : ''
            return <><div className="row" id="ePicker" style={{ flexWrap: 'wrap', gap: 8 }}>
              <select id="eToolType" value={pickTy} onChange={e => { setPickTy(e.target.value); sessionStorage.setItem('pickToolType', e.target.value); setToolSel(''); setCliGrp('') }}>
                <option value="">— 选择类型 —</option>
                {PICK_TYPES.filter(([k2]) => typeCounts[k2]).map(([k2, l]) => <option key={k2} value={k2}>{l}（{typeCounts[k2]}）</option>)}</select>
              {cliGroups.length > 0 && <SearchSelect id="eCliGrp" className="compact" value={grp}
                empty={{ value: '', label: `全部命令包（${facets.by_type?.cli || 0}）` }}
                options={cliGroups.map(([g, l, n]) => ({ value: g, label: `${l}（${n}）` }))}
                onChange={v => { setCliGrp(v); setToolSel('') }} />}
              <ToolCombobox key={pickTy + '|' + cliGrp} typ={pickTy} pack={packOf} group={groupOf}
                bound={bound} value={toolSel} mcpItems={mcpAvail} disabled={!pickTy}
                onChange={(name, tool) => { setToolSel(name); if (tool) setExtraTools(xs => xs.some(x => x.name === tool.name) ? xs : [...xs, tool]) }} />
              <Btn s id="toolAdd" onClick={addTool}>＋ 关联</Btn></div>
              <div className="muted" style={{ marginTop: 4 }}>先选类型，再在输入框里搜索工具。下拉列表限高，滚动可继续加载。已关联的工具会标明，不能重复选。技能无需映射入参；工作流按已同步入参映射（必填须映射或有默认值）；MCP 先选服务，再点「关联」下钻多选子工具；CLI 命令多时可先选命令包 / 业务分组再搜索；维护类平台内置工具不可选</div></>
          })()}
        </div></> : <div className="muted" id="eNoBind" style={{ margin: '8px 0 16px' }}>当前处理方式不使用知识库和工具，关联入口已隐藏。如需绑定，请改回「工具与知识流程」。</div>}

        <SingleTest code={code} draft={() => ({ code, ...payload() })} />

        <div className="sec"><h4 style={{ margin: '0 0 8px' }}>修改记录</h4>
          {(d.logs || []).length ? <table id="eLog"><tbody>{(d.logs || []).slice(0, 8).map((l: any, i: number) => <tr key={i}><td className="muted" style={{ whiteSpace: 'nowrap' }}>{fmt(l.at, false)}</td>
            <td style={{ whiteSpace: 'nowrap' }}>{l.by}</td><td>{l.message}</td></tr>)}</tbody></table> : <div className="muted">暂无</div>}
        </div>
      </div>
    </Drawer>
    {pick && <KbPicker code={code} selected={E.kb_refs} onClose={() => setPick(false)} onOk={refs => { set('kb_refs', refs); setPick(false); toast(`已关联 ${refs.length} 项知识`) }} />}
    {mcpPick && (() => { const v = servers.find((x: any) => x.name === mcpPick); return v ? <McpPicker server={v} tools={v.tools} bound={E.tools.map((b: Bind) => b.tool)}
      onClose={() => { setMcpPick(null); setToolSel('') }}
      onOk={names => { const ts = names.map(n => toolOf(n)).filter(Boolean); setExtraTools(xs => [...xs, ...ts.filter(t => !xs.some(x => x.name === t.name))]); set('tools', [...E.tools, ...ts.map(t => bindOf(t))]); setMcpPick(null); setToolSel(''); toast(`已关联 ${ts.length} 个 MCP 工具，请确认入参映射`) }} /> : null })()}
    {slotNew && <SlotForm slot={null} inline onClose={() => setSlotNew(false)} onDone={s => { setSlotNew(false); reloadSlots(); set('slots', [...E.slots, { name: s.name, required: false, ask: '' }]) }} />}
  </>
}


const COMBO_PAGE = 30

function ToolCombobox({ typ, pack, group, bound, value, onChange, disabled, mcpItems }: {
  typ: string; pack?: string; group?: string; bound: Set<string>; value: string
  onChange: (name: string, tool?: any) => void; disabled?: boolean; mcpItems: any[]
}) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [qDeb, setQDeb] = useState('')
  const [items, setItems] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [done, setDone] = useState(true)
  const [hi, setHi] = useState(-1)
  const box = useRef<HTMLDivElement>(null)
  const list = useRef<HTMLDivElement>(null)
  const pageRef = useRef(1)
  const loadingRef = useRef(false)
  const filtersRef = useRef('')
  const labelOf = (name: string) => {
    if (name.startsWith('srv:')) {
      const v = mcpItems.find((x: any) => x.name === name.slice(4))
      return v ? `${v.display_name}（${v.name} · ${v.avail} 个可用工具）` : name
    }
    const t = items.find((x: any) => x.name === name)
    return t ? `${t.display_name}（${t.name}）` : name
  }
  useEffect(() => {
    const tmr = setTimeout(() => setQDeb(q), 300)
    return () => clearTimeout(tmr)
  }, [q])
  const fetchPage = useCallback((pg: number, append: boolean, f: string) => {
    if (!typ || typ === 'mcp') return
    loadingRef.current = true
    setLoading(true)
    api.get('/tools' + qs({
      page: pg, page_size: COMBO_PAGE, q: qDeb,
      type: typ === 'builtin' ? undefined : typ,
      source: typ === 'builtin' ? 'builtin' : undefined,
      pack: pack || undefined,
      group: group || undefined,
      maintenance: false,
      exclude_type: typ === 'builtin' ? 'mcp' : undefined,
    })).then((d: any) => {
      if (filtersRef.current !== f) return
      setTotal(d.total || 0)
      setItems(prev => {
        const base = append ? prev : []
        const seen = new Set(base.map((t: any) => t.name))
        return [...base, ...(d.items || []).filter((t: any) => !seen.has(t.name))]
      })
      pageRef.current = pg
      setDone(!(d.items || []).length || pg * COMBO_PAGE >= (d.total || 0))
    }).catch(() => { if (filtersRef.current === f) setDone(true) })
      .finally(() => { if (filtersRef.current === f) { loadingRef.current = false; setLoading(false) } })
  }, [typ, pack, group, qDeb])
  useEffect(() => {
    if (!typ || typ === 'mcp') { setItems([]); setTotal(0); setDone(true); setLoading(false); return }
    const f = [typ, pack || '', group || '', qDeb].join('|')
    filtersRef.current = f
    pageRef.current = 1
    setDone(false)
    fetchPage(1, false, f)
  }, [typ, pack, group, qDeb, fetchPage])
  const mcpShown = typ === 'mcp' ? mcpItems.filter((v: any) => {
    const k = q.trim().toLowerCase()
    return !k || [v.name, v.display_name, v.description].some((s: string) => (s || '').toLowerCase().includes(k))
  }) : []
  const rows = typ === 'mcp' ? mcpShown.map((v: any) => ({
    key: 'srv:' + v.name, label: v.display_name, sub: `${v.name} · ${v.avail} 个可用工具`, disabled: false, raw: v,
  })) : items.map((t: any) => ({
    key: t.name, label: t.display_name, sub: t.name, disabled: bound.has(t.name), raw: t,
  }))
  const enabledIdx = () => rows.map((r, i) => r.disabled ? -1 : i).filter(i => i >= 0)
  const onScroll = (e: any) => {
    const el = e.currentTarget
    if (typ === 'mcp' || loadingRef.current || done) return
    if (el.scrollTop + el.clientHeight >= el.scrollHeight - 48) {
      const f = filtersRef.current
      fetchPage(pageRef.current + 1, true, f)
    }
  }
  useEffect(() => {
    if (!open) return
    const h = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false) }
    const k = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', h)
    document.addEventListener('keydown', k)
    return () => { document.removeEventListener('mousedown', h); document.removeEventListener('keydown', k) }
  }, [open])
  const pick = (r: { key: string; disabled: boolean; raw: any }) => {
    if (r.disabled) return
    onChange(r.key, typ === 'mcp' ? undefined : r.raw)
    setQ('')
    setOpen(false)
  }
  const onKey = (e: { key: string; preventDefault: () => void }) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      if (!open) { setOpen(true); return }
      const en = enabledIdx()
      if (!en.length) return
      const cur = en.indexOf(hi)
      const next = e.key === 'ArrowDown' ? en[(cur + 1) % en.length] : en[(cur - 1 + en.length) % en.length]
      setHi(next)
      const el = list.current?.querySelector(`[data-name="${CSS.escape(rows[next].key)}"]`) as HTMLElement | null
      el?.scrollIntoView({ block: 'nearest' })
    } else if (e.key === 'Enter') {
      e.preventDefault()
      const en = enabledIdx()
      const i = en.includes(hi) ? hi : en[0]
      if (i != null && rows[i]) pick(rows[i])
    } else if (e.key === 'Escape') setOpen(false)
  }
  const shown = value && !q ? labelOf(value) : q
  return <div className="combo" ref={box}>
    <input type="text" id="eTool" value={shown} disabled={disabled} autoComplete="off"
      placeholder={typ === 'mcp' ? '搜索并选择 MCP 服务' : '搜索并选择工具'}
      onFocus={e => { if (!disabled) { setOpen(true); if (value && !q) e.currentTarget.select() } }}
      onChange={e => { setQ(e.target.value); setOpen(true); if (value) onChange('') }}
      onKeyDown={onKey} />
    {open && !disabled && <div className="combo-list" id="eToolList" ref={list} onScroll={onScroll} role="listbox">
      {typ === 'mcp' && !mcpShown.length && <div className="combo-empty">{q.trim() ? '没有符合条件的服务' : '没有可关联的 MCP 服务'}</div>}
      {typ && typ !== 'mcp' && !loading && !rows.length && <div className="combo-empty">{qDeb.trim() ? '没有符合条件的工具' : '没有可关联的工具'}</div>}
      {rows.map((r, i) => <div key={r.key} className={'combo-item' + (r.disabled ? ' disabled' : '') + (i === hi ? ' on' : '')}
        data-name={r.key} role="option" aria-disabled={r.disabled || undefined} aria-selected={value === r.key}
        onMouseEnter={() => setHi(i)} onMouseDown={e => { e.preventDefault(); pick(r) }}>
        <span>{r.label}</span><span className="mono muted">{r.sub}</span>
        {r.disabled && <span className="muted" style={{ marginLeft: 'auto' }}>已关联</span>}
      </div>)}
      {typ !== 'mcp' && (loading || !done) && <div className="combo-empty">{loading ? '加载中…' : `已加载 ${items.length} / ${total}，滚动继续加载`}</div>}
    </div>}
  </div>
}

function SingleTest({ code, draft }: { code: string; draft: () => any }) {
  const { space } = useSession()
  const [t, setT] = useState('')
  const [r, setR] = useState<any>(null)
  const run = async () => {
    if (!t.trim()) { toast('请输入一句话'); return }
    try { setR(await api.post(`/spaces/${space.id}/test`, { text: t, draft: draft() })) } catch (e) { toast(errLines(e).join('；')) }
  }
  const ph = space.id === 'mc' ? '帮我做一张 TikTok 美区的主图' : '这款有现货吗，要500件'
  return <div className="sec"><h4 style={{ margin: '0 0 8px' }}>⑤ 单句测试</h4>
    <div className="row"><input type="text" id="tT" value={t} onChange={e => setT(e.target.value)} onKeyDown={e => e.key === 'Enter' && run()} placeholder={'输入一句用户的话，如：' + ph} style={{ flex: 1 }} />
      <Btn p id="tRun" onClick={run}>测试</Btn></div>
    <div id="tOut" style={{ marginTop: 8 }}>{r && <TestResult r={r} code={code} />}</div>
  </div>
}

function TestResult({ r }: { r: any; code: string }) {
  const head = <div className="muted" id="tTh" style={{ marginBottom: 6 }}>使用本空间识别阈值 <b>{r.threshold}</b>（候选下限 {r.candidate_min}）· 在「意图管理 › 空间设置」调整</div>
  if (r.verdict === 'clarify') return <>{head}
    <div className="note w" id="tVerdict">判定：<b>澄清</b> —— 最高置信度 {r.top_score} 低于阈值 {r.threshold}，列出 {r.clarify_candidates.length} 个候选让用户选</div>
    <div id="tClar">Bot：「{r.clarify_prompt}」<ol style={{ margin: '4px 0' }}>{r.clarify_candidates.map((x: any) => <li key={x.code}>{x.name} <span className="mono">{x.code}</span> <span className="muted">{x.score}</span></li>)}</ol>
      <span className="muted">用户选中后按该意图继续；澄清 {r.max_clarify} 次仍不明确 → 最终动作：{r.final_text}</span></div></>
  if (r.verdict === 'final') return <>{head}
    <div className="err" id="tVerdict">判定：<b>最终动作 · {r.final_action_label}</b> —— 最高置信度 {r.top_score}，没有不低于候选下限 {r.candidate_min} 的候选{r.final_action === 'decline' ? `，回复「${r.final_message || ''}」` : ''}</div>
    {r.candidates.length > 0 && <div className="muted">候选（均低于下限）：{r.candidates.map((x: any) => `${x.code} ${x.score}`).join('，')}</div>}</>
  const others = r.candidates.slice(1)
  return <>{head}
    <div className={r.hit.is_current ? 'ok' : 'note w'} id="tVerdict">判定：<b>命中</b> <b>{r.hit.name}</b> <span className="mono">{r.hit.code}</span> · 置信度 {r.top_score} ≥ 阈值 {r.threshold}{r.hit.is_current ? '（命中当前意图）' : '（未命中当前意图，请补充例句或区分描述）'}</div>
    {others.length > 0 && <div className="muted">其他候选：{others.map((x: any) => `${x.code} ${x.score}`).join('，')}</div>}
    <table><tbody>
      <tr><th style={{ width: 90 }}>槽位</th><td>{r.slots.length ? r.slots.map((s: any, i: number) => <span key={s.name}>{i > 0 && '，'}{s.name}={s.value ? <b>{s.value}</b> : <span className="muted">缺失</span>}</span>) : <span className="muted">无</span>}</td></tr>
      <tr><th>参考知识</th><td id="tRef">{r.reference.length ? r.reference.map((x: any, i: number) => <div key={i}>· {x.label}：<span className="muted">{x.preview}</span></div>) : <span className="muted">无</span>}</td></tr>
      <tr><th>将执行的校验</th><td id="tChk">{r.validate.length ? <>{r.validate.map((x: any, i: number) => <div key={i}>· {x.label}：<span className="muted">{x.preview}</span></div>)}<div className="muted">生成结果需逐条比对以上校验知识，不通过则改写或拦截</div></> : <span className="muted">无</span>}</td></tr>
      <tr><th>调用工具</th><td id="tTool">{r.tools.length ? <>{r.tools.map((t: any) => <div key={t.tool}>{t.registered ? <><Badge c="b">{({ http: 'API 接口', mcp: 'MCP 工具', cli: 'CLI 命令', skill: '技能', workflow: '工作流' } as any)[t.type]}</Badge> <span className="mono">{t.tool}({t.args.map((a: any) => `${a.name}=${a.value}`).join(', ')})</span></> : `${t.tool}（未注册）`}</div>)}
        <div className="muted">失败自动重试 {r.tool_retry} 次，仍失败 → 最终动作：{r.final_text}</div></> : <span className="muted">无</span>}</td></tr>
      <tr><th>处理</th><td id="tDec"><b>{r.decision}</b></td></tr>
    </tbody></table>
    <div className="muted">说明：单句测试用例句相似度模拟识别（{r.recognizer}，分值与 Bot 的真实模型不同），仅用于自查例句与配置是否合理。</div></>
}
