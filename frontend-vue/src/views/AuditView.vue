<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { api } from "../api";

const audits = ref<any[]>([]);
const selected = ref<any>(null);
const file = ref<File | null>(null);
const sampleSelected = ref(false);
const useLlm = ref(true);
const uploading = ref(false);
const loadingDetail = ref(false);
const error = ref("");
let pollTimer: number | undefined;
const sampleFilename = "脚手架工程施工方案.doc";

const terminal = (status: string) =>
  ["completed", "failed", "cancelled"].includes(status);
const statusText: Record<string, string> = {
  pending: "等待启动",
  parsing: "解析文档",
  running: "审查中",
  auditing: "规则审查中",
  completed: "审查完成",
  failed: "审查失败",
};
const progress = computed(() => {
  const run = selected.value;
  if (!run) return 0;
  if (run.status === "completed") return 100;
  if (run.candidate_rule_count > 0)
    return Math.min(
      98,
      Math.round(((run.completed_rules || 0) / run.candidate_rule_count) * 100),
    );
  const node = String(run.current_node || "");
  if (node.includes("audit_rule")) return 72;
  if (node.includes("bundle")) return 58;
  if (node.includes("retriev")) return 44;
  if (node.includes("scene") || node.includes("scope")) return 28;
  return 10;
});

function elapsed(run: any) {
  if (run?.total_elapsed_seconds == null && run?.elapsed_seconds == null)
    return "—";
  const seconds = Number(
    run?.total_elapsed_seconds || run?.elapsed_seconds || 0,
  );
  if (seconds < 60) return `${seconds.toFixed(1)} 秒`;
  return `${Math.floor(seconds / 60)} 分 ${Math.round(seconds % 60)} 秒`;
}
function nodeText(node: string | null) {
  if (node === "no_applicable_scene") return "";
  return (node || "等待调度")
    .replaceAll("_", " ")
    .replace("audit rule bundles", "规则包审查");
}
async function load() {
  try {
    audits.value = await api("/api/v1/audits/recent");
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "加载失败";
  }
}
async function refreshDetail(id: string) {
  loadingDetail.value = true;
  try {
    const detail: any = await api(`/audits/${id}`);
    const row = audits.value.find((item) => item.id === id);
    selected.value = {
      ...row,
      ...detail,
      filename: row?.filename || selected.value?.filename || "施工方案",
    };
    const index = audits.value.findIndex((item) => item.id === id);
    if (index >= 0) audits.value[index] = { ...audits.value[index], ...detail };
    if (terminal(detail.status)) {
      stopPolling();
      await load();
    }
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "读取审查进度失败";
    stopPolling();
  } finally {
    loadingDetail.value = false;
  }
}
function stopPolling() {
  if (pollTimer !== undefined) window.clearInterval(pollTimer);
  pollTimer = undefined;
}
function startPolling(id: string) {
  stopPolling();
  void refreshDetail(id);
  pollTimer = window.setInterval(() => void refreshDetail(id), 1500);
}
function chooseLocalFile(event: Event) {
  file.value = (event.target as HTMLInputElement).files?.[0] || null;
  sampleSelected.value = false;
}
function chooseSample() {
  file.value = null;
  sampleSelected.value = true;
}
async function choose(item: any) {
  stopPolling();
  selected.value = item;
  await refreshDetail(item.id);
  if (!terminal(selected.value.status)) startPolling(item.id);
}
async function upload() {
  if (!file.value && !sampleSelected.value) return;
  uploading.value = true;
  error.value = "";
  const body = new FormData();
  body.append("use_llm", String(useLlm.value));
  try {
    let result: any;
    let filename: string;
    if (sampleSelected.value) {
      result = await api("/api/v1/audit-samples/scaffold", {
        method: "POST",
        body,
      });
      filename = sampleFilename;
    } else {
      body.append("file", file.value as File);
      result = await api("/audit-documents", { method: "POST", body });
      filename = (file.value as File).name;
    }
    const active = { ...result, filename, item_count: 0 };
    audits.value.unshift(active);
    selected.value = active;
    startPolling(result.id);
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "上传失败";
  } finally {
    uploading.value = false;
  }
}

onMounted(async () => {
  await load();
  if (audits.value[0]) await choose(audits.value[0]);
});
onBeforeUnmount(stopPolling);
</script>

<template>
  <section class="page audit-page">
    <header class="page-heading">
      <div>
        <p class="eyebrow">PLAN REVIEW AGENT</p>
        <h1>施工方案专项审查</h1>
      </div>
    </header>
    <div v-if="error" class="error-banner">{{ error }}</div>
    <div class="audit-grid">
      <article class="upload-card card">
        <div class="upload-mark">DOC/DOCX</div>
        <h2>上传施工方案</h2>
        <label class="file-drop"
          ><input
            type="file"
            accept=".doc,.docx"
            @change="chooseLocalFile"
          /><b>{{ file?.name || (sampleSelected ? sampleFilename : "选择施工方案文件") }}</b
          ><span>{{
            file || sampleSelected ? "点击可重新选择" : "单文件最大 30 MB"
          }}</span></label
        ><div class="audit-example">
          <button type="button" :class="{ selected: sampleSelected }" @click="chooseSample">
            <span><small>例如：</small>{{ sampleFilename }}</span><b>{{ sampleSelected ? "已选择" : "直接选择" }}</b>
          </button>
        </div
        ><label class="toggle-line"
          ><input v-model="useLlm" type="checkbox" /><span
            >使用已配置的大模型进行语义审查</span
          ></label
        ><button
          class="btn btn-primary"
          :disabled="(!file && !sampleSelected) || uploading"
          @click="upload"
        >
          {{ uploading ? "正在上传并解析…" : "开始审查" }}
        </button>
      </article>
      <article class="card panel live-panel">
        <template v-if="selected"
          ><div class="section-head">
            <div>
              <small>当前审查</small>
              <h2>{{ selected.filename }}</h2>
            </div>
            <span class="audit-status" :class="selected.status">{{
              statusText[selected.status] || selected.status
            }}</span>
          </div>
          <div class="progress-track">
            <i :style="{ width: `${progress}%` }"></i>
          </div>
          <div class="progress-meta">
            <b>{{ progress }}%</b
            ><span>{{ nodeText(selected.current_node) }}</span>
          </div>
          <dl class="run-metrics">
            <div>
              <dt>已完成规则</dt>
              <dd>
                {{ selected.completed_rules ?? 0 }} /
                {{ selected.candidate_rule_count ?? "—" }}
              </dd>
            </div>
            <div>
              <dt>规则包</dt>
              <dd>
                {{ selected.completed_bundles ?? "—" }} /
                {{ selected.bundle_count ?? "—" }}
              </dd>
            </div>
            <div>
              <dt>业务问题</dt>
              <dd>
                {{ selected.findings?.length ?? "—" }}
              </dd>
            </div>
            <div>
              <dt>处理耗时</dt>
              <dd>{{ elapsed(selected) }}</dd>
            </div>
          </dl>
          <p v-if="selected.error" class="run-error">{{ selected.error }}</p>
          <p v-else-if="!terminal(selected.status)" class="live-note">
            <span></span>系统正在处理，本页每 1.5 秒自动更新
          </p>
          <p v-else class="live-note done">
            <span></span>审查已完成，结果已固定并保留证据链
          </p></template
        >
        <div v-else class="empty">上传方案后，这里会实时显示审查进度。</div>
      </article>
    </div>
    <article v-if="selected" class="card panel findings-panel">
      <div class="section-head">
        <div>
          <h2>业务问题与整改建议</h2>
        </div>
        <span v-if="loadingDetail" class="muted">更新中…</span>
      </div>
      <div v-if="selected.findings?.length" class="finding-list">
        <details
          v-for="(finding, index) in selected.findings"
          :key="finding.id"
          :open="index === 0"
        >
          <summary>
            <span>{{ String(Number(index) + 1).padStart(2, "0") }}</span>
            <div>
              <b>{{ finding.title }}</b
              ><small
                >{{ finding.scene }} ·
                {{ finding.source_location || "方案全文" }}</small
              >
            </div>
            <i>{{ finding.result }}</i>
          </summary>
          <div class="finding-body">
            <section>
              <h3>发现的问题</h3>
              <p>{{ finding.issue }}</p>
            </section>
            <section>
              <h3>可能后果</h3>
              <p>{{ finding.risk_consequence }}</p>
            </section>
            <section class="suggestion">
              <h3>整改建议</h3>
              <p>{{ finding.suggestion }}</p>
            </section>
            <blockquote v-if="finding.plan_quote">
              方案原文：{{ finding.plan_quote }}
            </blockquote>
            <div class="basis" v-if="finding.basis?.length">
              <span
                v-for="basis in finding.basis.slice(0, 4)"
                :key="basis.standard_code + String(basis.clause)"
                >{{ basis.standard_code || basis.title }}
                {{ basis.clause || "" }}</span
              >
            </div>
          </div>
        </details>
      </div>
      <div v-else-if="!terminal(selected.status)" class="empty">
        审查进行中，业务问题将在确定性校验完成后逐步出现。
      </div>
      <div v-else class="empty">本次审查没有形成需要展示的业务问题。</div>
    </article>
    <article class="card panel history">
      <div class="section-head">
        <h2>最近审查</h2>
        <button class="btn btn-ghost" @click="load">同步列表</button>
      </div>
      <div class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>方案文件</th>
              <th>状态</th>
              <th>当前节点</th>
              <th>审查项</th>
              <th>创建时间</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="item in audits"
              :key="item.id"
              :class="{ active: selected?.id === item.id }"
              @click="choose(item)"
            >
              <td>
                <b>{{ item.filename }}</b
                ><small>{{ item.id.slice(0, 8) }}</small>
              </td>
              <td>
                <span class="audit-status" :class="item.status">{{
                  statusText[item.status] || item.status
                }}</span>
              </td>
              <td>{{ nodeText(item.current_node) }}</td>
              <td>{{ item.item_count ?? 0 }}</td>
              <td>{{ new Date(item.created_at).toLocaleString("zh-CN") }}</td>
            </tr>
          </tbody>
        </table>
        <p v-if="!audits.length" class="empty">暂无审计记录</p>
      </div>
    </article>
  </section>
</template>

<style scoped>
.audit-grid {
  display: grid;
  grid-template-columns: 0.9fr 1.1fr;
  gap: 14px;
  margin-bottom: 14px;
}
.upload-card {
  padding: 25px;
}
.upload-mark {
  font: 800 12px monospace;
  color: var(--forest);
  background: var(--mint);
  display: grid;
  place-items: center;
  width: 86px;
  height: 45px;
  border-radius: 14px;
}
.upload-card h2 {
  font-size: 21px;
  margin: 16px 0 7px;
}
.upload-card > p {
  font-size: 11px;
  color: var(--muted);
  line-height: 1.7;
}
.file-drop {
  border: 1px dashed #9db0a8;
  border-radius: 13px;
  padding: 15px;
  display: block;
  margin: 17px 0;
  background: #f7faf8;
}
.file-drop input {
  display: none;
}
.file-drop b,
.file-drop span {
  display: block;
}
.file-drop b {
  font-size: 12px;
}
.file-drop span {
  font-size: 9px;
  color: var(--muted);
  margin-top: 4px;
}
.audit-example {
  margin: -7px 0 15px;
}
.audit-example > button {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  flex: 1;
  padding: 9px 11px;
  border: 1px solid #c9d9e9;
  border-radius: 10px;
  background: #edf5ff;
  color: #284f78;
  font-size: 11px;
  text-align: left;
}
.audit-example span {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
.audit-example small {
  color: var(--muted);
  font-size: 10px;
}
.audit-example > button:hover,
.audit-example > button.selected {
  border-color: #60a5fa;
  background: #e4f1ff;
}
.audit-example b {
  flex: none;
  color: #2563eb;
  font-size: 10px;
}
.toggle-line {
  display: flex;
  gap: 8px;
  align-items: center;
  font-size: 10px;
  margin-bottom: 15px;
}
.upload-card > .btn {
  width: 100%;
}
.live-panel {
  background: radial-gradient(circle at 90% 0, rgba(56, 189, 248, 0.18), transparent 36%), linear-gradient(145deg, #10233f, #174d7b);
  color: #fff;
}
.live-panel .section-head small {
  color: #99b8ae;
  font-size: 11px;
}
.live-panel h2 {
  margin-top: 5px;
  max-width: 360px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.progress-track {
  height: 7px;
  background: rgba(255, 255, 255, 0.12);
  border-radius: 10px;
  overflow: hidden;
  margin-top: 25px;
}
.progress-track i {
  display: block;
  height: 100%;
  background: var(--mint);
  border-radius: 10px;
  transition: width 0.4s;
}
.progress-meta {
  display: flex;
  justify-content: space-between;
  margin-top: 8px;
  font-size: 11px;
  color: #a9c1b9;
}
.progress-meta b {
  font: 700 15px monospace;
  color: var(--mint);
}
.run-metrics {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  margin: 24px 0 15px;
}
.run-metrics div {
  padding: 11px;
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 10px;
}
.run-metrics dt {
  font-size: 11px;
  color: #9bb9af;
}
.run-metrics dd {
  font: 700 17px monospace;
  margin-top: 7px;
}
.live-note {
  font-size: 11px;
  color: #aec4bd;
  display: flex;
  align-items: center;
  gap: 7px;
}
.live-note span {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--mint);
  box-shadow: 0 0 0 5px rgba(56, 189, 248, 0.14);
  animation: pulse 1.4s infinite;
}
.live-note.done span {
  animation: none;
}
.run-error {
  color: #ffd5d1;
  font-size: 10px;
}
.findings-panel {
  margin-bottom: 14px;
}
.finding-list details {
  border-top: 1px solid #e6ece8;
}
.finding-list summary {
  list-style: none;
  cursor: pointer;
  display: grid;
  grid-template-columns: 34px 1fr auto;
  gap: 12px;
  padding: 15px 2px;
  align-items: center;
}
.finding-list summary > span {
  font: 700 11px monospace;
  color: #91a59e;
}
.finding-list summary b,
.finding-list summary small {
  display: block;
}
.finding-list summary b {
  font-size: 14px;
}
.finding-list summary small {
  font-size: 11px;
  color: var(--muted);
  margin-top: 4px;
}
.finding-list summary i {
  font: 700 10px monospace;
  color: #a74d44;
  background: #fdebe8;
  border-radius: 7px;
  padding: 5px 7px;
}
.finding-body {
  padding: 0 44px 17px;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}
.finding-body section {
  background: #f5f8f6;
  border-radius: 10px;
  padding: 12px;
}
.finding-body section.suggestion {
  grid-column: 1/-1;
  background: #edf6ff;
}
.finding-body h3 {
  font-size: 12px;
  margin: 0 0 7px;
  color: #537269;
}
.finding-body p {
  font-size: 13px;
  line-height: 1.75;
  margin: 0;
}
.finding-body blockquote {
  grid-column: 1/-1;
  margin: 0;
  border-left: 3px solid #a7bf8c;
  padding: 8px 11px;
  background: #fafcf9;
  font-size: 11px;
  line-height: 1.65;
  color: var(--muted);
}
.basis {
  grid-column: 1/-1;
  display: flex;
  gap: 5px;
  flex-wrap: wrap;
}
.basis span {
  font-size: 10px;
  padding: 5px 7px;
  background: #eaf1ed;
  border-radius: 6px;
}
.history td:first-child b,
.history td:first-child small {
  display: block;
}
.history td:first-child small {
  font: 8px monospace;
  color: var(--muted);
  margin-top: 3px;
}
.history tbody tr {
  cursor: pointer;
}
.history tbody tr:hover,
.history tbody tr.active {
  background: #f1f7ed;
}
.audit-status {
  font: 700 11px monospace;
  padding: 7px 10px;
  border-radius: 7px;
  background: #edf2ef;
  color: #60746c;
}
.audit-status.completed {
  background: #e2f5e9;
  color: #277853;
}
.audit-status.failed {
  background: #fde9e6;
  color: #b23f39;
}
.audit-status.auditing,
.audit-status.running {
  background: #fff2d6;
  color: #8c6414;
}
@keyframes pulse {
  50% {
    opacity: 0.35;
  }
}
@media (max-width: 850px) {
  .audit-grid {
    grid-template-columns: 1fr;
  }
  .finding-body {
    grid-template-columns: 1fr;
    padding-left: 0;
    padding-right: 0;
  }
  .finding-body section.suggestion {
    grid-column: auto;
  }
  .finding-body blockquote,
  .basis {
    grid-column: auto;
  }
}
</style>
