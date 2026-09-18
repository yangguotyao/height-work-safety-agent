<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'

const data = ref<any>(null)
const error = ref('')
const today = new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric', weekday: 'long' }).format(new Date())

onMounted(async () => {
  try { data.value = await api('/api/v1/dashboard') } catch (cause) { error.value = cause instanceof Error ? cause.message : '加载失败' }
})

const risk = computed(() => data.value?.dynamic_risk || {})
const riskTotal = computed(() => (risk.value.red_count || 0) + (risk.value.yellow_count || 0) + (risk.value.green_count || 0))
</script>

<template>
  <section class="page">
    <header class="page-heading"><div><p class="eyebrow">PROJECT COMMAND CENTER</p><h1>今天，先看风险。</h1></div><span class="date-chip">{{ today }}</span></header>
    <div v-if="error" class="error-banner">{{ error }}</div>
    <div class="hero-grid">
      <article class="risk-hero card">
        <div class="hero-top"><div><small>当日风险分析</small><h2>{{ riskTotal ? `${riskTotal} 项任务已评估` : '等待今日任务' }}</h2></div><RouterLink class="btn btn-accent" to="/worker">打开班前风险分析 →</RouterLink></div>
        <div class="risk-numbers">
          <div><span class="risk-dot red"></span><strong>{{ risk.red_count || 0 }}</strong><small>红色预警</small></div>
          <div><span class="risk-dot yellow"></span><strong>{{ risk.yellow_count || 0 }}</strong><small>重点关注</small></div>
          <div><span class="risk-dot green"></span><strong>{{ risk.green_count || 0 }}</strong><small>常规控制</small></div>
        </div>
        <p class="hero-note">等级由透明规则和当前数据生成；颜色用于展示风险信号，不代表作业许可。</p>
      </article>
    </div>
    <div class="metric-grid">
      <article class="metric metric-emphasis card"><small>施工方案</small><strong>{{ data?.counts?.documents ?? '—' }}</strong></article>
      <article class="metric card"><small>审计任务</small><strong>{{ data?.counts?.audits ?? '—' }}</strong></article>
      <article class="metric card"><small>每日任务</small><strong>{{ data?.counts?.tasks ?? '—' }}</strong></article>
      <article class="metric card"><small>知识实体</small><strong>{{ data?.knowledge?.entity_count ?? '—' }}</strong></article>
    </div>
    <div class="dashboard-lower">
      <article class="card panel"><div class="section-head"><h2>专业 Agent 编队</h2></div><div class="agent-list">
        <RouterLink to="/audit"><b>01</b><div><strong>施工方案审查</strong><span>解析方案、匹配规则、沉淀作业控制要求</span></div><i>→</i></RouterLink>
        <RouterLink to="/worker"><b>02</b><div><strong>班前风险分析</strong><span>描述作业、关联多源证据、生成班前提示</span></div><i>→</i></RouterLink>
        <RouterLink to="/hazards"><b>03</b><div><strong>现场隐患巡检</strong><span>结合任务上下文识别现场疑似隐患</span></div><i>→</i></RouterLink>
        <RouterLink to="/rectification"><b>04</b><div><strong>隐患整改记录</strong><span>整改派单、证据核验、人工复核销项</span></div><i>→</i></RouterLink>
        <RouterLink to="/agent"><b>05</b><div><strong>安全日志生成</strong><span>汇总全过程真实记录并形成可追溯日志</span></div><i>→</i></RouterLink>
      </div></article>
      <article class="card panel"><div class="section-head"><h2>高频风险主题</h2></div><div v-if="data?.knowledge?.top_risks?.length" class="topic-list"><div v-for="(item,index) in data.knowledge.top_risks" :key="item.id"><span>{{ String(Number(index)+1).padStart(2,'0') }}</span><b>{{ item.name }}</b><em>{{ item.count }}</em></div></div><p v-else class="empty">暂无正式任务风险数据</p></article>
    </div>
  </section>
</template>

<style scoped>
.hero-grid{display:grid;grid-template-columns:1fr;gap:16px}.risk-hero{padding:27px;background:radial-gradient(circle at 90% 0,rgba(56,189,248,.22),transparent 34%),linear-gradient(135deg,#10233f,#174d7b);color:white;border:0;box-shadow:0 24px 55px rgba(18,55,94,.18)}.hero-top{display:flex;justify-content:space-between;gap:16px;align-items:flex-start}.hero-top small{color:#9fc3e2}.hero-top h2{font-size:26px;margin:7px 0}.risk-numbers{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:25px 0}.risk-numbers>div{border-left:1px solid rgba(255,255,255,.16);padding-left:17px}.risk-numbers strong,.risk-numbers small{display:block}.risk-numbers strong{font-size:39px;margin:7px 0 2px}.risk-numbers small{color:#aac5dc;font-size:10px}.hero-note{color:#8fb0cd;font-size:10px;margin:0}.metric-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:14px 0}.metric{padding:19px;border-top:3px solid #dce9f7}.metric small,.metric strong{display:block}.metric small{font-size:11px;color:var(--muted)}.metric strong{font-size:30px;margin:8px 0 0;color:#16385a}.metric-emphasis small{font-size:12px}.dashboard-lower{display:grid;grid-template-columns:1.15fr .85fr;gap:14px}.agent-list{display:grid}.agent-list a{display:grid;grid-template-columns:38px 1fr 20px;gap:10px;align-items:center;padding:13px 4px;border-bottom:1px solid #e5ebf3}.agent-list a:last-child{border:0}.agent-list>a>b{font:700 10px monospace;color:#5a82aa}.agent-list strong,.agent-list span{display:block}.agent-list strong{font-size:12px;margin-bottom:3px}.agent-list span{font-size:10px;color:var(--muted)}.agent-list i{font-style:normal;color:#2563eb}.topic-list>div{display:grid;grid-template-columns:28px 1fr 28px;gap:8px;padding:12px 0;border-bottom:1px solid #e5ebf3;align-items:center}.topic-list span{font:700 9px monospace;color:#6f8baa}.topic-list b{font-size:11px}.topic-list em{font-style:normal;background:#eaf3fc;color:#245d91;border-radius:8px;padding:4px;text-align:center;font-size:9px}@media(max-width:1050px){.dashboard-lower{grid-template-columns:1fr}.metric-grid{grid-template-columns:repeat(2,1fr)}}@media(max-width:600px){.risk-numbers strong{font-size:28px}.hero-top{display:block}.hero-top .btn{display:inline-block;margin-top:8px}.metric-grid{gap:8px}.metric{padding:14px}}
</style>
