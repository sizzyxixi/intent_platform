import { useEffect, useRef, useState } from 'react'
import { api, errLines, fmt } from '../api'
import { useSession } from '../session'
import { Badge, Btn, Drawer, ErrBox, ST_C, toast, useAsync } from '../ui'
import KbPicker from './KbPicker'
import { SlotForm } from './Slots'
import { Tax, leaves } from '../taxonomy'
import { McpPicker } from './McpServers'
import { TypeTag, isFileParam, pTypeLabel } from './ToolEdit'

type Slot = { name: string; required: boolean; ask: string }
type Ref = { kb_id: string; doc_id: string; chunk_id: string; usage: string; label?: string; valid?: boolean }
type Bind = { tool: string; param_map: Record<string, { source: string; value: string }> }
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
  const [toolQ, setToolQ] = useState('')
  const [pickTy0, setPickTy] = useState(() => sessionStorage.getItem('pickToolType') || '')
  const [mcpPick, setMcpPick] = useState<string | null>(null)
  const body = useRef<HTMLDivElement>(null)
  const [slots, reloadSlots] = useAsync(() => api.get(`/spaces/${space.id}/slots`), [space.id])
  const [tax] = useAsync<Tax>(() => api.get(`/spaces/${space.id}/taxonomy`), [space.id])
  const [tools] = useAsync(() => api.get('/tools'), [])
  useEffect(() => {
    api.get(`/intents/${code}`).then(x => {
      setD(x)
      setE({ name: x.name, category_id: x.category_id, description: x.description, ex: x.examples.join('\n'), status: x.status,
        slots: x.slots.map((s: any) => ({ name: s.name, required: s.required, ask: s.ask })),
        kb_refs: x.kb_refs, tools: x.tools })
    }).catch(e => { toast(errLines(e)[0]); onClose() })
  // eslint-disable-next-line
  }, [code])
  if (!d || !E || !slots || !tax || !tools) return <Drawer title="加载中…" onClose={onClose}><div className="loading">加载中…</div></Drawer>
  const set = (k: string, v: any) => setE((e: any) => ({ ...e, [k]: v }))
  const opts = leaves(tax)
  const curRoot = opts.find(o => o.id === d.category_id)?.root
  const cur = opts.find(o => o.id === E.category_id)
  const lvNames = tax.levels
  const toolOf = (name: string) => tools.find((t: any) => t.name === name)
  const slotDef = (name: string) => slots.find((s: any) => s.name === name)
  const payload = () => ({ name: E.name.trim(), category_id: E.category_id, description: E.description.trim(),
    examples: E.ex.split('\n').map((x: string) => x.trim()).filter(Boolean), slots: E.slots,
    kb_refs: E.kb_refs.map((r: Ref) => ({ kb_id: r.kb_id, doc_id: r.doc_id, chunk_id: r.chunk_id, usage: r.usage })),
    tools: E.tools, status: E.status })
  const showErrs = (l: string[]) => { setErrs(l); if (body.current) body.current.scrollTop = 0 }
  const check = async () => {
    try {
      const r = await api.post(`/intents/${code}/validate`, payload())
      if (r.problems.length) showErrs(r.problems); else { setErrs(undefined); toast('校验通过，可以启用') }
    } catch (e) { showErrs(errLines(e)) }
  }
  const save = async () => {
    try {
      const r = await api.put(`/intents/${code}`, payload())
      toast('已保存' + (r.warnings?.length ? '（提示：' + r.warnings.join('；') + '）' : ''))
      onSaved(); reloadSpaces(); onClose()
    } catch (e: any) {
      showErrs(errLines(e))
      toast(e.code === 'ENABLE_CHECK_FAILED' ? '启用前校验未通过，未保存' : '保存失败')
    }
  }
  const bindOf = (t: any): Bind => {
    const map: Bind['param_map'] = {}
    if (t.type !== 'skill') t.params.forEach((p: any) => {
      const s = E.slots.find((x: Slot) => x.name === p.name && (!isFileParam(p) || FILE_SLOT.includes(slotDef(x.name)?.type)))
      map[p.name] = s ? { source: '槽位', value: s.name } : p.name === 'session_id' ? { source: '上下文', value: 'session_id' } : { source: '槽位', value: '' }
    })
    return { tool: t.name, param_map: map }
  }
  const servers: any[] = Object.values(tools.filter((t: any) => t.type === 'mcp' && t.server).reduce((m: any, t: any) => {
    (m[t.server] ||= { ...t.mcp_server, tools: [] }).tools.push(t); return m }, {}))
  const bound = new Set(E.tools.map((b: Bind) => b.tool))
  const groups: Record<string, any[]> = { http: [], mcp: [], cli: [], skill: [], workflow: [], builtin: [] }
  tools.filter((t: any) => t.type !== 'mcp' && !t.maintenance && !bound.has(t.name)).forEach((t: any) => { (t.source === 'builtin' ? groups.builtin : groups[t.type])?.push(t) })
  servers.forEach((v: any) => { const avail = v.tools.filter((t: any) => t.enabled && !t.remote_missing && !bound.has(t.name)).length; if (avail) groups.mcp.push({ ...v, avail }) })
  const pickTy = pickTy0 && groups[pickTy0]?.length ? pickTy0 : (PICK_TYPES.find(([k]) => groups[k]?.length)?.[0] || '')
  const addTool = () => {
    if (!toolSel) { toast('请先选择工具'); return }
    if (toolSel.startsWith('srv:')) { setMcpPick(toolSel.slice(4)); return }
    const t = toolOf(toolSel)
    set('tools', [...E.tools, bindOf(t)]); setToolSel(''); toast(t.type === 'skill' ? `已关联技能 ${toolSel}` : `已关联 ${toolSel}，请确认入参映射`)
  }
  const setMap = (ti: number, p: string, src: string, v: string) => {
    const tl = E.tools.map((b: Bind, i: number) => i !== ti ? b : { ...b, param_map: { ...b.param_map, [p]: { source: src, value: v } } })
    set('tools', tl)
  }
  const ro = !canEdit
  const exCount = E.ex.split('\n').filter((x: string) => x.trim()).length
  const foot = <>
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
          <div className="lvpath" id="eLv">归属：{(cur?.names || []).map((nm, i) => <span key={i}><span className="lv">{lvNames[i]}</span>{nm} <span className="muted">/</span> </span>)}<span className="lv">{lvNames[lvNames.length - 1]}</span><b>{E.name || '（未命名）'}</b></div>
          <div className="grid g2"><div className="f"><label>{lvNames[lvNames.length - 1]}名称 *</label><input type="text" id="eN" value={E.name} disabled={ro} onChange={e => set('name', e.target.value)} /></div>
            <div className="f"><label>编码 *</label><input type="text" id="eC" value={d.code} disabled /></div></div>
          {tax.category_levels > 0 && <div className="f"><label>所属分类（{lvNames.slice(0, -1).join(' / ')}）{curRoot?.code ? <span className="muted">　编码依赖第一层编码 {curRoot.code}，只能在「{curRoot.name}」内移动</span> : null}</label>
            <select id="eCat" value={E.category_id ?? ''} disabled={ro} onChange={e => set('category_id', Number(e.target.value))}>
              {E.category_id == null && <option value="">— 请选择 —</option>}
              {opts.filter(o => !curRoot?.code || o.root.id === curRoot.id).map(o => <option key={o.id} value={o.id}>{o.label}</option>)}</select></div>}
          <div className="f"><label>意图描述 *</label><textarea id="eD" style={{ minHeight: 56 }} value={E.description} disabled={ro} onChange={e => set('description', e.target.value)} />
            <div className="muted" style={{ marginTop: 3 }}>这段描述会被大模型分类和语义匹配使用。请写清：用户想做什么；什么情况算这个意图；什么情况不算（容易和哪个意图混淆）。<br />示例：用户想为商品生成符合平台规范的主图；"做主图""首图"算；只要换白底不算（→ 白底图）。</div></div>
          <div className="f"><label>例句 *（每行一条，至少 3 条，当前 {exCount} 条）</label><textarea id="eEx" style={{ minHeight: 90 }} value={E.ex} disabled={ro} onChange={e => set('ex', e.target.value)} /></div>
        </div>

        <div className="sec"><h4 style={{ margin: '0 0 8px' }}>② 槽位（从本空间槽位列表选择；处理前需要拿到的信息）</h4>
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

        <div className="sec"><h4 style={{ margin: '0 0 8px' }}>③ 关联知识（参考：检索后用于回答/生成；校验：生成后比对，硬约束）</h4>
          {E.kb_refs.length ? <table id="eRefs"><tbody>{E.kb_refs.map((r: Ref, i: number) => <tr key={r.kb_id + r.doc_id + r.chunk_id}>
            <td>{r.valid === false ? <span style={{ color: '#b91c1c' }}>{r.label}</span> : r.label}</td>
            <td style={{ width: 110 }}><select value={r.usage} disabled={ro} onChange={e => set('kb_refs', E.kb_refs.map((x: Ref, j: number) => j === i ? { ...x, usage: e.target.value } : x))}>
              <option value="reference">参考</option><option value="validate">校验</option></select></td>
            <td style={{ width: 40 }}>{canEdit && <a onClick={() => set('kb_refs', E.kb_refs.filter((_: any, j: number) => j !== i))}>删除</a>}</td></tr>)}</tbody></table>
            : <div className="muted">尚未关联知识</div>}
          {canEdit && <div style={{ marginTop: 6 }}><Btn s id="kbPick" onClick={() => setPick(true)}>从知识库选择…</Btn> <span className="muted">知识库全平台通用，可按标签筛选</span></div>}
        </div>

        <div className="sec"><h4 style={{ margin: '0 0 8px' }}>④ 关联工具（来自工具中心）与入参映射</h4>
          {E.tools.map((b: Bind, ti: number) => {
            const tl = toolOf(b.tool)
            if (!tl) return <div key={ti} className="err">工具 {b.tool} 未在工具中心注册 <a onClick={() => set('tools', E.tools.filter((_: any, j: number) => j !== ti))}>移除</a></div>
            const kindCls = ({ skill: 'skill', workflow: 'wf', mcp: 'mcp', cli: 'cli' } as any)[tl.type] || 'api'
            const isWf = tl.type === 'workflow'
            return <div key={b.tool} className={'bound-tool is-' + kindCls} data-bound={b.tool}>
              <div className="row"><b>{tl.display_name}</b><span className="mono">{tl.name}</span>{tl.source === 'builtin' ? <Badge c="g">平台内置</Badge> : <TypeTag type={tl.type} />}{tl.type === 'mcp' && <span className="tg">{tl.mcp_server.display_name}</span>}{tl.confirm_required && <Badge c="y">写操作需确认</Badge>}
                {tl.remote_missing ? <Badge c="r">远端已消失，请更换</Badge> : tl.type === 'mcp' && !tl.enabled ? <Badge c="r">已停用</Badge> : null}<span className="sp" />
                {canEdit && <a onClick={() => set('tools', E.tools.filter((_: any, j: number) => j !== ti))}>移除</a>}</div>
              {tl.description && <div className="muted" style={{ margin: '4px 0' }}>{tl.description}</div>}
              {tl.type === 'skill' ? <>
                <div className="note" style={{ margin: '6px 0' }}>本意图抽取的槽位会作为上下文交给 AI，无需在此做入参映射。输出格式在意图的回复要求中约定。</div>
                {tl.skill?.files?.length ? <><div className="muted">技能文件（v{tl.skill.version}）</div><ul className="mono" style={{ margin: '4px 0', paddingLeft: 18, fontSize: 12 }}>{tl.skill.files.map((f: any) => <li key={f.path}>{f.path}</li>)}</ul></>
                  : <div className="err">该技能尚未上传技能包，请先在工具中心上传</div>}</>
              : <>
                {isWf && <div className="muted" style={{ margin: '4px 0 6px' }}>入参来自已同步的工作流定义。必填项须映射或已有默认值；文件类入参只能映射到「文件 / 图片」槽位。</div>}
                {!tl.params.length ? <div className="muted">无入参</div> :
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
            const items: any[] = pickTy === 'mcp' ? groups.mcp : (groups[pickTy] || [])
            const k = toolQ.trim().toLowerCase()
            const shown = items.filter((x: any) => !k || (pickTy === 'mcp' ? [x.name, x.display_name] : [x.name, x.display_name, x.description]).some((s: string) => (s || '').toLowerCase().includes(k)))
            return <><div className="row" id="ePicker" style={{ flexWrap: 'wrap', gap: 8 }}>
              <select id="eToolType" value={pickTy} onChange={e => { setPickTy(e.target.value); sessionStorage.setItem('pickToolType', e.target.value); setToolSel(''); setToolQ('') }}>
                <option value="">— 选择类型 —</option>
                {PICK_TYPES.filter(([k2]) => groups[k2]?.length).map(([k2, l]) => <option key={k2} value={k2}>{l}（{groups[k2].length}）</option>)}</select>
              <select id="eTool" value={toolSel} disabled={!pickTy} onChange={e => setToolSel(e.target.value)}>
                <option value="">{pickTy === 'mcp' ? '— 选择 MCP 服务 —' : '— 选择工具 —'}</option>
                {pickTy === 'mcp' ? shown.map((v: any) => <option key={v.name} value={'srv:' + v.name}>{v.display_name}（{v.name} · {v.avail} 个可用工具）▸</option>)
                  : shown.map((t: any) => <option key={t.name} value={t.name}>{t.display_name}（{t.name}）</option>)}</select>
              {items.length > 5 && <input type="text" id="eToolQ" value={toolQ} onChange={e => setToolQ(e.target.value)} placeholder="筛选" style={{ width: 140 }} />}
              <Btn s id="toolAdd" onClick={addTool}>＋ 关联</Btn></div>
              <div className="muted" style={{ marginTop: 4 }}>先选类型再选工具。技能无需映射入参；工作流按已同步入参映射（必填须映射或有默认值）；MCP 先选服务，再点「关联」下钻多选子工具；维护类平台内置工具不可选</div></>
          })()}
        </div>

        <SingleTest code={code} draft={() => ({ code, ...payload() })} />

        <div className="sec"><h4 style={{ margin: '0 0 8px' }}>修改记录</h4>
          {d.logs.length ? <table id="eLog"><tbody>{d.logs.slice(0, 8).map((l: any, i: number) => <tr key={i}><td className="muted" style={{ whiteSpace: 'nowrap' }}>{fmt(l.at, false)}</td>
            <td style={{ whiteSpace: 'nowrap' }}>{l.by}</td><td>{l.message}</td></tr>)}</tbody></table> : <div className="muted">暂无</div>}
        </div>
      </div>
    </Drawer>
    {pick && <KbPicker code={code} selected={E.kb_refs} onClose={() => setPick(false)} onOk={refs => { set('kb_refs', refs); setPick(false); toast(`已关联 ${refs.length} 项知识`) }} />}
    {mcpPick && (() => { const v = servers.find((x: any) => x.name === mcpPick); return v ? <McpPicker server={v} tools={v.tools} bound={E.tools.map((b: Bind) => b.tool)}
      onClose={() => { setMcpPick(null); setToolSel('') }}
      onOk={names => { set('tools', [...E.tools, ...names.map(n => bindOf(toolOf(n)))]); setMcpPick(null); setToolSel(''); toast(`已关联 ${names.length} 个 MCP 工具，请确认入参映射`) }} /> : null })()}
    {slotNew && <SlotForm slot={null} inline onClose={() => setSlotNew(false)} onDone={s => { setSlotNew(false); reloadSlots(); set('slots', [...E.slots, { name: s.name, required: false, ask: '' }]) }} />}
  </>
}

function SingleTest({ code, draft }: { code: string; draft: () => any }) {
  const { space } = useSession()
  const [t, setT] = useState('')
  const [r, setR] = useState<any>(null)
  const run = async () => {
    if (!t.trim()) { toast('请输入一句话'); return }
    try { setR(await api.post(`/spaces/${space.id}/test`, { text: t, draft: draft() })) } catch (e) { toast(errLines(e)[0]) }
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
