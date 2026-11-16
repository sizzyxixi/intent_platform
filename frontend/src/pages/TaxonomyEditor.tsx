import { useEffect, useMemo, useState } from 'react'
import { api, errLines } from '../api'
import { useSession } from '../session'
import { Btn, Modal, toast } from '../ui'
import { CatNode, Tax, toCascade, withoutSubtree } from '../taxonomy'
import { Cascader } from '../picker'

type Form = {
  parent: number | null
  id?: number
  name: string
  code: string
  description: string
  depth: number
}
type MoveForm = { node: CatNode; parentId: string }

export default function TaxonomyEditor({ tax, reload }: { tax: Tax; reload: () => void }) {
  const { space, canEdit, reloadSpaces } = useSession()
  const n = tax.category_levels
  const [open, setOpen] = useState<Set<number>>(() => new Set(tax.tree.map(r => r.id)))
  const [form, setForm] = useState<Form | null>(null)
  const [move, setMove] = useState<MoveForm | null>(null)
  const [moveIntent, setMoveIntent] = useState<{ code: string; name: string; category_id: string } | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    setOpen(o => {
      const next = new Set(o)
      tax.tree.forEach(r => next.add(r.id))
      return next
    })
  }, [tax])

  const done = (msg: string) => { toast(msg); reload(); reloadSpaces() }
  const run = async (fn: () => Promise<any>, msg: string) => {
    if (busy) return
    setBusy(true)
    try { await fn(); done(msg) } catch (e) { toast(errLines(e)[0]) } finally { setBusy(false) }
  }
  const toggle = (id: number) => setOpen(s => {
    const nset = new Set(s)
    if (nset.has(id)) nset.delete(id); else nset.add(id)
    return nset
  })


  const leaves = useMemo(() => {
    const out: { id: number; label: string }[] = []
    const walk = (nodes: CatNode[], names: string[]) => {
      for (const c of nodes) {
        const label = [...names, c.name].join(' / ')
        if (c.depth === n) out.push({ id: c.id, label })
        else walk(c.children || [], [...names, c.name])
      }
    }
    if (n) walk(tax.tree, [])
    return out
  }, [tax, n])

  const saveForm = () => form && run(async () => {
    if (form.id) {
      await api.patch(`/spaces/${space.id}/categories/${form.id}`, {
        name: form.name, code: form.depth === 1 ? (form.code || null) : undefined, description: form.description,
      })
    } else {
      await api.post(`/spaces/${space.id}/categories`, {
        parent_id: form.parent, name: form.name, code: form.code || null, description: form.description,
      })
    }
    setForm(null)
  }, form.id ? '已保存分类' : '已新增分类')

  const doMove = () => move && run(async () => {
    await api.patch(`/spaces/${space.id}/categories/${move.node.id}`, { parent_id: move.parentId ? Number(move.parentId) : null })
    setMove(null)
  }, '已移动分类')

  const doMoveIntent = () => moveIntent && run(async () => {
    await api.put(`/intents/${moveIntent.code}`, { category_id: Number(moveIntent.category_id) })
    setMoveIntent(null)
  }, '已移动意图')

  const startAdd = (parent: number | null, depth: number) => setForm({ parent, name: '', code: '', description: '', depth })
  const startEdit = (c: CatNode) => setForm({ parent: null, id: c.id, name: c.name, code: c.code || '', description: c.description || '', depth: c.depth })
  const del = (c: CatNode) => {
    if (c.count || (c.children && c.children.length)) { toast('有下级或有意图的分类不能删除，可先停用'); return }
    window.confirm(`删除分类「${c.name}」？`) && run(() => api.del(`/spaces/${space.id}/categories/${c.id}`), '已删除')
  }
  const disable = (c: CatNode, enabled: boolean) => run(
    () => api.patch(`/spaces/${space.id}/categories/${c.id}`, { enabled }),
    enabled ? '已启用分类' : '已停用分类')

  const ops = (c: CatNode, idx: number, sibs: number) => canEdit && <span className="tax-ops catops">
    {c.depth < n && <a title={`新增${tax.levels[c.depth]}`} onClick={() => startAdd(c.id, c.depth + 1)}>＋子级</a>}
    <a title="新增同级" onClick={() => startAdd(c.depth === 1 ? null : findParentId(tax.tree, c.id), c.depth)}>＋同级</a>
    <a title="编辑" onClick={() => startEdit(c)}>编辑</a>
    {idx > 0 && <a title="上移" onClick={() => run(() => api.patch(`/spaces/${space.id}/categories/${c.id}`, { shift: 'up' }), '已上移')}>上移</a>}
    {idx < sibs - 1 && <a title="下移" onClick={() => run(() => api.patch(`/spaces/${space.id}/categories/${c.id}`, { shift: 'down' }), '已下移')}>下移</a>}
    {c.depth > 1 && <a title="移动到其他上级" onClick={() => setMove({ node: c, parentId: String(findParentId(tax.tree, c.id) ?? '') })}>移动</a>}
    {c.enabled === false
      ? <a title="启用" onClick={() => disable(c, true)}>启用</a>
      : <a title="停用" onClick={() => disable(c, false)}>停用</a>}
    {!c.count && !(c.children && c.children.length) && <a title="删除" onClick={() => del(c)}>✕</a>}
  </span>

  const row = (c: CatNode, idx: number, sibs: number): any => {
    const kids = c.children || []
    const isLeaf = c.depth === n
    const expanded = open.has(c.id)
    const hasBody = (!isLeaf && kids.length > 0) || (isLeaf && (c.intents || []).length > 0)
    return <div key={c.id} className={'catnode' + (c.enabled === false ? ' tax-dis' : '')} style={{ marginLeft: c.depth > 1 ? 16 : 0 }}>
      <div className="tax-row catrow">
        <span className="tax-tog" onClick={() => hasBody && toggle(c.id)}>{hasBody ? (expanded ? '▾' : '▸') : '·'}</span>
        <div className="tax-meta">
          <span className="tax-lv">{tax.levels[c.depth - 1]}</span>
          <b>{c.name}</b>
          {c.code && <span className="mono muted"> {c.code}</span>}
          {c.enabled === false && <span className="muted">（已停用）</span>}
          <span className="muted"> {c.count}</span>
          {c.description ? <div className="tax-desc clamp2" title={c.description}>{c.description}</div> : <div className="tax-desc muted">暂无描述</div>}
        </div>
        {ops(c, idx, sibs)}
      </div>
      {expanded && !isLeaf && kids.map((k, i) => row(k, i, kids.length))}
      {expanded && isLeaf && (c.intents || []).map(it => <div key={it.code} className="tax-int">
        <span className="tax-lv">{tax.levels[tax.levels.length - 1]}</span>
        <span className="mono">{it.code}</span>
        <span>{it.name}</span>
        <span className="muted">{it.status}</span>
        {canEdit && leaves.length > 1 && <a title="移动到其他分类" onClick={() => setMoveIntent({ code: it.code, name: it.name, category_id: String(c.id) })}>移动</a>}
      </div>)}
    </div>
  }

  return <>
    <div className="card" id="taxCard">
      <h3>分类（{tax.levels.slice(0, -1).join(' / ') || '无分类层'}）
        <span className="sp" />
        <span className="muted" style={{ fontWeight: 'normal' }}>展开后可新增子级 / 同级、编辑描述、移动和调整顺序；有下级或有意图的分类不能删除</span>
      </h3>
      {n === 0 ? <div className="muted">本空间没有分类层，意图直接挂在空间下。可在上方「分类层级」里插入一层。</div> : <>
        <div className="tax-tree">
          {tax.tree.map((c, i) => row(c, i, tax.tree.length))}
        </div>
        {canEdit && <div className="row" style={{ marginTop: 8 }}>
          <Btn s id="catAddRoot" onClick={() => startAdd(null, 1)}>＋ 新增{tax.levels[0]}</Btn>
        </div>}
        {tax.unassigned > 0 && <div className="muted" style={{ marginTop: 8 }}>还有 {tax.unassigned} 个意图未挂分类。</div>}
      </>}
    </div>

    {form && <Modal title={form.id ? `编辑「${form.name}」` : `新增${tax.levels[form.depth - 1]}`} testId="catForm" onClose={() => setForm(null)}
      foot={<><Btn onClick={() => setForm(null)}>取消</Btn> <Btn p id="catNewOk" disabled={!form.name.trim()} onClick={saveForm}>{form.id ? '保存' : '添加'}</Btn></>}>
      <div className="f"><label>{tax.levels[form.depth - 1]}名称 *</label>
        <input type="text" id="catNewName" autoFocus value={form.name} maxLength={30} onChange={e => setForm({ ...form, name: e.target.value })} /></div>
      {form.depth === 1 && <div className="f"><label>编码（大写，可选）</label>
        <input type="text" id="catNewCode" placeholder="如 DESIGN" value={form.code} onChange={e => setForm({ ...form, code: e.target.value.toUpperCase() })} /></div>}
      <div className="f"><label>描述（可选，最多 500 字）</label>
        <textarea id="catNewDesc" rows={3} maxLength={500} value={form.description} placeholder="说明这一层覆盖什么范围，便于按层路由"
          onChange={e => setForm({ ...form, description: e.target.value })} /></div>
      <div className="muted">描述会出现在分类树、运行时配置和开放接口里，供智能体按层判断该往哪一支走。</div>
    </Modal>}

    {move && <Modal title={`移动「${move.node.name}」`} testId="catMove" onClose={() => setMove(null)}
      foot={<><Btn onClick={() => setMove(null)}>取消</Btn> <Btn p id="catMoveOk" disabled={!move.parentId} onClick={doMove}>移动</Btn></>}>
      <div className="f"><label>新的上级（{tax.levels[move.node.depth - 2]}）</label>
        <Cascader id="catMoveParent" value={move.parentId} placeholder="请选择上级"
          tree={toCascade(withoutSubtree(tax.tree, move.node.id), move.node.depth - 1)}
          selectable={n => n.depth === move.node.depth - 1}
          onChange={v => setMove({ ...move, parentId: v })} />
      </div>
    </Modal>}

    {moveIntent && <Modal title={`移动意图「${moveIntent.name}」`} testId="intMove" onClose={() => setMoveIntent(null)}
      foot={<><Btn onClick={() => setMoveIntent(null)}>取消</Btn> <Btn p id="intMoveOk" disabled={!moveIntent.category_id} onClick={doMoveIntent}>移动</Btn></>}>
      <div className="f"><label>目标分类（{tax.levels.slice(0, -1).join(' / ')}）</label>
        <Cascader id="intMoveCat" value={moveIntent.category_id} placeholder="请选择目标分类"
          tree={toCascade(tax.tree)} onChange={v => setMoveIntent({ ...moveIntent, category_id: v })} />
      </div>
    </Modal>}
  </>
}

function findParentId(nodes: CatNode[], id: number, parent: number | null = null): number | null {
  for (const c of nodes) {
    if (c.id === id) return parent
    const hit = findParentId(c.children || [], id, c.id)
    if (hit !== null || (c.children || []).some(x => x.id === id)) {
      if ((c.children || []).some(x => x.id === id)) return c.id
      return hit
    }
  }
  return null
}
