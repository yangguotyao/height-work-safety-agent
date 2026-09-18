<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { api } from "../api";

const route = useRoute();
const items = ref<any[]>([]);
const detail = ref<any>(null);
const selectedId = ref("");
const evidenceFile = ref<File | null>(null);
const evidencePreview = ref("");
const evidenceDescription = ref("");
const reviewReason = ref("");
const reviewReasonInput = ref<HTMLTextAreaElement | null>(null);
const reviewError = ref("");
const actionNotice = ref("");
const busy = ref(false);
const error = ref("");
let pollTimer: number | undefined;

const latestSubmission = computed(() => detail.value?.submissions?.at(-1) || null);
const canSubmit = computed(() =>
  ["pending_rectification", "rectifying"].includes(detail.value?.order_status),
);
const canReview = computed(() => detail.value?.order_status === "pending_review");

function statusText(value: string) {
  return {
    processing: "处理中",
    closed: "已关闭",
    revoked: "已撤销",
    pending_rectification: "待整改",
    rectifying: "整改中",
    pending_review: "待复核",
  }[value] || value;
}

function riskText(value: string) {
  return { red: "重大", yellow: "较高", green: "一般" }[value] || value;
}

function comparisonStatus(value: string) {
  return {
    queued: "等待整改效果分析",
    analyzing: "正在分析整改效果",
    ready: "整改效果分析",
    failed: "暂未生成分析结果",
  }[value] || value;
}

function actorText(value?: string) {
  return value && /qwen|模型|model/i.test(value) ? "AI辅助识别" : value || "系统记录";
}

function stopPolling() {
  if (pollTimer) window.clearInterval(pollTimer);
  pollTimer = undefined;
}

async function refreshDetail() {
  if (!selectedId.value) return;
  detail.value = await api(`/api/v1/safety-items/${selectedId.value}`);
  if (!["queued", "analyzing"].includes(latestSubmission.value?.comparison_status)) {
    stopPolling();
  }
}

function startPolling() {
  stopPolling();
  pollTimer = window.setInterval(() => refreshDetail().catch(stopPolling), 1200);
}

async function selectItem(id: string) {
  selectedId.value = id;
  error.value = "";
  reviewError.value = "";
  actionNotice.value = "";
  stopPolling();
  try {
    await refreshDetail();
    if (["queued", "analyzing"].includes(latestSubmission.value?.comparison_status)) {
      startPolling();
    }
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "安全事项加载失败";
  }
}

function chooseEvidence(event: Event) {
  const next = (event.target as HTMLInputElement).files?.[0] || null;
  if (evidencePreview.value) URL.revokeObjectURL(evidencePreview.value);
  evidenceFile.value = next;
  evidencePreview.value = next ? URL.createObjectURL(next) : "";
}

async function submitEvidence() {
  if (!detail.value?.order_id || !evidenceFile.value || evidenceDescription.value.trim().length < 2) return;
  busy.value = true;
  error.value = "";
  const body = new FormData();
  body.append("file", evidenceFile.value);
  body.append("description", evidenceDescription.value.trim());
  try {
    detail.value = await api(`/api/v1/rectification-orders/${detail.value.order_id}/submissions`, {
      method: "POST",
      body,
    });
    evidenceFile.value = null;
    evidenceDescription.value = "";
    if (evidencePreview.value) URL.revokeObjectURL(evidencePreview.value);
    evidencePreview.value = "";
    await loadItems(false);
    startPolling();
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "整改证据提交失败";
  } finally {
    busy.value = false;
  }
}

async function review(result: "pass" | "return") {
  if (!detail.value?.order_id) return;
  reviewError.value = "";
  actionNotice.value = "";
  if (result === "return" && reviewReason.value.trim().length < 2) {
    reviewError.value = "请先填写退回原因（至少2个字），便于整改人员明确需要补充或修改的内容。";
    await nextTick();
    reviewReasonInput.value?.focus();
    return;
  }
  busy.value = true;
  error.value = "";
  try {
    detail.value = await api(`/api/v1/rectification-orders/${detail.value.order_id}/review`, {
      method: "POST",
      body: JSON.stringify({ result, reason: reviewReason.value.trim() }),
    });
    reviewReason.value = "";
    actionNotice.value = result === "return"
      ? "已退回整改，工单已重新进入整改中。"
      : "复核已通过，安全事项已关闭。";
    await loadItems(false);
  } catch (cause) {
    reviewError.value = cause instanceof Error ? cause.message : "人工复核失败";
  } finally {
    busy.value = false;
  }
}

async function loadItems(selectFirst = true) {
  items.value = await api<any[]>("/api/v1/safety-items?limit=100");
  if (!selectFirst) return;
  const requested = typeof route.query.item === "string" ? route.query.item : "";
  const initial = items.value.find((item) => item.id === requested) || items.value[0];
  if (initial) await selectItem(initial.id);
}

onMounted(async () => {
  try {
    await loadItems();
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "整改闭环加载失败";
  }
});

onBeforeUnmount(() => {
  stopPolling();
  if (evidencePreview.value) URL.revokeObjectURL(evidencePreview.value);
});
</script>

<template>
  <section class="page closure-page">
    <header class="page-heading">
      <div>
        <p class="eyebrow">RECTIFICATION CLOSED LOOP</p>
        <h1>现场安全隐患整改闭环</h1>
      </div>
      <RouterLink class="btn btn-primary" to="/hazards">＋ 现场上报</RouterLink>
    </header>

    <p v-if="error" class="closure-error">{{ error }}</p>

    <div class="closure-layout">
      <aside class="card item-list">
        <header><b>正式安全事项</b><span>{{ items.length }} 项</span></header>
        <button
          v-for="item in items"
          :key="item.id"
          :class="['item-row', { active: selectedId === item.id }]"
          @click="selectItem(item.id)"
        >
          <div><small>{{ item.item_no || "安全事项" }}</small><b>{{ item.title }}</b><span>{{ item.location }}</span></div>
          <em :class="item.status">{{ statusText(item.order_status || item.status) }}</em>
        </button>
        <div v-if="!items.length" class="empty-list">还没有经人工确认的安全事项</div>
      </aside>

      <main v-if="detail" class="detail-column">
        <section class="card summary-card">
          <header>
            <div><small>{{ detail.item_no }} · 现场多模态上报</small><h2>{{ detail.title }}</h2></div>
            <span :class="['status-badge', detail.status]">{{ statusText(detail.status) }}</span>
          </header>
          <div class="summary-grid">
            <div><span>风险等级</span><b>{{ riskText(detail.risk_level) }}</b></div>
            <div><span>位置</span><b>{{ detail.location }}</b></div>
            <div><span>责任对象</span><b>{{ detail.order_responsible_ref }}</b></div>
            <div><span>整改期限</span><b>{{ detail.order_due_at || "未限定" }}</b></div>
          </div>
        </section>

        <section class="card process-card">
          <div class="section-title"><div><small>01 / SOURCE & CONFIRM</small><h3>问题来源与人工确认</h3></div></div>
          <div class="before-grid">
            <img :src="detail.before_image_url" alt="整改前现场图片" />
            <div class="source-copy">
              <label>现场描述</label><p>{{ detail.onsite_description }}</p>
              <label>AI疑似识别</label><p class="ai-copy">{{ detail.suspected_hazard }}</p>
              <label>人工确认结论</label><p>{{ detail.fact_description }}</p>
              <small>确认人：{{ detail.confirmed_by }} · {{ detail.confirmed_at }}</small>
            </div>
          </div>
          <div class="basis-block">
            <b>相关规范依据</b>
            <article v-for="basis in detail.basis.slice(0, 1)" :key="basis.source_id">
              <span>{{ basis.standard_code }} · {{ basis.clause }}</span>
              <p>{{ basis.quote }}</p>
            </article>
            <p v-if="!detail.basis.length" class="muted">暂无匹配的规范条款。</p>
          </div>
        </section>

        <section class="card process-card">
          <div class="section-title"><div><small>02 / RECTIFICATION</small><h3>整改任务与证据</h3></div><span>{{ detail.order_no }} · {{ statusText(detail.order_status) }}</span></div>
          <div class="requirement"><b>整改要求</b><p>{{ detail.order_requirement }}</p></div>
          <form v-if="canSubmit" class="evidence-form" @submit.prevent="submitEvidence">
            <label class="evidence-image">
              <input type="file" accept="image/jpeg,image/png,image/webp" required @change="chooseEvidence" />
              <img v-if="evidencePreview" :src="evidencePreview" alt="整改后图片预览" />
              <span v-else><b>上传整改后图片</b><small>JPG / PNG / WEBP，首版单张</small></span>
            </label>
            <label class="evidence-copy"><span>整改说明</span><textarea v-model="evidenceDescription" class="input" required placeholder="说明采取了哪些整改措施"></textarea><button class="btn btn-primary" :disabled="busy || !evidenceFile">提交整改证据并启动AI对比</button></label>
          </form>

          <article v-for="submission in detail.submissions" :key="submission.id" class="submission-card">
            <header><b>第 {{ submission.attempt_no }} 次整改提交</b><span>{{ submission.submitted_by }} · {{ submission.submitted_at }}</span></header>
            <div class="compare-images"><figure><img :src="detail.before_image_url" alt="整改前" /><figcaption>整改前</figcaption></figure><figure><img :src="submission.image_url" alt="整改后" /><figcaption>整改后 · 第{{ submission.attempt_no }}次</figcaption></figure></div>
            <p class="submission-copy">{{ submission.description }}</p>
            <div :class="['comparison', submission.comparison_status]">
              <b>{{ comparisonStatus(submission.comparison_status) }}</b>
              <template v-if="submission.comparison_status === 'ready'">
                <section class="result-line"><span>整改结果</span><strong>{{ submission.comparison.rectification_result }}</strong></section>
                <section><span>主要改善</span><ul><li v-for="text in submission.comparison.main_improvements" :key="text">{{ text }}</li><li v-if="!submission.comparison.main_improvements.length">未观察到足以判断的明显变化</li></ul></section>
                <section><span>仍需关注</span><p>{{ submission.comparison.remaining_concern }}</p></section>
                <section class="suggestion-line"><span>复核建议</span><p>{{ submission.comparison.review_suggestion }}</p></section>
              </template>
            </div>
          </article>
        </section>

        <section v-if="canReview" class="card review-card">
          <div class="section-title"><div><small>03 / HUMAN REVIEW</small><h3>人工复核与最终销项</h3></div><span>整改提交不会自动关闭</span></div>
          <textarea ref="reviewReasonInput" v-model="reviewReason" :class="['input', { invalid: reviewError }]" placeholder="通过时可填写复核说明；退回时必须填写原因" @input="reviewError = ''"></textarea>
          <p v-if="reviewError" class="review-error" role="alert">{{ reviewError }}</p>
          <div><button class="btn btn-ghost" :disabled="busy" @click="review('return')">{{ busy ? "处理中…" : "退回整改" }}</button><button class="btn btn-primary" :disabled="busy" @click="review('pass')">{{ busy ? "处理中…" : "复核通过并关闭" }}</button></div>
        </section>

        <section class="card process-card">
          <div class="section-title"><div><small>04 / TRACEABILITY</small><h3>全过程事件时间线</h3></div><span>{{ detail.timeline.length }} 条记录</span></div>
          <ol class="timeline">
            <li v-for="event in detail.timeline" :key="event.id"><i></i><div><b>{{ event.title }}</b><p>{{ event.detail }}</p><small>{{ actorText(event.actor) }} · {{ event.at }}</small></div></li>
          </ol>
        </section>
      </main>
      <main v-else class="card empty-detail"><div><b>选择一条安全事项</b><p>查看从AI识别到整改关闭的完整链路。</p></div></main>
    </div>
    <div v-if="actionNotice" class="action-notice" role="status">{{ actionNotice }}</div>
  </section>
</template>

<style scoped>
.closure-page{max-width:1680px}.closure-error{padding:11px 14px;border-radius:10px;background:#fff0ee;color:#a13c36;font-size:11px}.closure-layout{display:grid;grid-template-columns:320px minmax(0,1fr);gap:16px;align-items:start}.item-list{position:sticky;top:18px;max-height:calc(100vh - 36px);overflow:auto;padding:12px}.item-list>header,.section-title,.summary-card>header,.submission-card>header{display:flex;justify-content:space-between;gap:14px;align-items:flex-start}.item-list>header{padding:7px 6px 13px}.item-list>header span,.section-title>span{font-size:10px;color:var(--muted)}.item-row{width:100%;display:flex;justify-content:space-between;gap:10px;padding:13px 9px;border:0;border-top:1px solid var(--line);background:transparent;text-align:left}.item-row.active{border-radius:11px;background:#edf5fd}.item-row div{min-width:0}.item-row small,.item-row b,.item-row span{display:block}.item-row small{font:800 9px monospace;color:#5887b1}.item-row b{margin:5px 0;font-size:12px;line-height:1.5}.item-row span{overflow:hidden;color:var(--muted);font-size:10px;text-overflow:ellipsis;white-space:nowrap}.item-row em{flex:none;font-style:normal;font-size:9px;font-weight:800;color:#b36a00}.item-row em.closed{color:var(--green)}.empty-list,.empty-detail{padding:30px;text-align:center;color:var(--muted)}.detail-column{display:grid;gap:14px}.summary-card,.process-card,.review-card{padding:20px}.summary-card small{font:800 9px monospace;color:#5887b1}.summary-card h2{margin:7px 0 0;font-size:21px}.status-badge{padding:7px 10px;border-radius:9px;background:#fff3d8;color:#a06100;font-size:10px;font-weight:800}.status-badge.closed{background:#e7f7f1;color:var(--green)}.summary-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:18px}.summary-grid div{padding:12px;border-radius:11px;background:#f4f7fa}.summary-grid span,.summary-grid b{display:block}.summary-grid span{font-size:9px;color:var(--muted)}.summary-grid b{margin-top:5px;font-size:11px}.section-title{margin-bottom:14px}.section-title small{font:800 9px monospace;color:#5887b1}.section-title h3{margin:4px 0 0;font-size:16px}.before-grid{display:grid;grid-template-columns:minmax(260px,.7fr) 1.3fr;gap:16px}.before-grid>img{width:100%;height:270px;object-fit:cover;border-radius:13px;background:#e7edf5}.source-copy{display:grid;align-content:start;gap:5px}.source-copy label{font-size:9px;font-weight:800;color:#54718d}.source-copy p{margin:0 0 8px;font-size:11px;line-height:1.7}.source-copy .ai-copy{padding:10px;border-left:3px solid #f0ae35;background:#fff8e8}.source-copy small{color:var(--muted);font-size:9px}.basis-block,.requirement{margin-top:14px;padding:14px;border-radius:12px;background:#f5f8fb}.basis-block>b,.requirement>b{font-size:11px}.basis-block article{margin-top:9px;padding-top:9px;border-top:1px solid #dfe7ef}.basis-block article span{font-size:10px;font-weight:800;color:#2563eb}.basis-block article p,.requirement p{margin:5px 0 0;font-size:10px;line-height:1.65;white-space:pre-wrap}.muted{color:var(--muted)}.evidence-form{display:grid;grid-template-columns:300px 1fr;gap:14px;margin-top:14px}.evidence-image{position:relative;display:grid;place-items:center;min-height:190px;overflow:hidden;border:1px dashed #8fb6dc;border-radius:13px;background:#f2f7fd;text-align:center}.evidence-image input{position:absolute;inset:0;opacity:0}.evidence-image img{width:100%;height:210px;object-fit:cover}.evidence-image span b,.evidence-image span small{display:block}.evidence-image span small{margin-top:7px;color:var(--muted);font-size:9px}.evidence-copy{display:grid;gap:7px}.evidence-copy>span{font-size:10px;font-weight:800}.evidence-copy textarea,.review-card textarea{min-height:110px;resize:vertical}.evidence-copy button{justify-self:end}.submission-card{margin-top:14px;padding:15px;border:1px solid var(--line);border-radius:14px}.submission-card>header b{font-size:12px}.submission-card>header span{font-size:9px;color:var(--muted)}.compare-images{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px}.compare-images figure{position:relative;margin:0;overflow:hidden;border-radius:11px;background:#e8edf2}.compare-images img{display:block;width:100%;height:260px;object-fit:cover}.compare-images figcaption{position:absolute;left:8px;bottom:8px;padding:5px 7px;border-radius:7px;background:rgba(14,35,54,.82);color:white;font-size:9px}.submission-copy{font-size:11px}.comparison{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;padding:13px;border-radius:12px;background:#eef5fc}.comparison>b{grid-column:1/-1;font-size:11px;color:#2563eb}.comparison section{padding:10px;border-radius:9px;background:white}.comparison section span{font-size:9px;font-weight:800}.comparison ul{margin:6px 0 0;padding-left:17px}.comparison li{margin:3px 0;font-size:10px;line-height:1.55}.comparison.failed{background:#fff0ee;color:#a13c36}.review-card{border:1px solid #add8ca;background:#f5fffb}.review-card>div:last-child{display:flex;justify-content:flex-end;gap:8px;margin-top:10px}.timeline{margin:0;padding:0;list-style:none}.timeline li{position:relative;display:grid;grid-template-columns:18px 1fr;gap:10px;padding-bottom:16px}.timeline li:not(:last-child)::before{content:"";position:absolute;left:6px;top:13px;bottom:0;width:1px;background:#cfdae5}.timeline i{position:relative;z-index:1;width:13px;height:13px;border:3px solid #d9e9f7;border-radius:50%;background:#2563eb}.timeline b{font-size:11px}.timeline p{margin:4px 0;font-size:10px;line-height:1.55}.timeline small{color:var(--muted);font-size:9px}@media(max-width:1100px){.closure-layout{grid-template-columns:1fr}.item-list{position:static;max-height:320px}.summary-grid{grid-template-columns:repeat(2,1fr)}}@media(max-width:720px){.before-grid,.evidence-form,.compare-images,.comparison,.summary-grid{grid-template-columns:1fr}.summary-card>header{display:block}.status-badge{display:inline-block;margin-top:10px}.before-grid>img,.compare-images img{height:230px}}
.item-list>header span,.section-title>span{font-size:12px}.item-row{padding:15px 11px}.item-row small{font-size:10px}.item-row b{font-size:14px}.item-row span{font-size:12px}.item-row em{font-size:11px}.summary-card small{font-size:11px}.summary-card h2{font-size:24px}.status-badge{font-size:12px}.summary-grid span{font-size:11px}.summary-grid b{font-size:13px}.section-title small{font-size:10px}.section-title h3{font-size:18px}.source-copy label{font-size:11px}.source-copy p{font-size:13px}.source-copy small{font-size:11px}.basis-block>b,.requirement>b{font-size:13px}.basis-block article span{font-size:12px}.basis-block article p,.requirement p{font-size:12px}.evidence-image span small{font-size:11px}.evidence-copy>span{font-size:12px}.submission-card>header b{font-size:14px}.submission-card>header span{font-size:11px}.compare-images figcaption{font-size:11px}.submission-copy{font-size:13px}.comparison{grid-template-columns:1fr 1fr;padding:16px;gap:10px}.comparison>b{font-size:14px}.comparison section{padding:13px}.comparison section span{font-size:11px}.comparison section p,.comparison li{font-size:12px;line-height:1.65}.comparison .result-line,.comparison .suggestion-line{grid-column:1/-1}.comparison .result-line{display:flex;align-items:center;justify-content:space-between;background:#e6f4ff}.comparison .result-line strong{font-size:18px;color:#1768aa}.comparison .suggestion-line p{margin-bottom:0}.timeline b{font-size:13px}.timeline p{font-size:12px}.timeline small{font-size:10px}
.basis-block article{width:100%;min-width:0}.basis-block article p{white-space:normal;overflow-wrap:anywhere}
.review-card textarea.invalid{border-color:#c9473d;box-shadow:0 0 0 3px rgba(201,71,61,.1)}.review-error{margin:8px 0 0;color:#a8322a;font-size:12px;line-height:1.55}.action-notice{position:fixed;right:28px;bottom:28px;z-index:30;padding:13px 18px;border:1px solid #9fd5c3;border-radius:12px;background:#effbf6;color:#17644d;font-size:13px;font-weight:750;box-shadow:0 12px 30px rgba(24,74,59,.16)}
</style>
