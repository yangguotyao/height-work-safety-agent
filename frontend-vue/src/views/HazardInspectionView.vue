<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import { useRoute } from "vue-router";
import { api } from "../api";

type Candidate = {
  id: string;
  scene: string;
  hazard_type: string;
  suspected_hazard: string;
  visible_facts: string[];
  unable_to_confirm: string[];
  recommended_checks: string[];
  review_status: "pending" | "confirmed" | "rejected";
  safety_item?: any;
};

const file = ref<File | null>(null);
const preview = ref("");
const description = ref("");
const taskId = ref("");
const route = useRoute();
const tasks = ref<any[]>([]);
const inspections = ref<any[]>([]);
const selected = ref<any>(null);
const uploading = ref(false);
const error = ref("");
const confirmingId = ref<string | null>(null);
const reviewingId = ref<string | null>(null);
const draft = reactive({
  title: "",
  fact_description: "",
  hazard_category: "",
  risk_level: "yellow",
  location: "",
  rectification_requirement: "",
  responsible_ref: "",
  deadline: "",
  comment: "",
});
let pollTimer: number | undefined;

const selectedTask = computed(() => tasks.value.find((task) => task.id === taskId.value));
const pendingCount = computed(
  () => selected.value?.candidates?.filter((item: Candidate) => item.review_status === "pending").length || 0,
);

function statusText(status: string) {
  return {
    queued: "等待分析",
    analyzing: "AI分析中",
    ready: "等待人工确认",
    failed: "分析失败",
  }[status] || status;
}

function qualityText(quality: string) {
  return {
    usable: "图片信息清晰",
    partially_usable: "图片信息部分可用",
    unusable: "图片不足以识别",
  }[quality] || "尚未评估";
}

function chooseFile(event: Event) {
  const next = (event.target as HTMLInputElement).files?.[0] || null;
  if (preview.value) URL.revokeObjectURL(preview.value);
  file.value = next;
  preview.value = next ? URL.createObjectURL(next) : "";
}

function stopPolling() {
  if (pollTimer) window.clearInterval(pollTimer);
  pollTimer = undefined;
}

async function refresh(id: string) {
  selected.value = await api(`/api/v1/hazard-inspections/${id}`);
  const index = inspections.value.findIndex((item) => item.id === id);
  if (index >= 0) inspections.value[index] = selected.value;
  if (["ready", "failed"].includes(selected.value.analysis_status)) stopPolling();
}

function startPolling(id: string) {
  stopPolling();
  pollTimer = window.setInterval(() => refresh(id).catch(stopPolling), 1500);
}

async function submit() {
  if (!file.value || description.value.trim().length < 2) return;
  uploading.value = true;
  error.value = "";
  const body = new FormData();
  body.append("file", file.value);
  body.append("description", description.value.trim());
  if (taskId.value) body.append("task_id", taskId.value);
  try {
    const result = await api<any>("/api/v1/hazard-inspections", { method: "POST", body });
    selected.value = result;
    inspections.value.unshift(result);
    startPolling(result.id);
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "提交失败";
  } finally {
    uploading.value = false;
  }
}

async function selectInspection(item: any) {
  error.value = "";
  confirmingId.value = null;
  await refresh(item.id);
  if (["queued", "analyzing"].includes(selected.value.analysis_status)) startPolling(item.id);
}

function beginConfirm(candidate: Candidate) {
  confirmingId.value = candidate.id;
  draft.title = candidate.suspected_hazard;
  draft.fact_description = candidate.visible_facts.join("；");
  draft.hazard_category = candidate.hazard_type || candidate.scene || "高处作业现场安全";
  draft.risk_level = "yellow";
  draft.location = [selectedTask.value?.work_location, selectedTask.value?.work_floor]
    .filter(Boolean)
    .join(" ");
  draft.rectification_requirement = candidate.recommended_checks.join("；");
  draft.responsible_ref = selectedTask.value?.team_ref || "";
  draft.deadline = "";
  draft.comment = "";
}

async function reject(candidate: Candidate) {
  reviewingId.value = candidate.id;
  error.value = "";
  try {
    selected.value = await api(`/api/v1/hazard-candidates/${candidate.id}/review`, {
      method: "PATCH",
      body: JSON.stringify({ action: "reject", comment: "人工核查后不转为正式安全事项" }),
    });
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "复核失败";
  } finally {
    reviewingId.value = null;
  }
}

async function confirm(candidate: Candidate) {
  reviewingId.value = candidate.id;
  error.value = "";
  try {
    selected.value = await api(`/api/v1/hazard-candidates/${candidate.id}/review`, {
      method: "PATCH",
      body: JSON.stringify({
        action: "confirm",
        ...draft,
        deadline: draft.deadline || null,
      }),
    });
    confirmingId.value = null;
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "确认失败";
  } finally {
    reviewingId.value = null;
  }
}

async function load() {
  error.value = "";
  try {
    [tasks.value, inspections.value] = await Promise.all([
      api<any[]>("/api/v1/tasks/recent?limit=50"),
      api<any[]>("/api/v1/hazard-inspections?limit=30"),
    ]);
    const linkedTaskId = typeof route.query.task === "string" ? route.query.task : "";
    if (linkedTaskId && tasks.value.some((task) => task.id === linkedTaskId)) taskId.value = linkedTaskId;
    if (inspections.value[0]) await selectInspection(inspections.value[0]);
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "页面数据加载失败";
  }
}

onMounted(load);
onBeforeUnmount(() => {
  stopPolling();
  if (preview.value) URL.revokeObjectURL(preview.value);
});
</script>

<template>
  <section class="page hazard-page">
    <header class="page-heading">
      <div>
        <p class="eyebrow">ONSITE SAFETY INSPECTION</p>
        <h1>现场隐患巡检</h1>
      </div>
      <span class="human-gate">人工确认后生效</span>
    </header>

    <div v-if="error" class="error-banner">{{ error }}</div>

    <div class="hazard-layout">
      <aside class="left-column">
        <article class="card panel upload-panel">
          <div class="section-head"><h2>提交现场信息</h2><small>单张图片</small></div>
          <label class="image-drop">
            <input type="file" accept="image/jpeg,image/png,image/webp" @change="chooseFile" />
            <img v-if="preview" :src="preview" alt="待上传现场图片预览" />
            <span v-else class="upload-placeholder"><b>＋ 选择现场图片</b><small>JPG / PNG / WEBP，最大 10 MB</small></span>
          </label>
          <label class="form-field">
            <span>关联作业任务（可选）</span>
            <select v-model="taskId" class="input">
              <option value="">仅关联当前项目</option>
              <option v-for="task in tasks" :key="task.id" :value="task.id">
                {{ task.normalized_task || task.work_content }} · {{ task.work_location }} {{ task.work_floor }}
              </option>
            </select>
          </label>
          <label class="form-field">
            <span>现场文字描述</span>
            <textarea v-model="description" class="input description" maxlength="2000" placeholder="例如：12层外脚手架作业面，安全员巡检时发现局部防护情况需要核查。"></textarea>
          </label>
          <button class="btn btn-primary submit-button" :disabled="!file || description.trim().length < 2 || uploading" @click="submit">
            {{ uploading ? "正在上传…" : "提交AI分析" }}
          </button>
        </article>

        <article class="card panel history-panel">
          <div class="section-head"><h2>最近巡检</h2><small>{{ inspections.length }} 条</small></div>
          <button v-for="item in inspections" :key="item.id" class="history-item" :class="{ active: selected?.id === item.id }" @click="selectInspection(item)">
            <span><b>{{ item.original_filename }}</b><small>{{ item.description }}</small></span>
            <em :class="item.analysis_status">{{ statusText(item.analysis_status) }}</em>
          </button>
          <p v-if="!inspections.length" class="empty">还没有现场巡检记录</p>
        </article>
      </aside>

      <main class="result-column">
        <article v-if="!selected" class="card empty result-empty">上传现场图片后，AI分析结果将在这里展示。</article>
        <template v-else>
          <article class="card panel inspection-summary">
            <div class="inspection-image"><img :src="selected.image_url" alt="已上传现场图片" /></div>
            <div>
              <div class="summary-top">
                <span class="status-pill">{{ statusText(selected.analysis_status) }}</span>
              </div>
              <h2>{{ selected.description }}</h2>
              <p>{{ qualityText(selected.image_quality) }} · {{ selected.original_filename }}</p>
              <div v-if="selected.analysis_status === 'analyzing' || selected.analysis_status === 'queued'" class="analyzing"><i></i>正在结合图片、现场描述和任务上下文进行分析</div>
              <div v-if="selected.analysis_status === 'failed'" class="analysis-error">{{ selected.error }}</div>
            </div>
          </article>

          <article v-if="selected.analysis_status === 'ready'" class="card panel facts-panel">
            <div class="section-head"><h2>图片总体观察</h2></div>
            <ul v-if="selected.overall_visible_facts?.length"><li v-for="fact in selected.overall_visible_facts" :key="fact">{{ fact }}</li></ul>
            <p v-else class="muted">图片中没有提取到足够清晰的总体事实。</p>
            <div v-if="selected.unable_to_confirm?.length" class="unknown-block"><b>总体无法确认</b><span v-for="item in selected.unable_to_confirm" :key="item">{{ item }}</span></div>
          </article>

          <section v-if="selected.analysis_status === 'ready'" class="candidate-section">
            <div class="candidate-heading"><div><p class="eyebrow">HUMAN REVIEW QUEUE</p><h2>疑似隐患候选</h2></div><span>{{ pendingCount }} 项待确认</span></div>
            <article v-for="(candidate, index) in selected.candidates" :key="candidate.id" class="card panel candidate-card" :class="candidate.review_status">
              <header>
                <div><span>{{ index === 0 ? "主要疑似隐患" : "次要疑似隐患" }} · {{ candidate.scene || "现场巡检" }}</span><h3>{{ candidate.suspected_hazard }}</h3></div>
                <em :class="candidate.review_status">{{ candidate.review_status === "pending" ? "待人工确认" : candidate.review_status === "confirmed" ? "已转正式事项" : "已驳回" }}</em>
              </header>
              <div class="four-fields">
                <section><b>可见事实</b><ul><li v-for="item in candidate.visible_facts" :key="item">{{ item }}</li></ul></section>
                <section><b>疑似类型</b><p>{{ candidate.hazard_type || "待人工归类" }}</p></section>
                <section><b>无法确认</b><ul><li v-for="item in candidate.unable_to_confirm" :key="item">{{ item }}</li><li v-if="!candidate.unable_to_confirm.length">无补充项</li></ul></section>
                <section><b>建议核查</b><ul><li v-for="item in candidate.recommended_checks" :key="item">{{ item }}</li></ul></section>
              </div>

              <div v-if="candidate.review_status === 'pending'" class="review-actions">
                <button class="btn btn-ghost" :disabled="reviewingId === candidate.id" @click="reject(candidate)">人工驳回</button>
                <button class="btn btn-primary" @click="beginConfirm(candidate)">人工确认并建项</button>
              </div>

              <form v-if="confirmingId === candidate.id" class="confirm-form" @submit.prevent="confirm(candidate)">
                <div class="confirm-title"><div><b>正式安全事项</b><small>以下字段由人工确认，不采用AI自动结论</small></div><button type="button" @click="confirmingId = null">×</button></div>
                <label><span>事项标题</span><input v-model="draft.title" class="input" required /></label>
                <label><span>事实描述</span><textarea v-model="draft.fact_description" class="input" required></textarea></label>
                <div class="form-grid">
                  <label><span>隐患类别</span><input v-model="draft.hazard_category" class="input" required /></label>
                  <label><span>人工风险等级</span><select v-model="draft.risk_level" class="input"><option value="red">红色</option><option value="yellow">黄色</option><option value="green">绿色</option></select></label>
                  <label><span>现场位置</span><input v-model="draft.location" class="input" required /></label>
                  <label><span>责任人/班组</span><input v-model="draft.responsible_ref" class="input" required /></label>
                  <label><span>完成期限</span><input v-model="draft.deadline" class="input" type="date" /></label>
                </div>
                <label><span>整改要求</span><textarea v-model="draft.rectification_requirement" class="input" required></textarea></label>
                <label><span>确认备注（可选）</span><textarea v-model="draft.comment" class="input"></textarea></label>
                <button class="btn btn-primary" :disabled="reviewingId === candidate.id" type="submit">确认创建正式安全事项</button>
              </form>

              <section v-if="candidate.safety_item" class="formal-item">
                <div><span>正式安全事项 · {{ candidate.safety_item.status }}</span><b>{{ candidate.safety_item.title }}</b></div>
                <dl><div><dt>风险等级</dt><dd>{{ candidate.safety_item.risk_level }}</dd></div><div><dt>责任对象</dt><dd>{{ candidate.safety_item.responsible_ref }}</dd></div><div><dt>位置</dt><dd>{{ candidate.safety_item.location }}</dd></div><div><dt>整改要求</dt><dd>{{ candidate.safety_item.rectification_requirement }}</dd></div></dl>
                <RouterLink class="btn btn-primary closure-link" :to="`/rectification?item=${candidate.safety_item.id}`">进入整改闭环 →</RouterLink>
              </section>
            </article>
            <article v-if="!selected.candidates.length" class="card empty">当前图片未发现具有充分视觉依据的明显安全隐患，仍需安全员按现场制度完成例行核查。</article>
          </section>
        </template>
      </main>
    </div>
  </section>
</template>

<style scoped>
.human-gate{padding:10px 14px;border:1px solid #a7d9ce;border-radius:999px;background:#ebfaf6;color:#176b58;font-size:11px;font-weight:800}.hazard-layout{display:grid;grid-template-columns:360px minmax(0,1fr);gap:18px;align-items:start}.left-column{display:grid;gap:16px;position:sticky;top:20px}.upload-panel{display:grid;gap:15px}.image-drop{position:relative;display:grid;place-items:center;min-height:210px;overflow:hidden;border:1px dashed #91b6dc;border-radius:15px;background:#f2f7fd;cursor:pointer}.image-drop input{position:absolute;inset:0;opacity:0;cursor:pointer}.image-drop img{width:100%;height:250px;object-fit:cover}.upload-placeholder{text-align:center;color:#315b83}.upload-placeholder b,.upload-placeholder small{display:block}.upload-placeholder small{margin-top:7px;color:var(--muted);font-size:10px}.form-field>span,.confirm-form label>span{font-size:11px;font-weight:750;color:#49627e}.description{min-height:105px;resize:vertical}.submit-button{width:100%}.history-panel{max-height:370px;overflow:auto}.history-item{width:100%;display:grid;grid-template-columns:minmax(0,1fr) auto;gap:9px;align-items:center;padding:11px 5px;border:0;border-top:1px solid #e8eef5;background:transparent;text-align:left}.history-item.active{color:#174c84}.history-item b,.history-item small{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.history-item b{font-size:11px}.history-item small{margin-top:4px;color:var(--muted);font-size:9px}.history-item em,.candidate-card header em{font-style:normal;font-size:9px;font-weight:800}.history-item em.ready{color:#a06100}.history-item em.failed{color:var(--red)}.history-item em.analyzing,.history-item em.queued{color:#2563eb}.result-column{display:grid;gap:16px}.result-empty{min-height:380px;display:grid;place-items:center}.inspection-summary{display:grid;grid-template-columns:220px 1fr;gap:20px;align-items:center}.inspection-image{height:150px;overflow:hidden;border-radius:13px;background:#e7edf5}.inspection-image img{width:100%;height:100%;object-fit:cover}.summary-top{display:flex;justify-content:space-between;align-items:center}.summary-top small{color:var(--muted)}.inspection-summary h2{margin:14px 0 7px;font-size:18px}.inspection-summary p{margin:0;color:var(--muted);font-size:11px}.analyzing{display:flex;gap:9px;align-items:center;margin-top:18px;color:#2563eb;font-size:11px}.analyzing i{width:11px;height:11px;border:2px solid #bcd6f4;border-top-color:#2563eb;border-radius:50%;animation:spin .8s linear infinite}.analysis-error{margin-top:14px;color:#a73530;font-size:11px}.facts-panel ul,.four-fields ul{margin:0;padding-left:18px}.facts-panel li,.four-fields li{margin:5px 0;font-size:11px;line-height:1.6}.unknown-block{display:grid;gap:6px;margin-top:14px;padding:13px;border-radius:12px;background:#fff8e7;color:#765719;font-size:10px}.candidate-heading{display:flex;justify-content:space-between;align-items:end;margin:8px 2px 12px}.candidate-heading h2{margin:0}.candidate-heading>span{font-size:11px;color:var(--muted)}.candidate-section{display:grid;gap:13px}.candidate-card{border-left:4px solid #f0ae35}.candidate-card.confirmed{border-left-color:var(--green)}.candidate-card.rejected{border-left-color:#a8b4c2;opacity:.78}.candidate-card header{display:flex;justify-content:space-between;gap:15px}.candidate-card header span{color:#2563eb;font-size:9px;font-weight:800}.candidate-card h3{margin:6px 0 0;font-size:16px;line-height:1.55}.candidate-card header em.pending{color:#a06100}.candidate-card header em.confirmed{color:var(--green)}.candidate-card header em.rejected{color:var(--muted)}.four-fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-top:17px}.four-fields section{padding:13px;border-radius:12px;background:#f5f8fc}.four-fields b{font-size:10px;color:#365a7d}.four-fields p{margin:8px 0 0;font-size:11px;line-height:1.6}.review-actions{display:flex;justify-content:flex-end;gap:9px;margin-top:16px}.confirm-form{display:grid;gap:12px;margin-top:18px;padding:17px;border:1px solid #bcd5ef;border-radius:14px;background:#f7fbff}.confirm-form label{display:grid;gap:6px}.confirm-form textarea{min-height:72px;resize:vertical}.confirm-title{display:flex;justify-content:space-between}.confirm-title b,.confirm-title small{display:block}.confirm-title small{margin-top:4px;color:var(--muted);font-size:9px}.confirm-title button{border:0;background:transparent;color:#60758d;font-size:22px}.form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.formal-item{margin-top:17px;padding:15px;border-radius:13px;background:#eaf8f3;border:1px solid #b9e1d4}.formal-item span,.formal-item b{display:block}.formal-item span{color:#27705c;font-size:9px;font-weight:800}.formal-item b{margin-top:5px;font-size:13px}.formal-item dl{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px;margin:13px 0 0}.formal-item dl div{padding-top:8px;border-top:1px solid #cfe8df}.formal-item dt{font-size:9px;color:#54746b}.formal-item dd{margin:4px 0 0;font-size:10px;line-height:1.5}.closure-link{display:flex;margin-top:12px;text-decoration:none}@keyframes spin{to{transform:rotate(360deg)}}@media(max-width:1100px){.hazard-layout{grid-template-columns:1fr}.left-column{position:static;grid-template-columns:1fr 1fr}.inspection-summary{grid-template-columns:180px 1fr}}@media(max-width:700px){.left-column{grid-template-columns:1fr}.inspection-summary,.four-fields,.form-grid,.formal-item dl{grid-template-columns:1fr}.inspection-image{height:220px}}

.human-gate,.upload-placeholder small,.form-field>span,.confirm-form label>span,
.history-item small,.history-item em,.candidate-card header em,.candidate-heading>span,
.candidate-card header span,.confirm-title small,.formal-item span,.formal-item dt{font-size:11px}
.history-item b,.inspection-summary p,.analyzing,.analysis-error,.unknown-block,
.four-fields b,.formal-item dd{font-size:12px}
.facts-panel li,.four-fields li,.four-fields p{font-size:13px}
</style>
