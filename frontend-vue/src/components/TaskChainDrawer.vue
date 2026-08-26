<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { api } from "../api";

const props = defineProps<{ open: boolean; taskId: string | null }>();
const emit = defineEmits<{ close: [] }>();
const data = ref<any>(null);
const loading = ref(false);
const error = ref("");

const levelText: Record<string, string> = {
  red: "红色预警",
  yellow: "重点关注",
  green: "常规控制",
};
const changeText: Record<string, string> = {
  new: "首次纳入",
  escalated: "风险升高",
  reduced: "风险降低",
  changed: "条件变化",
  unchanged: "保持不变",
};
const chainNodes = computed(() => {
  const value = data.value;
  if (!value) return [];
  return [
    {
      code: "01",
      label: "任务登记",
      value: value.task.normalized_task || value.task.work_content,
      ready: true,
    },
    {
      code: "02",
      label: "任务风险卡",
      value: value.risk_card
        ? `${value.risk_card.main_risks?.length || 0} 项主要风险`
        : "尚未生成",
      ready: !!value.risk_card,
    },
    {
      code: "03",
      label: "动态评估",
      value: value.current_dynamic_risk
        ? levelText[value.current_dynamic_risk.risk_level]
        : "尚未评估",
      ready: !!value.current_dynamic_risk,
    },
    {
      code: "04",
      label: "方案审查",
      value: `${value.audit_findings?.length || 0} 项关联问题`,
      ready: !!value.audit_findings?.length,
    },
    {
      code: "05",
      label: "学习记录",
      value: `${value.learning?.quiz_attempts?.length || 0} 次测验 · ${value.learning?.active_wrong_count || 0} 道待巩固`,
      ready: !!(
        value.learning?.quiz_attempts?.length || value.learning?.qa_records?.length
      ),
    },
  ];
});

async function load() {
  if (!props.open || !props.taskId) return;
  loading.value = true;
  error.value = "";
  data.value = null;
  try {
    data.value = await api(`/api/v1/tasks/${props.taskId}/chain`);
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "任务链路加载失败";
  } finally {
    loading.value = false;
  }
}

watch(() => [props.open, props.taskId], load, { immediate: true });
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="chain-shell" @keydown.esc="emit('close')">
      <button class="chain-backdrop" aria-label="关闭任务全链路" @click="emit('close')"></button>
      <aside class="chain-drawer" role="dialog" aria-modal="true" aria-label="任务全链路详情">
        <header class="chain-header">
          <div>
            <p>TRACEABLE TASK CHAIN</p>
            <h2>任务全链路详情</h2>
          </div>
          <button aria-label="关闭" @click="emit('close')">×</button>
        </header>
        <div v-if="loading" class="chain-loading"><i></i><span>正在汇聚任务全链路数据…</span></div>
        <div v-else-if="error" class="chain-error">{{ error }}</div>
        <div v-else-if="data" class="chain-content">
          <section class="task-identity">
            <div>
              <span class="task-id">TASK · {{ data.task.id.slice(0, 8) }}</span>
              <h3>{{ data.task.normalized_task || data.task.work_content }}</h3>
              <p>{{ [data.task.work_location, data.task.work_floor, data.task.work_time].filter(Boolean).join(" · ") }}</p>
            </div>
            <span class="quality-badge" :class="data.data_quality.status">
              {{ data.data_quality.status === "merged" ? `已合并 ${data.duplicate_count} 条记录` : "数据唯一" }}
            </span>
          </section>
          <p class="quality-note">{{ data.data_quality.message }}</p>

          <section class="chain-map">
            <div v-for="(node, index) in chainNodes" :key="node.code" class="chain-node" :class="{ ready: node.ready }">
              <span>{{ node.code }}</span>
              <div><b>{{ node.label }}</b><small>{{ node.value }}</small></div>
              <i v-if="index < chainNodes.length - 1">→</i>
            </div>
          </section>

          <section v-if="data.current_dynamic_risk" class="current-risk" :class="data.current_dynamic_risk.risk_level">
            <div class="section-title">
              <div><span>CURRENT RISK</span><h3>当前风险分析</h3></div>
              <b>{{ levelText[data.current_dynamic_risk.risk_level] }} · {{ data.current_dynamic_risk.priority_score }}</b>
            </div>
            <p>{{ data.current_dynamic_risk.summary }}</p>
            <div class="change-callout" :class="data.current_dynamic_risk.change?.direction">
              <strong>{{ changeText[data.current_dynamic_risk.change?.status] || "版本说明" }}</strong>
              <span>{{ data.current_dynamic_risk.change?.explanation }}</span>
            </div>
          </section>

          <section v-if="data.risk_card" class="chain-section">
            <div class="section-title"><div><span>RISK CARD</span><h3>任务风险提示卡</h3></div><a :href="data.links.worker">进入安全培训 ↗</a></div>
            <div class="risk-card-preview">
              <div><b>主要风险</b><ul><li v-for="item in data.risk_card.main_risks?.slice(0, 4)" :key="item">{{ item }}</li></ul></div>
              <div><b>作业前检查</b><ul><li v-for="item in data.risk_card.pre_job_checks?.slice(0, 4)" :key="item">{{ item }}</li></ul></div>
            </div>
          </section>

          <section class="chain-section">
            <div class="section-title"><div><span>VERSION HISTORY</span><h3>风险版本变化</h3></div><small>{{ data.dynamic_risk_versions.length }} 个版本</small></div>
            <div v-if="data.dynamic_risk_versions.length" class="version-list">
              <div v-for="version in data.dynamic_risk_versions" :key="version.run_id">
                <span class="version-level" :class="version.risk_level"></span>
                <div><b>{{ levelText[version.risk_level] }} · {{ version.priority_score }}</b><p>{{ version.change?.explanation }}</p></div>
                <time>{{ new Date(version.created_at).toLocaleString("zh-CN") }}</time>
              </div>
            </div>
            <p v-else class="chain-empty">尚未形成风险分析版本。</p>
          </section>

          <div class="chain-two-columns">
            <section class="chain-section">
              <div class="section-title"><div><span>AUDIT</span><h3>关联审计问题</h3></div><a v-if="data.task.audit_run_id" :href="data.links.audit">查看审计 ↗</a></div>
              <div v-if="data.audit_findings.length" class="compact-list">
                <div v-for="item in data.audit_findings.slice(0, 5)" :key="item.id"><b>{{ item.scene }} · {{ item.effective_result }}</b><p>{{ item.final_text || item.issue }}</p></div>
              </div>
              <p v-else class="chain-empty">该任务暂未关联方案审查问题。</p>
            </section>
            <section class="chain-section">
              <div class="section-title"><div><span>LEARNING</span><h3>个人学习信号</h3></div><a :href="data.links.worker">查看学习记录 ↗</a></div>
              <div class="learning-numbers"><div><strong>{{ data.learning.quiz_attempts.length }}</strong><small>关联测验</small></div><div><strong>{{ data.learning.qa_records.length }}</strong><small>关联问答</small></div><div><strong>{{ data.learning.active_wrong_count }}</strong><small>待巩固错题</small></div></div>
            </section>
          </div>

          <section class="chain-section">
            <div class="section-title"><div><span>TIMELINE</span><h3>数据变动时间线</h3></div></div>
            <div class="timeline-list"><div v-for="item in data.timeline" :key="`${item.type}-${item.at}`"><i></i><div><b>{{ item.title }}</b><p>{{ item.detail }}</p></div><time>{{ new Date(item.at).toLocaleString("zh-CN") }}</time></div></div>
          </section>
        </div>
      </aside>
    </div>
  </Teleport>
</template>

<style scoped>
.chain-shell{position:fixed;inset:0;z-index:100;display:flex;justify-content:flex-end}.chain-backdrop{position:absolute;inset:0;border:0;background:rgba(8,18,34,.46);backdrop-filter:blur(4px)}.chain-drawer{position:relative;width:min(820px,78vw);height:100vh;overflow:auto;background:#f5f7fb;color:var(--ink);box-shadow:-30px 0 80px rgba(8,18,34,.2);animation:slide-in .22s ease}.chain-header{position:sticky;top:0;z-index:3;display:flex;justify-content:space-between;align-items:center;padding:22px 28px;background:rgba(11,24,45,.96);color:white;backdrop-filter:blur(14px)}.chain-header p{font:700 9px monospace;letter-spacing:.16em;color:#7dd3fc;margin:0 0 5px}.chain-header h2{font-size:22px;margin:0}.chain-header button{width:38px;height:38px;border:1px solid rgba(255,255,255,.16);border-radius:12px;background:rgba(255,255,255,.08);color:white;font-size:24px}.chain-content{padding:22px 26px 40px}.chain-loading,.chain-error{margin:30px;padding:24px;border-radius:16px;background:white}.chain-loading{display:flex;gap:12px;align-items:center}.chain-loading i{width:12px;height:12px;border:2px solid #bed2eb;border-top-color:var(--forest-2);border-radius:50%;animation:spin .8s linear infinite}.chain-error{color:#b42318;background:#fff1f0}.task-identity{display:flex;justify-content:space-between;gap:20px;align-items:flex-start;padding:22px;border-radius:18px;background:linear-gradient(135deg,#10233f,#18385f);color:white}.task-id{font:700 9px monospace;color:#7dd3fc}.task-identity h3{font-size:23px;margin:8px 0 7px}.task-identity p{font-size:11px;color:#b7c8dc;margin:0}.quality-badge{white-space:nowrap;padding:7px 10px;border-radius:999px;background:rgba(94,234,212,.14);color:#99f6e4;font-size:10px;font-weight:700}.quality-badge.merged{background:rgba(251,191,36,.15);color:#fde68a}.quality-note{margin:10px 4px 18px;color:var(--muted);font-size:10px}.chain-map{display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin:18px 0}.chain-node{position:relative;min-width:0;padding:13px;border:1px solid #dfe7f1;border-radius:14px;background:rgba(255,255,255,.75)}.chain-node>span{font:800 9px monospace;color:#91a3b9}.chain-node b,.chain-node small{display:block}.chain-node b{font-size:10px;margin:7px 0 4px}.chain-node small{font-size:8px;line-height:1.45;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.chain-node.ready{border-color:#bad7f5;background:white;box-shadow:0 8px 24px rgba(24,78,130,.06)}.chain-node.ready>span{color:#2563eb}.chain-node>i{position:absolute;right:-8px;top:50%;z-index:2;color:#8aa1ba;font-style:normal}.current-risk,.chain-section{border:1px solid #dfe7f1;border-radius:18px;background:white;padding:19px;margin-top:14px}.current-risk{border-left:4px solid var(--green)}.current-risk.yellow{border-left-color:var(--yellow)}.current-risk.red{border-left-color:var(--red)}.section-title{display:flex;justify-content:space-between;align-items:flex-start;gap:14px}.section-title span{font:700 8px monospace;letter-spacing:.12em;color:#5c7ea5}.section-title h3{font-size:15px;margin:5px 0 0}.section-title>b{font-size:10px;padding:7px 9px;border-radius:9px;background:#eef4fa}.section-title a{font-size:9px;color:#2563eb;font-weight:700}.section-title small{font-size:9px;color:var(--muted)}.current-risk>p{font-size:10px;color:var(--muted);margin:14px 0}.change-callout{display:grid;grid-template-columns:84px 1fr;gap:10px;padding:11px 12px;border-radius:11px;background:#eff6ff;font-size:9px;line-height:1.6}.change-callout.up{background:#fff0ee;color:#9f302d}.change-callout.down{background:#eaf8f1;color:#216c50}.risk-card-preview,.chain-two-columns{display:grid;grid-template-columns:1fr 1fr;gap:12px}.risk-card-preview{margin-top:14px}.risk-card-preview>div{padding:13px;border-radius:12px;background:#f5f8fc}.risk-card-preview b{font-size:9px;color:#365a7d}.risk-card-preview ul{padding-left:15px;margin:8px 0 0}.risk-card-preview li{font-size:9px;line-height:1.6;margin:4px 0}.version-list{margin-top:10px}.version-list>div{display:grid;grid-template-columns:10px 1fr auto;gap:10px;padding:11px 2px;border-top:1px solid #edf1f6;align-items:start}.version-level{width:8px;height:8px;margin-top:4px;border-radius:50%;background:var(--green)}.version-level.yellow{background:var(--yellow)}.version-level.red{background:var(--red)}.version-list b{font-size:10px}.version-list p{font-size:9px;line-height:1.55;color:var(--muted);margin:3px 0}.version-list time,.timeline-list time{font:8px monospace;color:#91a0b2;white-space:nowrap}.compact-list>div{padding:10px 0;border-top:1px solid #edf1f6}.compact-list b{font-size:9px}.compact-list p{font-size:8px;line-height:1.55;color:var(--muted);margin:4px 0}.learning-numbers{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin-top:15px}.learning-numbers>div{padding:12px;border-radius:12px;background:#f3f7fb}.learning-numbers strong,.learning-numbers small{display:block}.learning-numbers strong{font-size:21px}.learning-numbers small{font-size:8px;color:var(--muted);margin-top:3px}.timeline-list{margin-top:10px}.timeline-list>div{display:grid;grid-template-columns:10px 1fr auto;gap:10px;padding:10px 2px;border-top:1px solid #edf1f6}.timeline-list i{width:7px;height:7px;border-radius:50%;background:#38bdf8;margin-top:4px;box-shadow:0 0 0 4px #e0f2fe}.timeline-list b{font-size:10px}.timeline-list p{font-size:8px;color:var(--muted);margin:3px 0}.chain-empty{font-size:9px;color:var(--muted);margin:14px 0 0}@keyframes slide-in{from{transform:translateX(25px);opacity:.4}}@keyframes spin{to{transform:rotate(360deg)}}@media(max-width:900px){.chain-drawer{width:100vw}.chain-map{grid-template-columns:1fr}.chain-node>i{display:none}.chain-two-columns,.risk-card-preview{grid-template-columns:1fr}}
</style>
