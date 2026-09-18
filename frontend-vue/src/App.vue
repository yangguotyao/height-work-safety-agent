<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useProjectStore, type ProjectWorkspace } from './stores/project'
import { useAuthStore } from './stores/auth'
import SmartAssistant from './components/SmartAssistant.vue'
import AuthView from './views/AuthView.vue'

const projects = useProjectStore()
const auth = useAuthStore()
const mobileOpen = ref(false)
const projectDialog = ref(false)
const accountMenu = ref(false)
const accountDialog = ref(false)
const newProjectName = ref('')
const newProjectCity = ref('')
const newProjectAddress = ref('')
const saving = ref(false)
const formError = ref('')
const deleteTarget = ref<ProjectWorkspace | null>(null)
const deleteError = ref('')
const renameTarget = ref<ProjectWorkspace | null>(null)
const renameName = ref('')
const renameError = ref('')
const resetTarget = ref<any>(null)
const resetPassword = ref('')
const accountError = ref('')
const projectLoading = ref(false)
const booting = computed(() => !auth.ready || projectLoading.value)
const navigation = [
  { to: '/', code: 'AI', label: '今日总览' },
  { to: '/audit', code: '01', label: '施工方案审查' },
  { to: '/worker', code: '02', label: '班前风险分析' },
  { to: '/hazards', code: '03', label: '现场隐患巡检' },
  { to: '/rectification', code: '04', label: '隐患整改记录' },
  { to: '/agent', code: '05', label: '安全日志生成' }
]

async function loadProjectsForUser() {
  if (!auth.user || projectLoading.value) return
  projectLoading.value = true
  projects.ready = false
  try { await projects.load() }
  finally { projectLoading.value = false }
}
async function initialize() { await auth.load(); if (auth.user) await loadProjectsForUser() }
onMounted(initialize)
watch(() => auth.user?.id, (userId, previousId) => {
  if (userId && userId !== previousId) void loadProjectsForUser()
})
async function authenticated() { await loadProjectsForUser() }

async function createProject() {
  const name = newProjectName.value.trim()
  const city = newProjectCity.value.trim()
  if (name.length < 4 || city.length < 2) { formError.value = '请填写完整的项目名称和所在城市'; return }
  saving.value = true; formError.value = ''
  try { await projects.create(name, city, newProjectAddress.value.trim()) }
  catch (cause) { formError.value = cause instanceof Error ? cause.message : '项目创建失败'; saving.value = false }
}
function requestDelete(item: ProjectWorkspace) { deleteError.value = ''; deleteTarget.value = item }
function requestRename(item: ProjectWorkspace) { renameError.value = ''; renameName.value = item.name; renameTarget.value = item }
async function confirmRename() {
  const name = renameName.value.trim()
  if (!renameTarget.value || name.length < 4) { renameError.value = '请输入完整的项目名称'; return }
  try { await projects.rename(renameTarget.value.id, name) }
  catch (cause) { renameError.value = cause instanceof Error ? cause.message : '项目名称修改失败' }
}
async function confirmDelete() {
  if (!deleteTarget.value) return
  try { await projects.remove(deleteTarget.value.id); deleteTarget.value = null }
  catch (cause) { deleteError.value = cause instanceof Error ? cause.message : '项目删除失败' }
}
async function logout() { accountMenu.value = false; await auth.logout(); projects.$reset() }
async function openAccounts() {
  accountMenu.value = false; accountError.value = ''
  try { await auth.loadUsers(); accountDialog.value = true }
  catch (cause) { accountError.value = cause instanceof Error ? cause.message : '账号列表加载失败' }
}
async function toggleUser(item: any) {
  try { await auth.setUserActive(item.id, !item.active) }
  catch (cause) { accountError.value = cause instanceof Error ? cause.message : '账号状态修改失败' }
}
async function confirmResetPassword() {
  if (!resetTarget.value || resetPassword.value.length < 5) return
  try { await auth.resetPassword(resetTarget.value.id, resetPassword.value); resetTarget.value = null; resetPassword.value = '' }
  catch (cause) { accountError.value = cause instanceof Error ? cause.message : '密码重置失败' }
}
</script>

<template>
  <div v-if="booting" class="boot-screen"><div class="boot-mark">安</div><p>正在加载安全工作空间</p></div>
  <AuthView v-else-if="!auth.user" @authenticated="authenticated" />

  <main v-else-if="auth.needsProject || !projects.active" class="onboarding-page">
    <div class="onboarding-brand"><span>安</span><b>高处作业安全审查与预警智能体</b></div>
    <section class="onboarding-dialog"><p class="eyebrow">CREATE YOUR FIRST PROJECT</p><h1>创建首个项目</h1><p>账号已登录。创建项目后，方案、巡检、整改和日志都将保存在独立空间中。</p>
      <form @submit.prevent="createProject"><label>项目名称<input v-model="newProjectName" class="input" maxlength="80" placeholder="例如：武汉中心医院扩建项目"></label><label>所在城市<input v-model="newProjectCity" class="input" maxlength="60" placeholder="例如：武汉市"></label><label>项目地址概述（可选）<input v-model="newProjectAddress" class="input" maxlength="160" placeholder="例如：江岸区建设大道附近"></label><p v-if="formError" class="form-error">{{ formError }}</p><button class="btn btn-primary" :disabled="saving">{{ saving ? '正在创建项目…' : '创建并进入项目' }}</button></form>
      <button class="onboarding-logout" @click="logout">退出当前账号</button>
    </section>
  </main>

  <div v-else class="app-shell">
    <aside class="sidebar" :class="{ open: mobileOpen }">
      <div class="brand"><span class="brand-mark">安</span><div><strong>高处作业安全审查与预警智能体</strong><small>HEIGHTWORK SAFETY AGENT</small></div></div>
      <button class="project-chip" @click="projectDialog = true"><span class="pulse-dot"></span><div><small>当前项目</small><b>{{ projects.active?.name }}</b></div><span class="project-chevron">⌄</span></button>
      <nav><RouterLink v-for="item in navigation" :key="item.to" :to="item.to" @click="mobileOpen = false"><span class="nav-code">{{ item.code }}</span><span>{{ item.label }}</span></RouterLink></nav>
      <SmartAssistant />
      <div class="sidebar-bottom account-area"><button class="workspace-card" @click="accountMenu = !accountMenu"><span class="workspace-icon">{{ auth.user.display_name.slice(0,1) }}</span><span><b>{{ auth.user.display_name }}</b><small>{{ projects.active?.name }}</small></span><em>⌃</em></button><div v-if="accountMenu" class="account-menu"><button @click="projectDialog = true; accountMenu = false">项目管理</button><button v-if="auth.user.system_role === 'admin'" @click="openAccounts">账号管理</button><button @click="logout">切换账号</button><button class="logout" @click="logout">退出登录</button></div></div>
    </aside>
    <main class="main-area"><header class="mobile-header"><button class="menu-button" @click="mobileOpen = !mobileOpen">☰</button><strong>高处作业安全审查与预警智能体</strong><span class="status-pill">在线</span></header><div v-if="projects.error" class="workspace-error">{{ projects.error }}</div><RouterView /></main>
    <button v-if="mobileOpen" class="scrim" aria-label="关闭菜单" @click="mobileOpen = false"></button>
  </div>

  <div v-if="projectDialog" class="project-overlay" @click.self="projectDialog = false"><section class="project-dialog" role="dialog" aria-modal="true"><header><div><p class="eyebrow">PROJECT WORKSPACES</p><h2>项目管理</h2></div><button class="dialog-close" @click="projectDialog = false">×</button></header><div class="project-list"><div v-for="item in projects.projects" :key="item.id" class="project-entry" :class="{ active: item.id === projects.active?.id }"><button class="project-select" :disabled="projects.switching" @click="projects.activate(item.id)"><span class="project-avatar">{{ item.name.slice(0,1) }}</span><span><b>{{ item.name }}</b><small>{{ item.city || '未填写城市' }} · {{ item.code }}</small></span><em>{{ item.id === projects.active?.id ? '当前' : '切换' }}</em></button><div class="project-actions"><button class="project-rename" @click="requestRename(item)">改名</button><button class="project-delete" :disabled="item.id === projects.active?.id || projects.projects.length <= 1" @click="requestDelete(item)">删除</button></div></div></div><form class="project-create project-create-fields" @submit.prevent="createProject"><label>新建项目</label><input v-model="newProjectName" class="input" maxlength="80" placeholder="项目名称"><input v-model="newProjectCity" class="input" maxlength="60" placeholder="所在城市"><input v-model="newProjectAddress" class="input" maxlength="160" placeholder="地址概述（可选）"><button class="btn btn-primary" :disabled="saving">{{ saving ? '创建中…' : '创建并进入' }}</button><p v-if="formError" class="form-error">{{ formError }}</p></form></section></div>

  <div v-if="accountDialog" class="project-overlay" @click.self="accountDialog = false"><section class="project-dialog account-dialog"><header><div><p class="eyebrow">ACCOUNT MANAGEMENT</p><h2>账号管理</h2></div><button class="dialog-close" @click="accountDialog = false">×</button></header><p v-if="accountError" class="form-error">{{ accountError }}</p><div class="account-list"><article v-for="item in auth.users" :key="item.id"><span class="project-avatar">{{ item.display_name.slice(0,1) }}</span><div><b>{{ item.display_name }}</b><small>{{ item.username }} · {{ item.project_count }} 个项目</small></div><em :class="{ off: !item.active }">{{ item.active ? '正常' : '已停用' }}</em><button v-if="item.system_role !== 'admin'" @click="toggleUser(item)">{{ item.active ? '停用' : '启用' }}</button><button @click="resetTarget = item; resetPassword = ''">重置密码</button></article></div></section></div>
  <div v-if="resetTarget" class="project-overlay" @click.self="resetTarget = null"><section class="rename-dialog"><p class="eyebrow">RESET PASSWORD</p><h2>重置 {{ resetTarget.display_name }} 的密码</h2><label>新密码</label><input v-model="resetPassword" class="input" type="password" minlength="5" placeholder="至少5个字符"><div class="rename-actions"><button class="btn btn-ghost" @click="resetTarget = null">取消</button><button class="btn btn-primary" :disabled="resetPassword.length < 5" @click="confirmResetPassword">确认重置</button></div></section></div>
  <div v-if="renameTarget" class="project-overlay" @click.self="renameTarget = null"><section class="rename-dialog"><p class="eyebrow">RENAME PROJECT</p><h2>修改项目名称</h2><input v-model="renameName" class="input" maxlength="80" @keydown.enter="confirmRename"><p v-if="renameError" class="form-error">{{ renameError }}</p><div class="rename-actions"><button class="btn btn-ghost" @click="renameTarget = null">取消</button><button class="btn btn-primary" @click="confirmRename">保存名称</button></div></section></div>
  <div v-if="deleteTarget" class="project-overlay" @click.self="deleteTarget = null"><section class="delete-confirm"><span class="delete-warning-icon">!</span><p class="eyebrow">DELETE PROJECT</p><h2>确定删除这个项目吗？</h2><p>项目 <strong>{{ deleteTarget.name }}</strong> 的全部业务数据和上传资料将被删除。</p><p v-if="deleteError" class="form-error">{{ deleteError }}</p><div class="delete-confirm-actions"><button class="btn" @click="deleteTarget = null">取消</button><button class="btn btn-danger" @click="confirmDelete">确认删除</button></div></section></div>
</template>

<style scoped>
.onboarding-page{min-height:100vh;display:grid;place-items:center;position:relative;overflow:hidden;padding:30px;background:radial-gradient(circle at 20% 10%,#23799b 0,transparent 34%),linear-gradient(145deg,#081f34,#123f5d);color:white}.onboarding-page:before{content:"";position:absolute;inset:0;background-image:linear-gradient(rgba(255,255,255,.035) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.035) 1px,transparent 1px);background-size:42px 42px}.onboarding-brand{position:absolute;top:34px;left:42px;z-index:1;display:flex;align-items:center;gap:12px}.onboarding-brand span{width:43px;height:43px;display:grid;place-items:center;border-radius:13px;background:#30a98b;font-size:20px;font-weight:900}.onboarding-dialog{position:relative;z-index:1;width:min(510px,100%);padding:32px;border-radius:22px;background:white;color:#19354e;box-shadow:0 30px 80px rgba(0,0,0,.3)}.onboarding-dialog h1{margin:6px 0 10px;font-size:29px}.onboarding-dialog>p:not(.eyebrow){color:#6a7e91;font-size:13px;line-height:1.65}.onboarding-dialog form{display:grid;gap:14px;margin-top:22px}.onboarding-dialog label{display:grid;gap:7px;font-size:13px;font-weight:750}.onboarding-dialog .btn{margin-top:5px;padding:13px}.onboarding-logout{display:block;margin:18px auto 0;border:0;background:transparent;color:#66829b;font-size:12px}.account-area{position:relative}.account-menu{position:absolute;left:0;right:0;bottom:calc(100% + 9px);z-index:20;padding:7px;border:1px solid rgba(190,216,236,.2);border-radius:13px;background:#102c47;box-shadow:0 18px 45px rgba(0,0,0,.3)}.account-menu button{width:100%;padding:10px;border:0;border-radius:8px;background:transparent;color:#dceaf6;text-align:left;font-size:12px}.account-menu button:hover{background:rgba(255,255,255,.08)}.account-menu .logout{color:#ffb6ae}.project-create-fields{display:grid!important;grid-template-columns:1fr 1fr!important;gap:9px!important}.project-create-fields label,.project-create-fields p{grid-column:1/-1}.project-create-fields input:first-of-type,.project-create-fields input:last-of-type{grid-column:1/-1}.account-list{display:grid;gap:8px}.account-list article{display:grid;grid-template-columns:40px 1fr auto auto auto;gap:9px;align-items:center;padding:11px;border:1px solid #dde7f0;border-radius:12px;background:white}.account-list b,.account-list small{display:block}.account-list b{font-size:13px}.account-list small{margin-top:3px;color:#70859a;font-size:11px}.account-list em{color:#23745d;font-size:11px;font-style:normal;font-weight:800}.account-list em.off{color:#a34a43}.account-list button{padding:7px 8px;border:1px solid #d6e1ea;border-radius:8px;background:#f7fafc;color:#42627d;font-size:10px}@media(max-width:700px){.project-create-fields{grid-template-columns:1fr!important}.project-create-fields>*{grid-column:1!important}.account-list article{grid-template-columns:38px 1fr auto}.onboarding-brand{position:relative;top:auto;left:auto;margin-bottom:20px}.onboarding-page{align-content:center}.onboarding-dialog{box-sizing:border-box}}
</style>
