import { useState } from 'react'
import { api, errLines } from '../api'
import { useSession } from '../session'
import { Btn, ErrBox, Modal, toast } from '../ui'

const TYPES = ['文本', '数字', '日期', '枚举', '文件', '图片']

export function SlotForm({ slot, onClose, onDone, inline }: { slot: any | null; onClose: () => void; onDone: (s: any) => void; inline?: boolean }) {
  const { space } = useSession()
  const [f, setF] = useState({ name: slot?.name || '', display_name: slot?.display_name || '', type: slot?.type || '文本',
    options: (slot?.options || []).join(','), default_ask: slot?.default_ask || '' })
  const [err, setErr] = useState<string[]>()
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value })
  const ok = async () => {
    const body: any = { display_name: f.display_name, type: f.type, default_ask: f.default_ask,
      options: f.type === '枚举' ? f.options.split(/[,，]/).map((x: string) => x.trim()).filter(Boolean) : [] }
    try {
      const r = slot ? await api.put(`/spaces/${space.id}/slots/${slot.name}`, body)
        : await api.post(`/spaces/${space.id}/slots`, { ...body, name: f.name.trim() })
      toast(slot ? '槽位已更新' : inline ? `已新建槽位 ${r.name} 并加入本意图` : '槽位已新建，可在意图中选用')
      onDone(r)
    } catch (e) { setErr(errLines(e)) }
  }
  return <Modal title={slot ? `编辑槽位 · ${slot.name}` : `新建槽位 · ${space.name}`} onClose={onClose}
    foot={<><Btn onClick={onClose}>取消</Btn> <Btn p id="slotSave" onClick={ok}>{inline ? '创建并选中' : '保存'}</Btn></>}>
    <div className="grid g2">
      <div className="f"><label>英文名（本空间唯一，创建后不可改）*</label><input type="text" id="slN" value={f.name} onChange={set('name')} disabled={!!slot} placeholder="如 color" /></div>
      <div className="f"><label>显示名 *</label><input type="text" id="slD" value={f.display_name} onChange={set('display_name')} placeholder="如 颜色" /></div>
    </div>
    <div className="grid g2">
      <div className="f"><label>类型</label><select id="slT" value={f.type} onChange={set('type')}>{TYPES.map(t => <option key={t}>{t}</option>)}</select></div>
      {f.type === '枚举' && <div className="f"><label>可选值 *（逗号分隔）</label><input type="text" id="slO" value={f.options} onChange={set('options')} placeholder="如 红,黑,白" /></div>}
      {(f.type === '文件' || f.type === '图片') && <div className="muted" style={{ alignSelf: 'end', paddingBottom: 12 }}>值为文件链接；工具的文件入参只能映射到文件 / 图片槽位</div>}
    </div>
    <div className="f"><label>默认追问话术 *</label><input type="text" id="slA" value={f.default_ask} onChange={set('default_ask')} placeholder="如：请问要什么颜色？" style={{ width: '100%' }} /></div>
    <div className="muted">意图里可覆盖追问话术；留空则继承这里的默认值。</div>
    <ErrBox lines={err} />
  </Modal>
}
