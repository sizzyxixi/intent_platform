import { useCallback, useEffect, useState } from 'react'
import { api, errLines, setUnauthorizedHandler } from './api'
import { Session, SessionCtx, Space, User } from './session'
import { Btn, ErrBox, Modal, ToastHost, toast } from './ui'
import IntentsPage from './pages/Intents'
import KbPage from './pages/Kb'
import ToolsPage from './pages/Tools'
import SystemPage from './pages/System'

function useHash() {
  const [h, setH] = useState(window.location.hash || '#/intents')
  useEffect(() => {
    const f = () => setH(window.location.hash || '#/intents')
    window.addEventListener('hashchange', f)
    return () => window.removeEventListener('hashchange', f)
  }, [])
  return h
}

function Login({ onOk }: { onOk: (u: User) => void }) {
  const [u, setU] = useState('')
  const [p, setP] = useState('')
  const [err, setErr] = useState<string[]>()
  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    try {
      const r = await api.post('/auth/login', { username: u, password: p })
      onOk(r.user)
    } catch (x) { setErr(errLines(x)) }
  }
  return <div className="login"><form className="box" onSubmit={submit}>
    <h1>意图管理平台</h1><p className="desc">请使用平台账号登录</p>
    <div className="f"><label>用户名</label><input type="text" id="lgU" value={u} onChange={e => setU(e.target.value)} autoFocus /></div>
    <div className="f"><label>密码</label><input type="password" id="lgP" value={p} onChange={e => setP(e.target.value)} style={{ width: '100%' }} /></div>
    <ErrBox lines={err} />
    <Btn p type="submit" id="lgBtn">登录</Btn>
  </form></div>
}

function NewSpace({ onClose, onDone }: { onClose: () => void; onDone: (id: string) => void }) {
  const [f, setF] = useState({ id: '', name: '', description: '', bot: '', code_prefix: '' })
  const [err, setErr] = useState<string[]>()
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value })
  const ok = async () => {
    try { const r = await api.post('/spaces', f); onDone(r.id) } catch (e) { setErr(errLines(e)) }
  }
  return <Modal title="新建业务空间" onClose={onClose} foot={<><Btn onClick={onClose}>取消</Btn> <Btn p onClick={ok}>创建</Btn></>}>
    <div className="grid g2">
      <div className="f"><label>空间 ID *（小写英文，创建后不可改）</label><input type="text" id="spId" value={f.id} onChange={set('id')} placeholder="如 pack" /></div>
      <div className="f"><label>意图编码前缀 *（1～4 位大写字母）</label><input type="text" id="spPx" value={f.code_prefix} onChange={e => setF({ ...f, code_prefix: e.target.value.toUpperCase() })} placeholder="如 P" /></div>
    </div>
    <div className="f"><label>名称 *</label><input type="text" id="spN" value={f.name} onChange={set('name')} placeholder="如：包装设计助手" /></div>
    <div className="f"><label>描述</label><input type="text" id="spD" value={f.description} onChange={set('description')} /></div>
    <div className="f"><label>对应 Bot / 智能体 *</label><input type="text" id="spB" value={f.bot} onChange={set('bot')} placeholder="如：Moonclaw 包装智能体" /></div>
    <div className="muted">新空间的意图、意图分类、槽位、导入记录、运行时配置和版本都独立；知识库和工具全平台通用，可直接关联。新空间默认带一个「通用 / 默认」分类。</div>
    <ErrBox lines={err} />
  </Modal>
}

export default function App() {
  const [user, setUser] = useState<User | null | undefined>(undefined)
  const [spaces, setSpaces] = useState<Space[]>([])
  const [spaceId, setSpaceId] = useState<string>(() => localStorage.getItem('ip_space') || '')
  const [newSp, setNewSp] = useState(false)
  const hash = useHash()

  useEffect(() => {
    setUnauthorizedHandler(() => setUser(null))
    api.get('/auth/me').then(setUser).catch(() => setUser(null))
  }, [])
  const reloadSpaces = useCallback(async () => { setSpaces(await api.get('/spaces')) }, [])
  useEffect(() => { if (user) reloadSpaces() }, [user, reloadSpaces])

  if (user === undefined) return <div className="loading">加载中…</div>
  if (user === null) return <><Login onOk={setUser} /><ToastHost /></>
  if (!spaces.length) return <div className="loading">加载中…</div>
  const space = spaces.find(s => s.id === spaceId) || spaces[0]
  const setSpace = (id: string) => { localStorage.setItem('ip_space', id); setSpaceId(id) }
  const go = (h: string) => { window.location.hash = h }
  const sess: Session = { user, spaces, space, setSpace, reloadSpaces, canEdit: user.role !== 'viewer',
    isAdmin: user.role === 'admin', go }
  const [, menu, sub] = hash.split('?')[0].split('/')
  const page = ['intents', 'kb', 'tools', 'system'].includes(menu) ? menu : 'intents'
  const MENU: [string, string][] = [['intents', '意图管理'], ['kb', '知识库'], ['tools', '工具中心']]
  const crumb = page === 'system' ? '用户与审计' : MENU.find(m => m[0] === page)![1]
  const logout = async () => { await api.post('/auth/logout'); setUser(null) }

  return <SessionCtx.Provider value={sess}>
    <div className="app">
      <aside className="side"><div className="logo">意图管理平台</div>
        <div className="menu" id="menu" style={{ paddingTop: 8 }}>
          {MENU.map(([k, n]) => <div key={k} className={'mi' + (page === k ? ' on' : '')} onClick={() => go('#/' + k)}>{n}</div>)}
        </div>
        <div className="me" id="me">
          <div style={{ marginBottom: 4 }}>{user.display_name}（{user.role_label}）</div>
          {sess.isAdmin && <a onClick={() => go('#/system')} id="sysLink">用户与审计</a>}
          <a onClick={logout} id="logout">退出</a>
        </div>
      </aside>
      <div className="main">
        <div className="top">
          <div className="crumb" id="crumb">意图管理平台 / <b>{crumb}</b></div>
          {page === 'intents' && <div id="spaceSw"><span className="muted">业务空间</span>
            <select id="spSel" value={space.id} onChange={e => { setSpace(e.target.value); toast('已切换到「' + spaces.find(s => s.id === e.target.value)!.name + '」') }}>
              {spaces.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
            {sess.isAdmin && <Btn onClick={() => setNewSp(true)} id="spNew">＋ 新建空间</Btn>}</div>}
          {page === 'kb' && <span className="muted">知识库全平台通用，不区分业务空间</span>}
          {page === 'tools' && <span className="muted">工具中心全平台通用，不区分业务空间</span>}
        </div>
        <div className="content" id="content">
          {page === 'intents' && <IntentsPage key={space.id} tab={sub || 'list'} />}
          {page === 'kb' && <KbPage kbId={sub || ''} />}
          {page === 'tools' && <ToolsPage tab={sub || 'list'} />}
          {page === 'system' && (sess.isAdmin ? <SystemPage /> : <div className="err">仅管理员可访问</div>)}
        </div>
      </div>
    </div>
    {newSp && <NewSpace onClose={() => setNewSp(false)} onDone={async id => { setNewSp(false); await reloadSpaces(); setSpace(id); toast('已创建并切换到新空间') }} />}
    <ToastHost />
  </SessionCtx.Provider>
}
