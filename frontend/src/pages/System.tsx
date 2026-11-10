import { useState } from 'react'
import { api, errLines, fmt, qs } from '../api'
import { useSession } from '../session'
import { Badge, Btn, ErrBox, Modal, Pager, toast, useAsync } from '../ui'

const ROLES: [string, string][] = [['admin', '管理员'], ['editor', '编辑'], ['viewer', '只读']]
const ENT: [string, string][] = [['', '全部对象'], ['intent', '意图'], ['slot', '槽位'], ['space', '空间'], ['kb', '知识库'], ['document', '文档'], ['chunk', '切片'], ['tool', '工具'], ['api_key', 'API Key'], ['user', '用户'], ['import', '导入']]

export default function SystemPage() {
  const [tab, setTab] = useState('users')
  return <>
    <h2>用户与审计</h2>
    <p className="desc">管理员可在此管理控制台账号（管理员 / 编辑 / 只读）并查看全部写操作的审计日志（控制台与开放接口、MCP 调用都会记录）。</p>
    <div className="tabs" style={{ margin: '0 0 14px' }}>{[['users', '用户'], ['audit', '审计日志']].map(([k, l]) => <div key={k} className={tab === k ? 'on' : ''} onClick={() => setTab(k)}>{l}</div>)}</div>
    {tab === 'users' ? <Users /> : <Audit />}
  </>
}

function Users() {
  const { user } = useSession()
  const [list, reload] = useAsync(() => api.get('/users'), [])
  const [edit, setEdit] = useState<any>(null)
  const patch = async (u: any, b: any, msg: string) => { try { await api.patch('/users/' + u.id, b); toast(msg); reload() } catch (e) { toast(errLines(e)[0]) } }
  return <div className="card">
    <div style={{ marginBottom: 10 }}><Btn p id="userNew" onClick={() => setEdit({})}>＋ 新建用户</Btn></div>
    <table id="userTbl"><thead><tr><th>用户名</th><th>显示名</th><th>角色</th><th>状态</th><th>创建时间</th><th>最近登录</th><th>操作</th></tr></thead><tbody>
      {!list ? <tr><td colSpan={7} className="loading">加载中…</td></tr> : list.map((u: any) => <tr key={u.id}>
        <td className="mono">{u.username}</td><td>{u.display_name}</td>
        <td><select value={u.role} onChange={e => patch(u, { role: e.target.value }, '角色已修改，该用户需重新登录')}>{ROLES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></td>
        <td><Badge c={u.is_active ? 'g' : 'n'}>{u.is_active ? '启用' : '停用'}</Badge></td><td className="muted">{fmt(u.created_at, false)}</td><td className="muted">{fmt(u.last_login_at, false)}</td>
        <td style={{ whiteSpace: 'nowrap' }}><a className="lk" onClick={() => setEdit(u)}>编辑 / 重置密码</a>
          {u.id !== user.id && <a className="lk" onClick={() => patch(u, { is_active: !u.is_active }, u.is_active ? '已停用' : '已启用')}>{u.is_active ? '停用' : '启用'}</a>}</td></tr>)}
    </tbody></table>
    <div className="muted" style={{ marginTop: 6 }}>修改角色、停用或重置密码后，该用户已登录的会话立即失效。系统至少保留一个启用的管理员。</div>
    {edit && <UserForm u={edit.id ? edit : null} onClose={() => setEdit(null)} onDone={() => { setEdit(null); reload() }} />}
  </div>
}

function UserForm({ u, onClose, onDone }: { u: any | null; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ username: u?.username || '', display_name: u?.display_name || '', role: u?.role || 'editor', password: '' })
  const [err, setErr] = useState<string[]>()
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value })
  const ok = async () => {
    try {
      if (u) await api.patch('/users/' + u.id, { display_name: f.display_name, role: f.role, ...(f.password ? { password: f.password } : {}) })
      else await api.post('/users', f)
      toast(u ? '已保存' : '用户已创建'); onDone()
    } catch (e) { setErr(errLines(e)) }
  }
  return <Modal title={u ? `编辑用户 · ${u.username}` : '新建用户'} onClose={onClose} foot={<><Btn onClick={onClose}>取消</Btn> <Btn p id="uSave" onClick={ok}>保存</Btn></>}>
    <div className="grid g2"><div className="f"><label>用户名 *</label><input type="text" id="uN" value={f.username} disabled={!!u} onChange={set('username')} /></div>
      <div className="f"><label>显示名</label><input type="text" id="uD" value={f.display_name} onChange={set('display_name')} placeholder="如：运营-林夏" /></div></div>
    <div className="grid g2"><div className="f"><label>角色</label><select id="uR" value={f.role} onChange={set('role')}>{ROLES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></div>
      <div className="f"><label>{u ? '新密码（留空不修改）' : '初始密码 *'}（至少 8 位）</label><input type="password" id="uP" value={f.password} onChange={set('password')} autoComplete="new-password" /></div></div>
    <div className="muted">管理员：全部功能，含用户、API Key；编辑：维护意图、槽位、知识库、工具；只读：只能查看与测试。</div>
    <ErrBox lines={err} />
  </Modal>
}

function Audit() {
  const { spaces } = useSession()
  const [f, setF] = useState({ entity_type: '', space_id: '', q: '' })
  const [qIn, setQIn] = useState('')
  const [page, setPage] = useState(1)
  const [data] = useAsync(() => api.get('/audit' + qs({ ...f, page, page_size: 50 })), [f, page])
  return <div className="card">
    <div className="row" style={{ marginBottom: 10 }}>
      <select id="auE" value={f.entity_type} onChange={e => { setF({ ...f, entity_type: e.target.value }); setPage(1) }}>{ENT.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
      <select id="auS" value={f.space_id} onChange={e => { setF({ ...f, space_id: e.target.value }); setPage(1) }}><option value="">全部空间</option>{spaces.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select>
      <input type="text" id="auQ" value={qIn} onChange={e => setQIn(e.target.value)} onKeyDown={e => e.key === 'Enter' && (setF({ ...f, q: qIn.trim() }), setPage(1))} placeholder="搜索摘要 / 对象 / 操作人" style={{ width: 240 }} />
      <Btn onClick={() => { setF({ ...f, q: qIn.trim() }); setPage(1) }}>筛选</Btn></div>
    <table id="auditTbl"><thead><tr><th>时间</th><th>操作人</th><th>动作</th><th>对象</th><th>空间</th><th>摘要</th></tr></thead><tbody>
      {!data ? <tr><td colSpan={6} className="loading">加载中…</td></tr> : data.items.length ? data.items.map((r: any) => <tr key={r.id}>
        <td className="muted" style={{ whiteSpace: 'nowrap' }}>{fmt(r.at)}</td><td>{r.actor}{r.actor_type !== 'user' && <span className="muted">（{r.actor_type === 'api_key' ? 'API' : r.actor_type}）</span>}</td>
        <td className="mono">{r.action}</td><td className="mono">{r.entity_type}:{r.entity_id}</td><td>{r.space_id || <span className="muted">—</span>}</td><td>{r.summary}</td></tr>)
        : <tr><td colSpan={6} className="muted" style={{ textAlign: 'center', padding: 30 }}>暂无记录</td></tr>}
    </tbody></table>
    {data && <Pager page={page} total={data.total} size={50} onPage={setPage} />}
  </div>
}
