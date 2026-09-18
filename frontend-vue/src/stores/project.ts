import { defineStore } from 'pinia'
import { api } from '../api'

export interface ProjectWorkspace {
  id: string
  name: string
  code: string
  city?: string
  address?: string
  created_at: string
  updated_at: string
  active?: boolean
}

interface ProjectCatalog {
  active_project_id: string | null
  active_project: ProjectWorkspace | null
  projects: ProjectWorkspace[]
}

export const useProjectStore = defineStore('project', {
  state: () => ({
    ready: false,
    switching: false,
    deleting: false,
    renaming: false,
    active: null as ProjectWorkspace | null,
    projects: [] as ProjectWorkspace[],
    error: ''
  }),
  actions: {
    async load() {
      this.error = ''
      try {
        const catalog = await api<ProjectCatalog>('/api/v1/projects')
        this.active = catalog.active_project
        this.projects = catalog.projects
      } catch (cause) {
        this.error = cause instanceof Error ? cause.message : '项目加载失败'
      } finally {
        this.ready = true
      }
    },
    async create(name: string, city: string, address = '') {
      const project = await api<ProjectWorkspace>('/api/v1/projects', {
        method: 'POST',
        body: JSON.stringify({ name, city, address })
      })
      window.location.reload()
    },
    async activate(projectId: string) {
      if (projectId === this.active?.id || this.switching) return
      this.switching = true
      this.error = ''
      try {
        await api(`/api/v1/projects/${encodeURIComponent(projectId)}/activate`, {
          method: 'POST'
        })
        window.location.reload()
      } catch (cause) {
        this.error = cause instanceof Error ? cause.message : '项目切换失败'
        this.switching = false
        throw cause
      }
    },
    async remove(projectId: string) {
      if (projectId === this.active?.id || this.deleting) return
      this.deleting = true
      this.error = ''
      try {
        await api(`/api/v1/projects/${encodeURIComponent(projectId)}`, {
          method: 'DELETE'
        })
        await this.load()
      } catch (cause) {
        this.error = cause instanceof Error ? cause.message : '项目删除失败'
        throw cause
      } finally {
        this.deleting = false
      }
    },
    async rename(projectId: string, name: string) {
      if (this.renaming) return
      this.renaming = true
      this.error = ''
      try {
        await api(`/api/v1/projects/${encodeURIComponent(projectId)}`, {
          method: 'PATCH',
          body: JSON.stringify({ name })
        })
        window.location.reload()
      } catch (cause) {
        this.error = cause instanceof Error ? cause.message : '项目名称修改失败'
        throw cause
      } finally {
        this.renaming = false
      }
    }
  }
})
