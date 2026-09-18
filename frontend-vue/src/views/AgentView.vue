<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import TaskChainDrawer from '../components/TaskChainDrawer.vue'
import { api } from '../api'
import { useProjectStore } from '../stores/project'

const projects = useProjectStore()
const logs = ref<any[]>([])
const selectedLog = ref<any | null>(null)
const logDate = ref(new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai' }).format(new Date()))
const generating = ref(false)
const logError = ref('')
const chainTaskId = ref<string | null>(null)

const content = computed(() => selectedLog.value?.content || {})
const summary = computed(() => content.value.summary || {})
const tasks = computed(() => content.value.tasks || [])
const planAudits = computed(() => content.value.plan_audits || [])
const planRevisions = computed(() => content.value.plan_revisions || [])
const inspections = computed(() => content.value.onsite_inspections || [])
const onsiteItems = computed(() => content.value.onsite_rectification || [])
const timeline = computed(() => content.value.timeline || [])
const openItems = computed(() => content.value.open_items || [])
const evidenceSources = computed(() => content.value.evidence_sources || [])
const weather = computed(() => content.value.weather || {})

const statusLabels: Record<string, string> = {
  pending_rectification: '待整改', rectifying: '整改中', pending_review: '待复核',
  closed: '已关闭', processing: '处理中', needs_revision: '需继续修订',
  completed: '已完成', analyzing: '分析中', accepted: '已确认', rejected: '不构成隐患'
}

function statusLabel(value?: string) {
  return statusLabels[value || ''] || value || '已记录'
}

function riskLabel(level?: string) {
  return level === 'red' ? '高风险' : level === 'yellow' ? '较高风险' : '一般风险'
}

function timeText(value?: string) {
  if (!value) return '—'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function actorText(value?: string) {
  return value && /qwen|模型|model/i.test(value) ? 'AI辅助识别' : value || '系统记录'
}

function revisionRemaining(item: any) {
  const comparison = item.comparison || {}
  return Math.max(Number(comparison.original_finding_count || 0) - Number(comparison.resolved_count || 0), 0)
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

function selectLog(event: Event) {
  const id = (event.target as HTMLSelectElement).value
  const selected = logs.value.find(item => item.id === id)
  if (!selected) return
  selectedLog.value = selected
  logDate.value = selected.assessment_date
}

function downloadLog() {
  if (selectedLog.value) window.location.href = `/api/v1/safety-logs/${encodeURIComponent(selectedLog.value.id)}/download`
}

onMounted(async () => {
  try { await loadLogs() }
  catch (cause) { logError.value = cause instanceof Error ? cause.message : '页面数据加载失败' }
})
</script>

<template>
  <section class="page safety-log-page">
    <header class="page-heading">
      <div><p class="eyebrow">SAFETY PROCESS LOG</p><h1>施工安全全过程日志</h1><p>{{ projects.active?.name || '当前项目' }} · 真实业务数据自动汇总</p></div>
      <div class="heading-actions"><input v-model="logDate" class="date-input" type="date" /><button class="btn btn-primary" :disabled="generating" @click="generateLog">{{ generating ? '正在汇总…' : '生成 / 更新日志' }}</button></div>
    </header>
    <div v-if="logError" class="error-banner">{{ logError }}</div>

    <section class="log-command card">
      <div class="command-copy"><span class="log-state" :class="{ ready: selectedLog }">{{ selectedLog ? `V${selectedLog.version}` : '待生成' }}</span><div><b>{{ selectedLog ? `${selectedLog.assessment_date} 全过程日志` : '选择日期并生成全过程日志' }}</b><p>{{ selectedLog ? '记录当日真实业务事件，并附带截至当日未闭环事项。' : '汇总方案审查、班前风险、现场巡检与整改复核。' }}</p></div></div>
      <div class="command-actions"><select v-if="logs.length" class="history-select" :value="selectedLog?.id" @change="selectLog"><option v-for="item in logs" :key="item.id" :value="item.id">{{ item.assessment_date }} · V{{ item.version }} · {{ item.content?.timeline?.length || 0 }}条事件</option></select><button class="btn btn-ghost" :disabled="!selectedLog" @click="downloadLog">下载 Word</button></div>
    </section>

    <template v-if="selectedLog">
      <div class="log-metrics">
        <article><span>方案审查/修订</span><strong>{{ summary.plan_activity_count || 0 }}</strong></article><article><span>班前风险分析</span><strong>{{ summary.task_count || 0 }}</strong></article><article><span>现场巡检</span><strong>{{ summary.inspection_count || 0 }}</strong></article><article><span>新增安全事项</span><strong>{{ summary.safety_item_count || 0 }}</strong></article><article class="closed"><span>当日关闭</span><strong>{{ summary.closed_safety_item_count || 0 }}</strong></article><article :class="{ attention: summary.open_item_count }"><span>当前未闭环</span><strong>{{ summary.open_item_count || 0 }}</strong></article>
      </div>

      <main class="log-preview card">
        <header class="document-head"><div><small>项目安全过程记录</small><h2>{{ selectedLog.project_name }}</h2></div><div><b>{{ selectedLog.assessment_date }}</b><span>日志版本 V{{ selectedLog.version }}</span></div></header>
        <section class="process-flow" aria-label="施工安全全过程"><div v-for="(module,index) in content.source_modules" :key="module"><span>{{ String(Number(index) + 1).padStart(2, '0') }}</span><b>{{ module }}</b><i v-if="Number(index) < content.source_modules.length - 1">→</i></div></section>

        <section class="log-section timeline-section">
          <div class="section-title"><div><small>01 / PROCESS</small><h3>当日安全事件时间线</h3></div><span>{{ timeline.length }} 条真实记录</span></div>
          <div v-if="timeline.length" class="timeline-list"><article v-for="(item,index) in timeline" :key="`${item.type}-${item.at}-${index}`"><div class="timeline-mark"><i></i><em></em></div><div class="timeline-main"><div><span class="stage-tag">{{ item.stage }}</span><b>{{ item.title }}</b><small>{{ timeText(item.at) }}</small></div><p>{{ item.detail }}</p><footer><span>记录人：{{ actorText(item.actor) }}</span><RouterLink :to="item.link">{{ item.status }} · 查看详情 →</RouterLink></footer></div></article></div>
          <p v-else class="section-empty">当日暂无安全过程事件。</p>
        </section>

        <section class="log-section">
          <div class="section-title"><div><small>02 / PLAN</small><h3>施工方案审查与整改</h3></div><span>{{ planAudits.length + planRevisions.length }} 项业务动作</span></div>
          <div class="plan-grid"><article v-for="item in planAudits" :key="item.id" class="business-card"><header><span>方案审查</span><RouterLink to="/audit">查看原方案 →</RouterLink></header><h4>{{ item.filename }}</h4><p>AI辅助审查形成 <b>{{ item.finding_count }}</b> 项方案问题。</p></article><article v-for="item in planRevisions" :key="item.id" class="business-card revision-card"><header><span>第 {{ item.attempt_no }} 次整改对比</span><RouterLink to="/audit">查看整改记录 →</RouterLink></header><h4>{{ item.revised_filename }}</h4><div class="revision-result"><b>{{ item.comparison?.original_finding_count || 0 }}<small>原问题</small></b><b class="ok">{{ item.comparison?.resolved_count || 0 }}<small>已解决</small></b><b class="warn">{{ revisionRemaining(item) }}<small>仍需修改</small></b></div><p v-if="item.outstanding_titles?.length">待修改：{{ item.outstanding_titles.join('、') }}</p></article></div>
          <p v-if="!planAudits.length && !planRevisions.length" class="section-empty">当日无施工方案审查或修订记录。</p>
        </section>

        <section class="log-section">
          <div class="section-title"><div><small>03 / PRE-JOB</small><h3>班前风险分析</h3></div><span>{{ tasks.length }} 项任务</span></div>
          <div v-if="weather.summary" class="weather-strip"><b>天气与环境</b><p>{{ weather.summary }}</p></div>
          <div v-if="tasks.length" class="task-log-list"><article v-for="task in tasks" :key="task.task_id" :class="['task-log', task.risk_level]"><header><div><span class="risk-level">{{ riskLabel(task.risk_level) }}</span><h4>{{ task.normalized_task }}</h4></div><button @click="chainTaskId = task.task_id">查看任务全链路 →</button></header><p>{{ task.work_time }} · {{ task.work_location || '未填写位置' }} · {{ task.team_ref || '未填写班组' }}</p><div class="task-detail-grid"><section><b>主要风险</b><ul><li v-for="item in task.main_risks.slice(0,3)" :key="item">{{ item }}</li></ul></section><section><b>关键控制措施</b><ul><li v-for="item in task.pre_job_checks.slice(0,3)" :key="item">{{ item }}</li></ul></section><section><b>天气与环境提示</b><ul><li v-for="item in task.weather_warnings.slice(0,3)" :key="item">{{ item }}</li><li v-if="!task.weather_warnings.length">未触发额外天气风险提示</li></ul></section></div></article></div>
          <p v-else class="section-empty">当日暂无班前风险分析任务。</p>
        </section>

        <section class="log-section">
          <div class="section-title"><div><small>04 / ONSITE</small><h3>现场隐患巡检与整改</h3></div><span>{{ inspections.length }} 次巡检</span></div>
          <div class="onsite-grid"><article v-for="item in inspections" :key="item.id" class="inspection-card"><header><span>现场多模态巡检</span><RouterLink to="/hazards">查看识别记录 →</RouterLink></header><h4>{{ item.original_filename }}</h4><p>{{ item.description || '现场图片上报' }}</p><div><b>{{ item.candidate_count || 0 }}<small>AI疑似</small></b><b class="ok">{{ item.accepted_count || 0 }}<small>人工确认</small></b><b>{{ item.rejected_count || 0 }}<small>人工排除</small></b></div></article><article v-for="item in onsiteItems" :key="item.id" class="rectification-card"><header><span>{{ item.item_no }} · {{ statusLabel(item.order_status || item.status) }}</span><RouterLink :to="`/rectification?item=${item.id}`">查看整改闭环 →</RouterLink></header><h4>{{ item.title }}</h4><p>{{ item.location }} · {{ item.responsible_ref }}</p><div><span>整改提交 <b>{{ item.submission_count || 0 }}</b> 次</span><span>人工复核 <b>{{ item.review_count || 0 }}</b> 次</span></div></article></div>
          <p v-if="!inspections.length && !onsiteItems.length" class="section-empty">当日暂无现场巡检或整改状态变化。</p>
        </section>

        <section class="log-section open-section"><div class="section-title"><div><small>05 / PENDING</small><h3>截至当日未闭环事项</h3></div><span>{{ openItems.length }} 项</span></div><div v-if="openItems.length" class="open-list"><article v-for="(item,index) in openItems" :key="`${item.type}-${index}`"><span>{{ item.type === 'plan_revision' ? '方案整改' : '现场隐患' }}</span><div><b>{{ item.title }}</b><p>{{ item.detail }}</p></div><RouterLink :to="item.link">{{ statusLabel(item.status) }} →</RouterLink></article></div><p v-else class="section-empty success-empty">截至当日没有未闭环事项。</p></section>

        <details class="evidence-section"><summary>数据来源与追溯依据 <span>{{ evidenceSources.length }} 条</span></summary><div class="evidence-list"><article v-for="item in evidenceSources.slice(0,10)" :key="`${item.evidence_type}-${item.source_id}`"><b>{{ item.title || item.source_id }}</b><p>{{ item.quote }}</p><small>{{ item.location }}</small></article></div><p>日志按真实业务数据生成；AI仅提供疑似识别与辅助对比，正式安全事项及最终关闭以人工确认、人工复核记录为准。</p></details>
      </main>
    </template>

    <section v-else class="blank-log card"><span>LOG</span><h2>当前日期还没有安全日志</h2><p>生成后可查看施工安全全过程时间线、各业务环节和未闭环事项。</p><button class="btn btn-primary" @click="generateLog">生成 {{ logDate }} 日志</button></section>
    <TaskChainDrawer :open="!!chainTaskId" :task-id="chainTaskId" @close="chainTaskId = null" />
  </section>
</template>

<style scoped>
.safety-log-page{max-width:1680px}.heading-actions,.command-actions,.command-copy{display:flex;align-items:center;gap:12px}.date-input,.history-select{border:1px solid var(--line);border-radius:11px;background:white;color:var(--ink);padding:11px 13px;font-size:14px}.log-command{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:18px 20px}.log-state{min-width:62px;padding:10px;border-radius:11px;background:#eef2f7;color:#687b90;text-align:center;font:800 12px monospace}.log-state.ready{background:#e9f4ff;color:#1768aa}.command-copy b{font-size:15px}.command-copy p{font-size:13px;color:var(--muted);margin:4px 0 0}.history-select{max-width:290px}.log-metrics{display:grid;grid-template-columns:repeat(6,1fr);gap:11px;margin:13px 0}.log-metrics article{display:flex;align-items:center;justify-content:space-between;padding:16px;border:1px solid var(--line);border-radius:15px;background:rgba(255,255,255,.84)}.log-metrics span{font-size:13px;color:var(--muted)}.log-metrics strong{font-size:27px;color:#183a5b}.log-metrics .closed strong{color:var(--green)}.log-metrics .attention{border-color:#f2c66d;background:#fffaf0}.log-metrics .attention strong{color:#b66b00}.log-preview{padding:24px;min-height:560px}.document-head{display:flex;justify-content:space-between;gap:20px;padding-bottom:19px;border-bottom:2px solid #183a5b}.document-head small{font-size:12px;color:var(--muted)}.document-head h2{font-size:24px;margin:6px 0 0}.document-head>div:last-child{text-align:right}.document-head b,.document-head span{display:block}.document-head b{font-size:16px}.document-head span{font-size:13px;color:var(--muted);margin-top:4px}.process-flow{display:grid;grid-template-columns:repeat(4,1fr);align-items:stretch;gap:18px;margin:22px 0 32px}.process-flow>div{position:relative;display:flex;min-height:96px;flex-direction:column;justify-content:center;padding:18px 20px;border:1px solid #d8e4f0;border-radius:14px;background:#f8fbfe}.process-flow span,.process-flow b{display:block}.process-flow span{font:800 13px monospace;color:#3f7cad}.process-flow b{font-size:17px;line-height:1.4;margin-top:8px}.process-flow i{position:absolute;right:-19px;top:50%;z-index:2;width:20px;display:grid;place-items:center;transform:translateY(-50%);color:#7894ae;font-size:20px;line-height:1;font-style:normal}.log-section{margin-top:15px;padding:20px;border:1px solid #dfe7f1;border-radius:16px;background:white}.section-title{display:flex;justify-content:space-between;gap:14px;align-items:flex-start;margin-bottom:15px}.section-title small{font:800 10px monospace;color:#5887b1}.section-title h3{font-size:18px;margin:5px 0 0}.section-title>span{padding:6px 9px;border-radius:8px;background:#eef4fa;color:#456987;font-size:12px}.section-empty{color:var(--muted);font-size:13px;margin:8px 0}.timeline-list article{display:grid;grid-template-columns:18px 1fr;gap:13px}.timeline-mark{display:flex;flex-direction:column;align-items:center}.timeline-mark i{width:10px;height:10px;margin-top:9px;border-radius:50%;background:#2f80c7;box-shadow:0 0 0 5px #e4f2ff}.timeline-mark em{width:2px;flex:1;min-height:60px;background:#d8e5f1}.timeline-list article:last-child .timeline-mark em{display:none}.timeline-main{padding:4px 0 17px}.timeline-main>div{display:flex;align-items:center;gap:9px}.timeline-main b{font-size:15px}.timeline-main small{margin-left:auto;color:var(--muted);font-size:12px}.timeline-main p{margin:7px 0;color:#4d6278;font-size:13px;line-height:1.65}.timeline-main footer{display:flex;justify-content:space-between;gap:12px;font-size:12px;color:#6d8195}.timeline-main a,.business-card a,.inspection-card a,.rectification-card a,.open-list a{color:#2563eb;text-decoration:none;font-weight:700}.stage-tag{padding:4px 7px;border-radius:7px;background:#e8f4ff;color:#1e6da8;font-size:11px;font-weight:800}.plan-grid,.onsite-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:11px}.business-card,.inspection-card,.rectification-card{padding:15px;border:1px solid #dfe7ef;border-radius:13px;background:#f9fbfd}.business-card header,.inspection-card header,.rectification-card header{display:flex;justify-content:space-between;gap:10px;font-size:12px;color:#61788f}.business-card h4,.inspection-card h4,.rectification-card h4{font-size:15px;margin:10px 0 6px}.business-card p,.inspection-card p,.rectification-card p{font-size:13px;line-height:1.6;color:#536a80;margin:0}.revision-card{border-left:4px solid #e0a22d;background:#fffcf4}.revision-result,.inspection-card>div{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:11px 0}.revision-result>b,.inspection-card>div>b{padding:10px;border-radius:10px;background:white;font-size:22px}.revision-result small,.inspection-card small{display:block;margin-top:3px;color:var(--muted);font-size:11px}.revision-result .ok,.inspection-card .ok{color:var(--green)}.revision-result .warn{color:#b66b00}.weather-strip{display:grid;grid-template-columns:110px 1fr;gap:12px;padding:13px 15px;margin-bottom:12px;border-radius:12px;background:#eef7ff}.weather-strip b{font-size:13px;color:#1768aa}.weather-strip p{font-size:13px;line-height:1.65;margin:0;color:#45637e}.task-log-list{display:grid;gap:11px}.task-log{border:1px solid #dce5ef;border-left:4px solid var(--green);border-radius:13px;background:#fbfcfe}.task-log.yellow{border-left-color:var(--yellow)}.task-log.red{border-left-color:var(--red)}.task-log>header{display:flex;justify-content:space-between;gap:14px;padding:14px 15px 9px}.task-log>header>div{display:flex;align-items:center;gap:9px}.task-log h4{font-size:15px;margin:0}.task-log header button{border:0;background:transparent;color:#2563eb;font-size:12px;font-weight:700}.risk-level{padding:5px 8px;border-radius:7px;background:#eaf8f1;color:#217357;font-size:11px;font-weight:800}.task-log.yellow .risk-level{background:#fff5df;color:#a46400}.task-log.red .risk-level{background:#fff0ee;color:#a43b35}.task-log>p{font-size:13px;color:var(--muted);padding:0 15px;margin:0 0 10px}.task-detail-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;padding:0 13px 13px}.task-detail-grid section{padding:12px;border-radius:10px;background:#f3f7fb}.task-detail-grid b{font-size:13px}.task-detail-grid ul{padding-left:18px;margin:8px 0 0}.task-detail-grid li{font-size:13px;line-height:1.65;margin:4px 0}.rectification-card>div{display:flex;gap:18px;margin-top:10px;font-size:12px;color:#60778e}.rectification-card>div b{font-size:16px;color:#173c60}.open-section{border-color:#efd59d;background:#fffdf8}.open-list{display:grid;gap:8px}.open-list article{display:grid;grid-template-columns:78px 1fr auto;gap:12px;align-items:center;padding:13px;border-radius:11px;background:white}.open-list article>span{font-size:11px;font-weight:800;color:#a36300}.open-list b{font-size:14px}.open-list p{font-size:12px;color:var(--muted);margin:4px 0 0}.success-empty{color:var(--green)}.evidence-section{margin-top:15px;padding:17px 20px;border:1px solid #dfe7f1;border-radius:15px;background:#f8fafc}.evidence-section summary{cursor:pointer;font-size:14px;font-weight:800}.evidence-section summary span{float:right;color:var(--muted);font-weight:500}.evidence-section>p{font-size:12px;color:var(--muted);line-height:1.7}.evidence-list{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin-top:14px}.evidence-list article{padding:12px;border-radius:10px;background:white}.evidence-list b{font-size:13px}.evidence-list p{font-size:12px;line-height:1.6;color:#4f6478;margin:5px 0}.evidence-list small{font-size:11px;color:var(--muted)}.blank-log{min-height:460px;display:grid;place-content:center;text-align:center;padding:30px}.blank-log>span{width:62px;height:62px;border-radius:18px;display:grid;place-items:center;margin:auto;background:#e9f4ff;color:#2563eb;font:900 13px monospace}.blank-log h2{font-size:23px;margin:18px 0 8px}.blank-log p{color:var(--muted);font-size:14px}.blank-log button{justify-self:center;margin-top:10px}@media(max-width:1200px){.log-metrics{grid-template-columns:repeat(3,1fr)}}@media(max-width:800px){.page-heading,.log-command{align-items:stretch;flex-direction:column}.heading-actions,.command-actions{display:grid;grid-template-columns:1fr 1fr}.history-select{max-width:none}.log-metrics{grid-template-columns:repeat(2,1fr)}.process-flow,.plan-grid,.onsite-grid,.task-detail-grid,.evidence-list{grid-template-columns:1fr}.process-flow i{display:none}.timeline-main>div{align-items:flex-start;flex-wrap:wrap}.timeline-main small{width:100%;margin:0}.timeline-main footer,.open-list article{align-items:flex-start;grid-template-columns:1fr}.document-head{display:block}.document-head>div:last-child{text-align:left;margin-top:8px}}
</style>
