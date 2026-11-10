// 分类树工具：叶子选项、路径显示
export type CatNode = { id: number; code: string | null; name: string; depth: number; count: number; children: CatNode[] }
export type Tax = { levels: string[]; category_levels: number; tree: CatNode[]; unassigned: number }
export type Leaf = { id: number; label: string; root: CatNode; names: string[] }

export function leaves(tax: Tax): Leaf[] {
  const out: Leaf[] = []
  const walk = (n: CatNode, root: CatNode, names: string[]) => {
    const nm = [...names, n.name]
    if (n.depth === tax.category_levels) out.push({ id: n.id, label: nm.join(' / '), root, names: nm })
    else n.children.forEach(c => walk(c, root, nm))
  }
  tax.tree.forEach(r => walk(r, r, []))
  return out
}

export const pathText = (p: { name: string }[] | undefined) => (p || []).map(x => x.name).join(' / ')
