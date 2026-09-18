import { defineStore } from 'pinia'
import { api, ApiError } from '../api'

export interface AccountUser {
  id: string
  username: string
  display_name: string
  system_role: string
  project_id?: string | null
  project_name?: string
  project_role?: string
}

export const useAuthStore = defineStore('auth', {
  state: () => ({
    ready: false,
    busy: false,
    user: null as AccountUser | null,
    needsProject: false,
    users: [] as any[],
    error: ''
  }),
  actions: {
    async load() {
      this.error = ''
      try {
        const result = await api<any>('/api/v1/auth/me')
        this.user = result.user
        this.needsProject = Boolean(result.needs_project)
      } catch (cause) {
        if (!(cause instanceof ApiError) || cause.status !== 401) {
          this.error = cause instanceof Error ? cause.message : '账号状态加载失败'
        }
        this.user = null
        this.needsProject = false
      } finally {
        this.ready = true
      }
    },
    async login(username: string, password: string) {
      this.busy = true
      this.error = ''
      try {
        const result = await api<any>('/api/v1/auth/login', {
          method: 'POST', body: JSON.stringify({ username, password })
        })
        this.user = result.user
        this.needsProject = Boolean(result.needs_project)
      } catch (cause) {
        this.error = cause instanceof Error ? cause.message : '登录失败'
        throw cause
      } finally { this.busy = false }
    },
    async register(payload: Record<string, string>) {
      this.busy = true
      this.error = ''
      try {
        return await api<any>('/api/v1/auth/register', {
          method: 'POST', body: JSON.stringify(payload)
        })
      } catch (cause) {
        this.error = cause instanceof Error ? cause.message : '注册失败'
        throw cause
      } finally { this.busy = false }
    },
    async logout() {
      try { await api('/api/v1/auth/logout', { method: 'POST' }) } catch { /* session may already be invalid */ }
      this.user = null
      this.needsProject = false
      this.users = []
    },
    async loadUsers() {
      this.users = await api<any[]>('/api/v1/auth/users')
    },
    async setUserActive(userId: string, active: boolean) {
      await api(`/api/v1/auth/users/${encodeURIComponent(userId)}/status`, {
        method: 'PATCH', body: JSON.stringify({ active })
      })
      await this.loadUsers()
    },
    async resetPassword(userId: string, password: string) {
      await api(`/api/v1/auth/users/${encodeURIComponent(userId)}/reset-password`, {
        method: 'POST', body: JSON.stringify({ password })
      })
    }
  }
})
