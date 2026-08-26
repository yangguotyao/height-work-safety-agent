<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { api } from '../api'
import TaskChainDrawer from '../components/TaskChainDrawer.vue'
import { useProjectStore } from '../stores/project'

interface Message {
  id?: string
  role: 'user' | 'assistant'
  content: string
  agent_name?: string
  metadata?: any
}

const projects = useProjectStore()
const logs = ref<any[]>([])
const selectedLog = ref<any | null>(null)
const logDate = ref(new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai' }).format(new Date()))
const generating = ref(false)
const logError = ref('')
const chainTaskId = ref<string | null>(null)
const conversations = ref<any[]>([])
const conversationId = ref<string | null>(null)
const messages = ref<Message[]>([])
const input = ref('')
const sending = ref(false)
const chatError = ref('')
const chatEl = ref<HTMLElement | null>(null)
const examples = [
  '解释今天风险等级最高的任务',
  '查询脚手架拆除相关项目依据',
  '生成今天的高处作业安全日志',
  '联网查询今年人工智能有什么新进展？'
]

const summary = computed(() => selectedLog.value?.content?.summary || {})
const tasks = computed(() => selectedLog.value?.content?.tasks || [])
const auditFindings = computed(() => selectedLog.value?.content?.audit_findings || [])
const evidenceSources = computed(() => selectedLog.value?.content?.evidence_sources || [])

function riskLabel(level?: string) {
  return level === 'red' ? '红色' : level === 'yellow' ? '黄色' : '绿色'
}

function agentLabel(name?: string) {
  return ({
    coordinator: '总协调 Agent', audit_agent: '方案审查 Agent', worker_agent: '工人助手 Agent',
    knowledge_agent: '安全知识图谱 Agent', risk_agent: '动态风险 Agent',
    safety_log_agent: '安全日志 Agent', web_agent: '联网问答 Agent'
  } as Record<string, string>)[name || ''] || '总协调 Agent'
}

function toolLabel(name: string) {
  return ({
    'risk.latest': '读取动态风险', 'audit.latest': '读取方案审查',
    'knowledge.search': '检索项目知识', 'knowledge.accident_search': '检索事故案例',
    'worker.safety_qa': '规范安全问答', 'worker.task_intake': '登记作业任务',
    'worker.learning_profile': '读取学习记录', 'worker.create_quiz': '生成场景测验',
    'safety_log.generate': '生成安全日志', 'safety_log.latest': '读取安全日志',
    'web.search': '博查联网搜索', 'system.overview': '读取项目概览'
  } as Record<string, string>)[name] || name
}

async function loadLogs(selectId?: string) {
  logs.value = await api('/api/v1/safety-logs')
  const target = selectId
    ? logs.value.find(item => item.id === selectId)
    : logs.value.find(item => item.assessment_date === logDate.value) || logs.value[0]
  selectedLog.value = target || null
}

async function generateLog() {
  if (generating.value) return
  generating.value = true
  logError.value = ''
  try {
    const result: any = await api('/api/v1/safety-logs', {
      method: 'POST', body: JSON.stringify({ assessment_date: logDate.value })
    })
    await loadLogs(result.id)
  } catch (cause) {
    logError.value = cause instanceof Error ? cause.message : '安全日志生成失败'
  } finally {
    generating.value = false
  }
}

function openLog(log: any) {
  if (!log) return
  selectedLog.value = log
  logDate.value = log.assessment_date
}

function selectLog(event: Event) {
  const id = (event.target as HTMLSelectElement).value
  openLog(logs.value.find(item => item.id === id))
}

function downloadLog() {
  if (!selectedLog.value) return
  window.location.href = `/api/v1/safety-logs/${encodeURIComponent(selectedLog.value.id)}/download`
}

async function loadConversations() {
  conversations.value = await api('/api/v1/agent/conversations')
}

async function newConversation() {
  conversationId.value = null
  messages.value = []
  input.value = ''
}

async function send(text?: string) {
  const content = (text ?? input.value).trim()
  if (!content || sending.value) return
  messages.value.push({ role: 'user', content })
  input.value = ''
  sending.value = true
  chatError.value = ''
  await nextTick()
  chatEl.value?.scrollTo({ top: chatEl.value.scrollHeight, behavior: 'smooth' })
  try {
    const result: any = await api('/api/v1/agent/messages', {
      method: 'POST',
      body: JSON.stringify({ conversation_id: conversationId.value, message: content })
    })
    conversationId.value = result.conversation_id
    messages.value.push(result.message)
    if (result.metadata?.tools?.includes('safety_log.generate')) await loadLogs()
    await loadConversations()
  } catch (cause) {
    chatError.value = cause instanceof Error ? cause.message : '问答请求失败'
    messages.value.pop()
  } finally {
    sending.value = false
    await nextTick()
    chatEl.value?.scrollTo({ top: chatEl.value.scrollHeight, behavior: 'smooth' })
  }
}

onMounted(async () => {
  try {
    await Promise.all([loadLogs(), loadConversations()])
  } catch (cause) {
    logError.value = cause instanceof Error ? cause.message : '页面数据加载失败'
  }
})
</script>

<template>
  <section class="page safety-log-page">
    <header class="page-heading">
      <div><p class="eyebrow">DAILY SAFETY LOG</p><h1>当日高处作业安全日志</h1><p>{{ projects.active?.name || '当前项目' }}</p></div>
      <div class="heading-actions">
        <input v-model="logDate" class="date-input" type="date" />
        <button class="btn btn-primary" :disabled="generating" @click="generateLog">{{ generating ? '正在汇总…' : '生成当日日志' }}</button>
      </div>
    </header>
    <div v-if="logError" class="error-banner">{{ logError }}</div>

    <section class="log-command card">
      <div class="command-copy">
        <span class="log-state" :class="{ ready: selectedLog }">{{ selectedLog ? `V${selectedLog.version}` : '待生成' }}</span>
        <div><b>{{ selectedLog ? `${selectedLog.assessment_date} 安全日志` : '选择日期并生成安全日志' }}</b><p v-if="!selectedLog">系统将汇总当前项目的审查、任务、知识和风险数据。</p></div>
      </div>
      <div class="command-actions">
        <select v-if="logs.length" class="history-select" :value="selectedLog?.id" @change="selectLog">
          <option v-for="item in logs" :key="item.id" :value="item.id">{{ item.assessment_date }} · V{{ item.version }} · {{ item.content?.summary?.task_count || 0 }}项任务</option>
        </select>
        <button class="btn btn-ghost" :disabled="!selectedLog" @click="downloadLog">下载 Word</button>
      </div>
    </section>

    <div v-if="selectedLog" class="log-metrics">
      <article><span>作业任务</span><strong>{{ summary.task_count || 0 }}</strong></article>
      <article class="red"><span>红色风险</span><strong>{{ summary.red_count || 0 }}</strong></article>
      <article class="yellow"><span>黄色风险</span><strong>{{ summary.yellow_count || 0 }}</strong></article>
      <article class="green"><span>绿色风险</span><strong>{{ summary.green_count || 0 }}</strong></article>
      <article><span>关联审查问题</span><strong>{{ summary.audit_finding_count || 0 }}</strong></article>
    </div>

    <div class="safety-workspace">
      <main class="log-preview card">
        <div v-if="!selectedLog" class="blank-log"><span>LOG</span><h2>当前日期还没有安全日志</h2><p>生成后可在这里查看风险清单、方案关联问题、培训建议和追溯依据。</p><button class="btn btn-primary" @click="generateLog">生成 {{ logDate }} 日志</button></div>
        <template v-else>
          <header class="document-head"><div><small>项目安全过程记录</small><h2>{{ selectedLog.project_name }}</h2></div><div><b>{{ selectedLog.assessment_date }}</b><span>版本 V{{ selectedLog.version }}</span></div></header>
          <section class="agent-flow"><div v-for="(module,index) in selectedLog.content.source_modules" :key="module"><span>{{ String(Number(index) + 1).padStart(2, '0') }}</span><b>{{ module }}</b><i>已汇总</i></div></section>

          <section class="log-section">
            <div class="section-title"><div><small>01 / TASK & RISK</small><h3>当日作业与风险清单</h3></div><span>{{ tasks.length }} 项</span></div>
            <div v-if="tasks.length" class="task-log-list">
              <article v-for="task in tasks" :key="task.task_id" :class="['task-log', task.risk_level]">
                <header><div><span class="risk-level">{{ riskLabel(task.risk_level) }}</span><h4>{{ task.normalized_task }}</h4></div><strong>{{ task.priority_score }}</strong></header>
                <p>{{ task.work_time }} · {{ task.work_location || '未填写位置' }} · {{ task.work_floor || '不按楼层定位' }}</p>
                <div class="task-detail-grid"><section><b>主要风险</b><ul><li v-for="item in task.main_risks.slice(0,4)" :key="item">{{ item }}</li></ul></section><section><b>作业前检查</b><ul><li v-for="item in task.pre_job_checks.slice(0,4)" :key="item">{{ item }}</li></ul></section><section><b>禁止行为</b><ul><li v-for="item in task.prohibited_behaviors.slice(0,4)" :key="item">{{ item }}</li></ul></section></div>
                <footer><span>{{ task.risk_summary }}</span><button @click="chainTaskId = task.task_id">查看任务全链路 →</button></footer>
              </article>
            </div>
            <p v-else class="section-empty">当日暂无已登记的高处作业任务。</p>
          </section>

          <section class="log-section">
            <div class="section-title"><div><small>02 / AUDIT</small><h3>关联方案审查问题</h3></div><span>{{ auditFindings.length }} 项</span></div>
            <div v-if="auditFindings.length" class="finding-list"><article v-for="item in auditFindings" :key="item.id"><b>{{ item.scene }} · {{ item.effective_result }}</b><p>{{ item.issue }}</p><span>{{ item.suggestion }}</span></article></div>
            <p v-else class="section-empty">当日任务未匹配到相关方案审查问题。</p>
          </section>

          <div class="two-log-sections">
            <section class="log-section"><div class="section-title"><div><small>03 / LEARNING</small><h3>培训建议</h3></div></div><div class="learning-stats"><span><b>{{ selectedLog.content.learning.quiz_attempt_count }}</b>关联测验</span><span><b>{{ selectedLog.content.learning.active_wrong_count }}</b>待复习错题</span></div><ul class="plain-list"><li v-for="item in selectedLog.content.learning.recommendations" :key="item">{{ item }}</li></ul></section>
            <section class="log-section"><div class="section-title"><div><small>04 / EVIDENCE</small><h3>规范与事故依据</h3></div><span>{{ evidenceSources.length }} 条</span></div><div class="evidence-list"><article v-for="item in evidenceSources.slice(0,6)" :key="`${item.evidence_type}-${item.source_id}`"><b>{{ item.title || item.source_id }}</b><p>{{ item.quote }}</p></article></div></section>
          </div>
        </template>
      </main>

      <aside class="qa-panel card">
        <header><div><small>AI ASSISTANT</small><h2>智能小助手</h2></div><button @click="newConversation">＋ 新会话</button></header>
        <div ref="chatEl" class="qa-messages">
          <div v-if="!messages.length" class="qa-welcome"><h3>解释项目数据，也可以明确要求联网查询</h3><button v-for="item in examples" :key="item" @click="send(item)">{{ item }}<i>↗</i></button></div>
          <article v-for="(message,index) in messages" :key="message.id || index" :class="['qa-message', message.role]"><small v-if="message.role === 'assistant'">{{ agentLabel(message.agent_name) }}</small><p>{{ message.content }}</p><div v-if="message.metadata?.tools" class="qa-tools"><span v-for="tool in message.metadata.tools" :key="tool">{{ toolLabel(tool) }}</span></div><div v-if="message.metadata?.results?.[0]?.items" class="web-sources"><a v-for="source in message.metadata.results[0].items" :key="source.url" :href="source.url" target="_blank" rel="noreferrer">{{ source.title }} ↗</a></div></article>
          <article v-if="sending" class="qa-message assistant"><small>正在协同</small><p>正在读取项目工具与上下文…</p></article>
        </div>
        <div v-if="chatError" class="chat-error">{{ chatError }}</div>
        <form class="qa-composer" @submit.prevent="send()"><textarea v-model="input" rows="2" placeholder="询问日志、任务、风险、规范；通用问题可明确要求联网…" @keydown.enter.exact.prevent="send()"></textarea><button :disabled="sending || !input.trim()">发送</button></form>
      </aside>
    </div>
    <TaskChainDrawer :open="!!chainTaskId" :task-id="chainTaskId" @close="chainTaskId = null" />
  </section>
</template>

<style scoped>
.safety-log-page{max-width:1680px}.heading-actions{display:flex;gap:9px;align-items:center}.date-input,.history-select{border:1px solid var(--line);border-radius:11px;background:white;color:var(--ink);padding:10px 12px;font-size:12px}.log-command{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:17px 19px}.command-copy,.command-actions{display:flex;align-items:center;gap:12px}.log-state{min-width:58px;padding:9px 10px;border-radius:10px;background:#eef2f7;color:#687b90;text-align:center;font:800 11px monospace}.log-state.ready{background:#e9f4ff;color:#1768aa}.command-copy b{font-size:14px}.command-copy p{font-size:11px;color:var(--muted);margin:4px 0 0}.history-select{max-width:260px}.log-metrics{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin:12px 0}.log-metrics article{display:flex;align-items:center;justify-content:space-between;padding:14px 16px;border:1px solid var(--line);border-radius:14px;background:rgba(255,255,255,.8)}.log-metrics span{font-size:11px;color:var(--muted)}.log-metrics strong{font-size:24px;color:#183a5b}.log-metrics .red strong{color:var(--red)}.log-metrics .yellow strong{color:#c77b00}.log-metrics .green strong{color:var(--green)}.safety-workspace{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(350px,.75fr);gap:14px;align-items:start}.log-preview{min-width:0;min-height:520px;padding:20px}.blank-log{min-height:450px;display:grid;place-content:center;text-align:center}.blank-log>span{width:58px;height:58px;border-radius:17px;display:grid;place-items:center;margin:auto;background:#e9f4ff;color:#2563eb;font:900 12px monospace}.blank-log h2{font-size:22px;margin:18px 0 8px}.blank-log p{color:var(--muted);font-size:12px;max-width:470px}.blank-log button{justify-self:center;margin-top:8px}.document-head{display:flex;justify-content:space-between;gap:20px;padding-bottom:18px;border-bottom:2px solid #183a5b}.document-head small{font-size:10px;color:var(--muted)}.document-head h2{font-size:22px;margin:5px 0 0}.document-head>div:last-child{text-align:right}.document-head b,.document-head span{display:block}.document-head b{font-size:15px}.document-head span{font-size:11px;color:var(--muted);margin-top:4px}.agent-flow{display:grid;grid-template-columns:repeat(4,1fr);gap:7px;margin:15px 0}.agent-flow>div{padding:12px;border:1px solid #dce7f2;border-radius:11px;background:#f8fbfe}.agent-flow span,.agent-flow b,.agent-flow i{display:block}.agent-flow span{font:800 10px monospace;color:#6190bb}.agent-flow b{font-size:13px;margin:6px 0}.agent-flow i{font-style:normal;font-size:11px;color:#168167}.log-section{margin-top:14px;padding:18px;border:1px solid #dfe7f1;border-radius:15px;background:white}.section-title{display:flex;justify-content:space-between;gap:14px;align-items:flex-start;margin-bottom:13px}.section-title small{font:800 9px monospace;color:#5887b1}.section-title h3{font-size:15px;margin:4px 0 0}.section-title>span{padding:5px 8px;border-radius:8px;background:#eef4fa;color:#456987;font-size:10px}.task-log-list,.finding-list,.evidence-list{display:grid;gap:9px}.task-log{overflow:hidden;border:1px solid #dce5ef;border-left:4px solid var(--green);border-radius:13px;background:#fbfcfe}.task-log.yellow{border-left-color:var(--yellow)}.task-log.red{border-left-color:var(--red)}.task-log>header{display:flex;justify-content:space-between;gap:14px;padding:13px 14px 8px}.task-log>header>div{display:flex;align-items:center;gap:8px}.task-log h4{font-size:14px;margin:0}.task-log>header>strong{font-size:20px;color:#365a7d}.risk-level{padding:4px 7px;border-radius:7px;background:#eaf8f1;color:#217357;font-size:9px;font-weight:800}.yellow .risk-level{background:#fff5df;color:#a46400}.red .risk-level{background:#fff0ee;color:#a43b35}.task-log>p{font-size:11px;color:var(--muted);padding:0 14px;margin:0 0 9px}.task-detail-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;padding:0 12px 12px}.task-detail-grid section{padding:11px;border-radius:10px;background:#f3f7fb}.task-detail-grid b{font-size:12px}.task-detail-grid ul,.plain-list{padding-left:18px;margin:7px 0 0}.task-detail-grid li{font-size:12px;line-height:1.65;margin:4px 0}.plain-list li{font-size:10px;line-height:1.6;margin:3px 0}.task-log footer{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 14px;background:#edf3f8}.task-log footer span{font-size:11px;color:#4b657e}.task-log footer button{flex:none;border:0;background:transparent;color:#2563eb;font-size:10px;font-weight:750}.finding-list article,.evidence-list article{padding:11px 12px;border-radius:10px;background:#f5f8fb}.finding-list b,.evidence-list b{font-size:11px}.finding-list p,.evidence-list p{font-size:10px;line-height:1.65;color:#4f6478;margin:5px 0}.finding-list span{font-size:10px;color:#2563eb}.section-empty{color:var(--muted);font-size:11px;margin:5px 0}.two-log-sections{display:grid;grid-template-columns:1fr 1fr;gap:0 12px}.learning-stats{display:grid;grid-template-columns:1fr 1fr;gap:8px}.learning-stats span{padding:11px;border-radius:10px;background:#f3f7fb;font-size:10px;color:var(--muted)}.learning-stats b{display:block;font-size:21px;color:#183a5b;margin-bottom:3px}.qa-panel{position:sticky;top:18px;display:flex;flex-direction:column;height:clamp(560px,calc(100vh - 36px),720px);min-height:0;overflow:hidden}.qa-panel>header{display:flex;align-items:center;justify-content:space-between;padding:18px;border-bottom:1px solid var(--line)}.qa-panel>header small{font:800 9px monospace;color:#5887b1}.qa-panel>header h2{font-size:18px;margin:4px 0 0}.qa-panel>header button{border:1px solid var(--line);border-radius:9px;background:white;padding:7px 9px;font-size:10px}.qa-messages{flex:1;overflow:auto;padding:15px}.qa-welcome{display:grid;gap:8px;padding-top:8px}.qa-welcome h3{font-size:15px;line-height:1.55;margin:5px 0 6px}.qa-welcome button{display:flex;justify-content:space-between;gap:8px;padding:10px;border:1px solid var(--line);border-radius:10px;background:#f8fafc;text-align:left;font-size:10px}.qa-welcome i{font-style:normal;color:#2563eb}.qa-message{margin-bottom:13px}.qa-message small{font:800 9px monospace;color:#6787a5}.qa-message p{white-space:pre-wrap;font-size:11px;line-height:1.75;margin:4px 0;padding:10px 12px;border-radius:4px 12px 12px;background:#f0f4f8}.qa-message.user p{background:#e6f1fc}.qa-tools{display:flex;flex-wrap:wrap;gap:4px}.qa-tools span{padding:4px 6px;border-radius:6px;background:#edf3f8;color:#5d7185;font:700 8px monospace}.web-sources{display:grid;gap:4px;margin-top:6px}.web-sources a{font-size:9px;color:#2563eb;overflow-wrap:anywhere}.chat-error{margin:0 12px 7px;padding:9px;border-radius:9px;background:#fff0ee;color:#a13c36;font-size:10px}.qa-composer{display:grid;grid-template-columns:1fr auto;gap:7px;padding:12px;border-top:1px solid var(--line)}.qa-composer textarea{resize:none;border:1px solid var(--line);border-radius:10px;padding:9px;outline:0;font-size:11px}.qa-composer button{border:0;border-radius:9px;background:#183a5b;color:white;padding:0 13px;font-size:11px;font-weight:750}.qa-composer button:disabled{opacity:.5}.evidence-list{max-height:300px;overflow:auto}@media(max-width:1200px){.safety-workspace{grid-template-columns:1fr}.qa-panel{position:static;height:560px;min-height:560px}.log-metrics{grid-template-columns:repeat(3,1fr)}}@media(max-width:760px){.page-heading,.log-command{align-items:stretch;flex-direction:column}.heading-actions,.command-actions{display:grid;grid-template-columns:1fr 1fr}.history-select{max-width:none}.log-metrics{grid-template-columns:repeat(2,1fr)}.agent-flow,.task-detail-grid,.two-log-sections{grid-template-columns:1fr}.log-preview{padding:14px}.document-head{display:block}.document-head>div:last-child{text-align:left;margin-top:8px}.qa-panel{min-height:560px}.task-log footer{align-items:flex-start;flex-direction:column}}
</style>
