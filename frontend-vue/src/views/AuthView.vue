<script setup lang="ts">
import { ref } from 'vue'
import { useAuthStore } from '../stores/auth'

const emit = defineEmits<{ authenticated: [] }>()
const auth = useAuthStore()
const mode = ref<'login' | 'register'>('login')
const username = ref('Admin')
const password = ref('admin')
const confirmPassword = ref('')
const message = ref('')

async function login() {
  message.value = ''
  try {
    await auth.login(username.value.trim(), password.value)
    emit('authenticated')
  } catch { /* store shows the error */ }
}

async function register() {
  message.value = ''
  try {
    const result = await auth.register({
      username: username.value.trim(), password: password.value,
      confirm_password: confirmPassword.value
    })
    mode.value = 'login'
    message.value = result.message
    password.value = ''
    confirmPassword.value = ''
  } catch { /* store shows the error */ }
}

function switchMode(next: 'login' | 'register') {
  mode.value = next
  auth.error = ''
  message.value = ''
  if (next === 'login' && !username.value) username.value = 'Admin'
}
</script>

<template>
  <main class="auth-page">
    <section class="auth-visual" role="img" aria-label="高处作业全过程风险管控智能体施工安全主题图"></section>
    <section class="auth-panel">
      <div class="auth-card">
        <header><span class="mobile-logo">安</span><p>欢迎使用</p><h2>{{ mode === 'login' ? '登录安全工作台' : '创建系统账号' }}</h2><small v-if="mode === 'register'">注册完成后，登录并创建首个项目</small></header>
        <div class="auth-tabs"><button :class="{ active: mode === 'login' }" @click="switchMode('login')">登录</button><button :class="{ active: mode === 'register' }" @click="switchMode('register')">注册</button></div>
        <form v-if="mode === 'login'" @submit.prevent="login">
          <label>用户名<input v-model="username" autocomplete="username" maxlength="64"></label>
          <label>密码<input v-model="password" type="password" autocomplete="current-password" maxlength="200"></label>
          <p v-if="message" class="auth-success">{{ message }}</p><p v-if="auth.error" class="auth-error">{{ auth.error }}</p>
          <button class="auth-submit" :disabled="auth.busy || !username || !password">{{ auth.busy ? '正在登录…' : '登录' }}</button>
        </form>
        <form v-else @submit.prevent="register">
          <label>用户名<input v-model="username" autocomplete="username" maxlength="64" placeholder="字母、数字、点、横线或下划线"></label>
          <div class="password-row"><label>密码<input v-model="password" type="password" autocomplete="new-password" maxlength="200" placeholder="至少5个字符"></label><label>确认密码<input v-model="confirmPassword" type="password" autocomplete="new-password" maxlength="200"></label></div>
          <p v-if="auth.error" class="auth-error">{{ auth.error }}</p>
          <button class="auth-submit" :disabled="auth.busy || !username || password.length < 5 || password !== confirmPassword">{{ auth.busy ? '正在注册…' : '注册账号' }}</button>
        </form>
      </div>
    </section>
  </main>
</template>

<style scoped>
.auth-page{min-height:100vh;display:grid;grid-template-columns:minmax(0,2.1fr) minmax(390px,1fr);background:#f5f8fb;color:#122b43}.auth-visual{min-height:100vh;background:#0870c3 url('../../登录背景.png') left center/cover no-repeat}.mobile-logo{width:48px;height:48px;display:none;place-items:center;border-radius:15px;background:#30a98b;color:white;font-size:23px;font-weight:900;box-shadow:0 10px 30px rgba(33,184,146,.25)}.auth-panel{display:grid;place-items:center;padding:46px;background:white}.auth-card{width:min(440px,100%)}.auth-card header p{margin:0 0 8px;color:#3776a7;font-size:13px;font-weight:800}.auth-card h2{margin:0;font-size:28px}.auth-card header small{display:block;margin-top:9px;color:#75879a;font-size:13px}.auth-tabs{display:grid;grid-template-columns:1fr 1fr;margin:32px 0 24px;padding:4px;border-radius:12px;background:#edf2f7}.auth-tabs button{padding:10px;border:0;border-radius:9px;background:transparent;color:#788a9c;font-size:14px;font-weight:800}.auth-tabs button.active{background:white;color:#174f7b;box-shadow:0 3px 12px rgba(33,62,88,.1)}form{display:grid;gap:17px}label{display:grid;gap:7px;color:#334f67;font-size:13px;font-weight:750}input{width:100%;box-sizing:border-box;padding:13px 14px;border:1px solid #cbd8e4;border-radius:11px;outline:0;background:#fbfcfe;color:#17344f;font-size:14px}input:focus{border-color:#408dca;box-shadow:0 0 0 3px rgba(64,141,202,.12)}.password-row{display:grid;grid-template-columns:1fr 1fr;gap:10px}.auth-submit{margin-top:5px;padding:14px;border:0;border-radius:11px;background:linear-gradient(110deg,#163e60,#176f81);color:white;font-size:15px;font-weight:850;box-shadow:0 9px 22px rgba(20,69,94,.18)}.auth-submit:disabled{opacity:.5}.auth-error,.auth-success{margin:0;padding:10px 12px;border-radius:9px;font-size:12px}.auth-error{background:#fff0ee;color:#a13c36}.auth-success{background:#ecf9f4;color:#217258}@media(max-width:900px){.auth-page{grid-template-columns:1fr}.auth-visual{display:none}.auth-panel{min-height:100vh;padding:28px}.mobile-logo{display:grid;margin-bottom:20px}.password-row{grid-template-columns:1fr}}
</style>
