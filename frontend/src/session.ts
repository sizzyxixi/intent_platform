import { createContext, useContext } from 'react'

export type User = { id: number; username: string; display_name: string; role: 'admin' | 'editor' | 'viewer'; role_label: string }
export type Space = { id: string; name: string; description: string; bot: string; code_prefix: string; version: number;
  intent_count: number; enabled_count: number; recognition: any }

export type Session = {
  user: User
  spaces: Space[]
  space: Space
  setSpace: (id: string) => void
  reloadSpaces: () => Promise<void>
  canEdit: boolean
  isAdmin: boolean
  go: (hash: string) => void
}
export const SessionCtx = createContext<Session>(null as any)
export const useSession = () => useContext(SessionCtx)
