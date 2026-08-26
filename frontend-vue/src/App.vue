<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useProjectStore, type ProjectWorkspace } from './stores/project'

const projects = useProjectStore()
const mobileOpen = ref(false)
const projectDialog = ref(false)
const newProjectName = ref('')
const saving = ref(false)
const formError = ref('')
const deleteTarget = ref<ProjectWorkspace | null>(null)
const deleteError = ref('')
const renameTarget = ref<ProjectWorkspace | null>(null)
const renameName = ref('')
const renameError = ref('')
const navigation = [
  { to: '/', code: 'AI', label: '今日总览' },
  { to: '/audit', code: '01', label: '方案审查' },
  { to: '/worker', code: '02', label: '安全培训' },
  { to: '/risk', code: '03', label: '风险分析' },
  { to: '/agent', code: '04', label: '日志生成' }
]

onMounted(() => projects.load())

async function createProject() {
  const name = newProjectName.value.trim()
  if (name.length < 4) {
    formError.value = '请输入完整的项目名称'
    return
  }
  saving.value = true
  formError.value = ''
  try {
    await projects.create(name)
  } catch (cause) {
    formError.value = cause instanceof Error ? cause.message : '项目创建失败'
    saving.value = false
  }
}

function requestDelete(item: ProjectWorkspace) {
  deleteError.value = ''
  deleteTarget.value = item
}

function requestRename(item: ProjectWorkspace) {
  renameError.value = ''
  renameName.value = item.name
  renameTarget.value = item
}

async function confirmRename() {
  const name = renameName.value.trim()
  if (!renameTarget.value) return
  if (name.length < 4) {
    renameError.value = '请输入完整的项目名称'
    return
  }
  renameError.value = ''
  try {
    await projects.rename(renameTarget.value.id, name)
  } catch (cause) {
    renameError.value = cause instanceof Error ? cause.message : '项目名称修改失败'
  }
}

async function confirmDelete() {
  if (!deleteTarget.value) return
  deleteError.value = ''
  try {
    await projects.remove(deleteTarget.value.id)
    deleteTarget.value = null
  } catch (cause) {
    deleteError.value = cause instanceof Error ? cause.message : '项目删除失败'
  }
}
</script>

<template>
  <div v-if="!projects.ready" class="boot-screen">
    <div class="boot-mark">安</div>
    <p>正在加载项目安全空间</p>
  </div>
  <div v-else class="app-shell">
    <aside class="sidebar" :class="{ open: mobileOpen }">
      <div class="brand">
        <span class="brand-mark">安</span>
        <div><strong>高处作业安全审查与预警智能体</strong><small>HEIGHTWORK SAFETY AGENT</small></div>
      </div>
      <button class="project-chip" @click="projectDialog = true">
        <span class="pulse-dot"></span>
        <div><small>当前项目</small><b>{{ projects.active?.name || '项目未加载' }}</b></div>
        <span class="project-chevron">⌄</span>
      </button>
      <nav>
        <RouterLink
          v-for="item in navigation"
          :key="item.to"
          :to="item.to"
          @click="mobileOpen = false"
        >
          <span class="nav-code">{{ item.code }}</span><span>{{ item.label }}</span>
        </RouterLink>
      </nav>
      <div class="sidebar-bottom">
        <button class="workspace-card" @click="projectDialog = true">
          <span class="workspace-icon">项</span>
          <span><b>项目工作空间</b><small>{{ projects.projects.length }} 个项目</small></span>
          <em>管理</em>
        </button>
      </div>
    </aside>
    <main class="main-area">
      <header class="mobile-header">
        <button class="menu-button" @click="mobileOpen = !mobileOpen">☰</button>
          <strong>高处作业安全审查与预警智能体</strong><span class="status-pill">在线</span>
      </header>
      <div v-if="projects.error" class="workspace-error">{{ projects.error }}</div>
      <RouterView />
    </main>
    <button v-if="mobileOpen" class="scrim" aria-label="关闭菜单" @click="mobileOpen = false"></button>
  </div>

  <div v-if="projectDialog" class="project-overlay" @click.self="projectDialog = false">
    <section class="project-dialog" role="dialog" aria-modal="true" aria-label="项目工作空间">
      <header>
        <div><p class="eyebrow">PROJECT WORKSPACES</p><h2>切换项目</h2></div>
        <button class="dialog-close" aria-label="关闭" @click="projectDialog = false">×</button>
      </header>
      <div class="project-list">
        <div
          v-for="item in projects.projects"
          :key="item.id"
          class="project-entry"
          :class="{ active: item.id === projects.active?.id }"
        >
          <button
            class="project-select"
            :disabled="projects.switching || projects.deleting || projects.renaming"
            @click="projects.activate(item.id)"
          >
            <span class="project-avatar">{{ item.name.slice(0, 1) }}</span>
            <span><b>{{ item.name }}</b><small>{{ item.code }}</small></span>
            <em>{{ item.id === projects.active?.id ? '当前' : '切换' }}</em>
          </button>
          <div class="project-actions">
            <button
              class="project-rename"
              :disabled="projects.switching || projects.deleting || projects.renaming"
              :aria-label="`修改项目名称 ${item.name}`"
              @click="requestRename(item)"
            >改名</button>
            <button
              class="project-delete"
              :disabled="item.id === projects.active?.id || projects.projects.length <= 1 || projects.switching || projects.deleting || projects.renaming"
              :title="item.id === projects.active?.id ? '请先切换到其他项目' : '删除项目及全部资料'"
              :aria-label="`删除项目 ${item.name}`"
              @click="requestDelete(item)"
            >删除</button>
          </div>
        </div>
      </div>
      <form class="project-create" @submit.prevent="createProject">
        <label for="project-name">新建项目</label>
        <div><input id="project-name" v-model="newProjectName" class="input" maxlength="80" placeholder="输入新项目名称"><button class="btn btn-primary" :disabled="saving">{{ saving ? '创建中…' : '创建并进入' }}</button></div>
        <p v-if="formError || projects.error" class="form-error">{{ formError || projects.error }}</p>
      </form>
    </section>
  </div>

  <div v-if="renameTarget" class="project-overlay rename-overlay" @click.self="renameTarget = null">
    <section class="rename-dialog" role="dialog" aria-modal="true" aria-labelledby="rename-project-title">
      <p class="eyebrow">RENAME PROJECT</p>
      <h2 id="rename-project-title">修改项目名称</h2>
      <p>项目资料、历史记录和风险版本不会改变。</p>
      <label for="rename-project-name">新的项目名称</label>
      <input id="rename-project-name" v-model="renameName" class="input" maxlength="80" @keydown.enter="confirmRename">
      <p v-if="renameError" class="form-error">{{ renameError }}</p>
      <div class="rename-actions">
        <button class="btn btn-ghost" :disabled="projects.renaming" @click="renameTarget = null">取消</button>
        <button class="btn btn-primary" :disabled="projects.renaming || renameName.trim().length < 4" @click="confirmRename">{{ projects.renaming ? '保存中…' : '保存名称' }}</button>
      </div>
    </section>
  </div>

  <div v-if="deleteTarget" class="project-overlay delete-confirm-overlay" @click.self="deleteTarget = null">
    <section class="delete-confirm" role="alertdialog" aria-modal="true" aria-labelledby="delete-project-title">
      <span class="delete-warning-icon">!</span>
      <p class="eyebrow">DELETE PROJECT</p>
      <h2 id="delete-project-title">确定删除这个项目吗？</h2>
      <p>即将删除 <strong>{{ deleteTarget.name }}</strong>，该项目的方案、审查记录、任务、学习记录、风险分析和上传资料将一并删除。</p>
      <div class="delete-warning">删除后无法恢复。建议确认已不再需要这些资料。</div>
      <p v-if="deleteError" class="form-error">{{ deleteError }}</p>
      <div class="delete-confirm-actions">
        <button class="btn" :disabled="projects.deleting" @click="deleteTarget = null">取消</button>
        <button class="btn btn-danger" :disabled="projects.deleting" @click="confirmDelete">{{ projects.deleting ? '正在删除…' : '确认删除' }}</button>
      </div>
    </section>
  </div>
</template>
