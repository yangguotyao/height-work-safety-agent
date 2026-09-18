<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { api } from "../api";

const audits = ref<any[]>([]);
const selected = ref<any>(null);
const file = ref<File | null>(null);
const useLlm = ref(true);
const uploading = ref(false);
const loadingDetail = ref(false);
const revisionFile = ref<File | null>(null);
const revisionUploading = ref(false);
const error = ref("");
let pollTimer: number | undefined;

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
const revisionStatusText: Record<string, string> = {
  analyzing: "AI 对比中",
  closed: "整改完成",
  needs_revision: "需继续修订",
  failed: "对比失败",
};
const revisionOutcomeText: Record<string, string> = {
  resolved: "已解决",
  partial: "部分解决",
  unresolved: "未解决",
  uncertain: "无法判断",
};
function outstandingDetails(revision: any) {
  return (revision?.comparison?.details || []).filter(
    (item: any) => item.outcome !== "resolved",
  );
}
function remainingIssueText(item: any) {
  return (item?.remaining_issues || []).map((remaining: any) => remaining.issue).join("；");
}
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
    const latestRevision = detail.revisions?.[0];
    if (terminal(detail.status) && latestRevision?.status !== "analyzing") {
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
}
async function choose(item: any) {
  stopPolling();
  selected.value = item;
  await refreshDetail(item.id);
  if (!terminal(selected.value.status)) startPolling(item.id);
}
async function upload() {
  if (!file.value) return;
  uploading.value = true;
  error.value = "";
  const body = new FormData();
  body.append("use_llm", String(useLlm.value));
  try {
    body.append("file", file.value);
    const result: any = await api("/audit-documents", { method: "POST", body });
    const filename = file.value.name;
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
function chooseRevisionFile(event: Event) {
  revisionFile.value = (event.target as HTMLInputElement).files?.[0] || null;
}
async function submitRevision() {
  if (!selected.value || !revisionFile.value || revisionUploading.value) return;
  revisionUploading.value = true;
  error.value = "";
  const body = new FormData();
  body.append("file", revisionFile.value);
  body.append("use_llm", String(useLlm.value));
  try {
    await api(`/api/v1/audits/${selected.value.id}/revisions`, {
      method: "POST",
      body,
    });
    revisionFile.value = null;
    startPolling(selected.value.id);
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "修订方案上传失败";
  } finally {
    revisionUploading.value = false;
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
        <h1>施工方案审查</h1>
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
          /><b>{{ file?.name || "选择施工方案文件" }}</b
          ><span>{{
            file ? "点击可重新选择" : "单文件最大 30 MB"
          }}</span></label
        ><label class="toggle-line"
          ><input v-model="useLlm" type="checkbox" /><span
            >使用已配置的大模型进行语义审查</span
          ></label
        ><button
          class="btn btn-primary"
          :disabled="!file || uploading"
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
          <h2>方案问题与处理建议</h2>
        </div>
        <span v-if="loadingDetail" class="muted">更新中…</span>
      </div>
      <section v-if="selected.findings?.length" class="revision-panel">
        <div class="revision-upload">
          <div>
            <small>整体方案整改</small>
            <h3>上传修订后的完整方案</h3>
            <p>AI只核验原审查问题是否解决，不扩展新的整改范围。</p>
          </div>
          <label class="revision-file" :class="{ selected: revisionFile }">
            <input type="file" accept=".doc,.docx" @change="chooseRevisionFile" />
            <span class="revision-file-copy">
              <b>{{ revisionFile?.name || "选择修订后的完整方案" }}</b>
            </span>
            <i>{{ revisionFile ? "已选择" : "浏览文件" }}</i>
          </label>
          <button class="btn btn-primary" :disabled="!revisionFile || revisionUploading || selected.revisions?.[0]?.status === 'analyzing'" @click="submitRevision">
            {{ revisionUploading ? "正在上传…" : "提交并自动对比" }}
          </button>
        </div>
        <details v-if="selected.revisions?.length" class="revision-history">
          <summary class="revision-history-title">
            <div><h3>方案整改记录</h3><small>点击查看每次方案整改对比</small></div>
            <span>{{ selected.revisions.length }} 次修订 <i></i></span>
          </summary>
          <div class="revision-history-body">
          <article v-for="(revision, revisionIndex) in selected.revisions" :key="revision.id" class="revision-result" :class="revision.status">
            <header>
              <div><small>第 {{ revision.attempt_no }} 次修订{{ revisionIndex === 0 ? " · 最新" : "" }}</small><h3>{{ revisionStatusText[revision.status] || revision.status }}</h3></div>
              <span>{{ revision.revised_filename }}</span>
            </header>
            <template v-if="revision.comparison?.result">
              <div class="revision-metrics">
                <span><b>{{ revision.comparison.original_finding_count }}</b>原问题</span>
                <span><b>{{ revision.comparison.resolved_count }}</b>已解决</span>
                <span><b>{{ revision.comparison.partial_count }}</b>部分解决</span>
                <span><b>{{ revision.comparison.unresolved_count + revision.comparison.uncertain_count }}</b>未解决/无法判断</span>
              </div>
              <p>{{ revision.comparison.summary }}</p>

              <section v-if="outstandingDetails(revision).length" class="outstanding-list">
                <div class="outstanding-title"><b>仍需修改的问题</b><span>{{ outstandingDetails(revision).length }} 项</span></div>
                <article v-for="item in outstandingDetails(revision)" :key="item.finding_id">
                  <header><b>{{ item.title }}</b><span :class="item.outcome">{{ revisionOutcomeText[item.outcome] || item.outcome }}</span></header>
                  <dl>
                    <div v-if="item.remaining_issues?.length" class="remaining-rules">
                      <dt>仍未通过的具体要求</dt>
                      <dd>
                        <article v-for="remaining in item.remaining_issues" :key="remaining.rule_id">
                          <b>{{ remaining.issue }}</b>
                          <p>{{ remaining.suggestion }}</p>
                          <small>{{ remaining.source_location }}</small>
                        </article>
                      </dd>
                    </div>
                    <div><dt>原审查问题</dt><dd>{{ item.original_issue }}</dd></div>
                    <div><dt>本次判断</dt><dd>{{ item.explanation }}</dd></div>
                    <div><dt>修订方案证据</dt><dd v-if="item.revised_evidence?.length"><p v-for="quote in item.revised_evidence" :key="quote">{{ quote }}</p></dd><dd v-else>修订方案中未找到足以证明该问题已解决的对应内容。</dd></div>
                  </dl>
                </article>
              </section>

              <details class="comparison-details">
                <summary>查看全部 {{ revision.comparison.details?.length || 0 }} 项逐项对比</summary>
                <div v-for="item in revision.comparison.details" :key="item.finding_id">
                  <b>{{ item.title }}</b><span :class="item.outcome">{{ revisionOutcomeText[item.outcome] || item.outcome }}</span>
                  <p><strong>原问题：</strong>{{ item.original_issue }}</p>
                  <p><strong>对比结论：</strong>{{ item.explanation }}</p>
                  <p v-if="item.remaining_issues?.length"><strong>仍未通过：</strong>{{ remainingIssueText(item) }}</p>
                  <p v-if="item.revised_evidence?.length"><strong>修订证据：</strong>{{ item.revised_evidence.join("；") }}</p>
                </div>
              </details>
            </template>
            <p v-else-if="revision.status === 'analyzing'" class="revision-wait"><i></i>正在复审修订方案并与原问题逐项对比…</p>
            <p v-else>{{ revision.comparison?.error || "本次对比未完成。" }}</p>
          </article>
          </div>
        </details>
      </section>
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
              <h3>处理建议</h3>
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
              <th>方案整改</th>
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
              <td>
                <span v-if="item.revision_count" class="revision-list-state" :class="item.revision_status">
                  {{ item.revision_count }} 次 · {{ revisionStatusText[item.revision_status] || item.revision_status }}
                </span>
                <span v-else class="muted">未提交</span>
              </td>
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
.revision-panel {
  margin: 15px 0 20px;
  padding: 16px;
  border: 1px solid #cddceb;
  border-radius: 14px;
  background: #f7fbff;
}
.revision-upload {
  display: grid;
  grid-template-columns: 0.85fr minmax(360px, 1.15fr) auto;
  gap: 16px;
  align-items: center;
}
.revision-upload small { color: #3978ad; font-weight: 800; }
.revision-upload h3 { margin: 4px 0; font-size: 16px; }
.revision-upload p { margin: 0; color: var(--muted); font-size: 11px; }
.revision-file {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
  min-height: 72px;
  border: 1px dashed #7da7ca;
  border-radius: 14px;
  padding: 12px 14px;
  background: linear-gradient(135deg, #fff 0%, #f1f8ff 100%);
  color: #315f88;
  cursor: pointer;
  transition: border-color .2s ease, box-shadow .2s ease, transform .2s ease;
}
.revision-file:hover {
  border-color: #2f7eb8;
  box-shadow: 0 8px 20px rgba(47, 126, 184, .11);
  transform: translateY(-1px);
}
.revision-file.selected { border-style: solid; border-color: #5a9d7d; background: #f3fbf7; }
.revision-file input { display: none; }
.revision-file-copy { min-width: 0; }
.revision-file-copy b { display: block; }
.revision-file-copy b { overflow: hidden; color: #204d70; font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
.revision-file > i { padding: 6px 9px; border-radius: 999px; background: #e5f1fa; color: #276b9d; font-size: 11px; font-style: normal; font-weight: 750; white-space: nowrap; }
.revision-result { margin-top: 14px; padding: 14px; border-radius: 12px; background: white; border-left: 4px solid #d29a31; }
.revision-result.closed { border-left-color: #2d966a; }
.revision-result.failed { border-left-color: #c84d45; }
.revision-result header { display: flex; justify-content: space-between; gap: 12px; align-items: center; }
.revision-result header small { color: var(--muted); font-size: 10px; }
.revision-result header h3 { margin: 4px 0 0; font-size: 17px; }
.revision-result header > span { color: #49677f; font-size: 11px; }
.revision-metrics { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin-top: 13px; }
.revision-metrics span { padding: 10px; border-radius: 9px; background: #f1f6fa; color: var(--muted); font-size: 10px; }
.revision-metrics b { display: block; color: #193c5c; font-size: 20px; margin-bottom: 2px; }
.revision-result > p { font-size: 12px; line-height: 1.7; }
.revision-history { margin-top: 16px; border: 1px solid #d7e4ee; border-radius: 12px; background: #fff; overflow: hidden; }
.revision-history-title { display: flex; justify-content: space-between; align-items: center; padding: 13px 15px; cursor: pointer; list-style: none; }
.revision-history-title::-webkit-details-marker { display: none; }
.revision-history-title:hover { background: #f3f8fc; }
.revision-history-title h3 { margin: 0; font-size: 15px; }
.revision-history-title small { display: block; margin-top: 3px; color: var(--muted); font-size: 11px; font-weight: 500; }
.revision-history-title > span { display: inline-flex; align-items: center; gap: 9px; color: var(--muted); font-size: 12px; }
.revision-history-title i { width: 8px; height: 8px; border-right: 2px solid #68849a; border-bottom: 2px solid #68849a; transform: rotate(45deg); transition: transform .2s ease; }
.revision-history[open] .revision-history-title i { transform: rotate(225deg); }
.revision-history-body { padding: 0 15px 15px; border-top: 1px solid #e4edf4; }
.revision-wait { display: flex; align-items: center; gap: 8px; }
.revision-wait i { width: 8px; height: 8px; border-radius: 50%; background: #d29a31; animation: pulse 1.4s infinite; }
.comparison-details { margin-top: 10px; }
.comparison-details > summary { cursor: pointer; color: #286a9d; font-size: 11px; font-weight: 750; }
.comparison-details > div { display: grid; grid-template-columns: 1fr auto; gap: 4px 10px; padding: 10px 2px; border-top: 1px solid #e7edf3; }
.comparison-details b { font-size: 11px; }
.comparison-details span { font-size: 10px; font-weight: 800; color: #a45d21; }
.comparison-details span.resolved { color: #247b58; }
.comparison-details p { grid-column: 1/-1; margin: 0; color: var(--muted); font-size: 11px; }
.comparison-details p + p { margin-top: 3px; }
.comparison-details strong { color: #345570; }
.outstanding-list { margin-top: 13px; padding: 13px; border: 1px solid #eed6a7; border-radius: 11px; background: #fff9ec; }
.outstanding-title { display: flex; justify-content: space-between; align-items: center; margin-bottom: 5px; }
.outstanding-title b { font-size: 13px; color: #82530b; }
.outstanding-title span { padding: 4px 7px; border-radius: 999px; background: #ffe8b5; color: #875607; font-size: 9px; font-weight: 800; }
.outstanding-list > article { padding: 11px 0; border-top: 1px solid #efdfbd; }
.outstanding-list > article > header { align-items: flex-start; }
.outstanding-list > article > header b { font-size: 12px; line-height: 1.5; }
.outstanding-list > article > header span { flex: none; font-size: 9px; font-weight: 800; color: #a45d21; }
.outstanding-list dl { display: grid; gap: 8px; margin: 10px 0 0; }
.outstanding-list dt { color: #806a45; font-size: 9px; font-weight: 800; }
.outstanding-list dd { margin: 3px 0 0; color: #4b5e6d; font-size: 11px; line-height: 1.6; }
.outstanding-list dd p { margin: 4px 0; padding: 7px 9px; border-radius: 7px; background: #fff; }
.remaining-rules dd { display: grid; gap: 7px; }
.remaining-rules dd article { padding: 9px 10px; border-left: 3px solid #e6a22d; border-radius: 7px; background: #fff; }
.remaining-rules dd article b,.remaining-rules dd article small { display: block; }
.remaining-rules dd article b { color: #68470e; font-size: 11px; line-height: 1.55; }
.remaining-rules dd article p { padding: 0; color: #526777; }
.remaining-rules dd article small { color: #8493a1; font-size: 9px; }
.revision-list-state { display: inline-block; padding: 5px 7px; border-radius: 7px; background: #fff4d9; color: #925e0c; font-size: 10px; font-weight: 750; white-space: nowrap; }
.revision-list-state.closed { background: #e5f6ee; color: #237153; }
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
.finding-state {
  display: flex;
  align-items: center;
  gap: 7px;
}
.finding-state i,
.finding-state em {
  font: 700 10px monospace;
  font-style: normal;
  white-space: nowrap;
}
.finding-state i {
  color: #a74d44;
  background: #fdebe8;
  border-radius: 7px;
  padding: 5px 7px;
}
.finding-state em {
  color: #3d617f;
  background: #edf3f8;
  border-radius: 7px;
  padding: 5px 7px;
}
.finding-state em.closed { background: #e6f5ec; color: #277853; }
.finding-state em.excluded { background: #f1f2f4; color: #6b7280; }
.finding-state em.processing,
.finding-state em.pending_review { background: #fff3da; color: #94650b; }
.finding-list summary > i {
  font: 700 10px monospace;
  font-style: normal;
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
.finding-body section.handling-panel {
  grid-column: 1/-1;
  background: #f8fafc;
  border: 1px solid #dfe7f1;
  padding: 15px;
}
.handling-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 15px;
}
.handling-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 7px;
}
.handling-actions .btn,
.handling-form .btn { font-size: 11px; padding: 8px 11px; }
.control-active { color: #207653; font-weight: 700; }
.handling-note { color: var(--muted); }
.handling-form { margin-top: 12px; }
.handling-form textarea {
  width: 100%;
  box-sizing: border-box;
  resize: vertical;
  border: 1px solid #cbd8e5;
  border-radius: 10px;
  padding: 10px 12px;
  font: inherit;
  font-size: 12px;
  line-height: 1.6;
}
.handling-form > div { display: flex; justify-content: flex-end; gap: 7px; margin-top: 8px; }
.handling-history { margin-top: 13px; border-top: 1px solid #e1e8ef; padding-top: 7px; }
.handling-history > div { display: grid; grid-template-columns: 10px 1fr; gap: 7px; padding-top: 8px; }
.handling-history > div > span { width: 7px; height: 7px; margin-top: 5px; border-radius: 50%; background: #4a86b8; }
.handling-history p { display: grid; grid-template-columns: auto 1fr; gap: 4px 10px; }
.handling-history b { font-size: 11px; }
.handling-history small { font-size: 10px; color: var(--muted); }
.handling-history em { grid-column: 1/-1; font-style: normal; font-size: 11px; color: #506579; }
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
  .basis,
  .finding-body section.handling-panel {
    grid-column: auto;
  }
  .finding-list summary { grid-template-columns: 28px 1fr; }
  .finding-state { grid-column: 2; }
  .handling-head { display: block; }
  .handling-actions { justify-content: flex-start; margin-top: 10px; }
  .revision-upload { grid-template-columns: 1fr; }
  .revision-metrics { grid-template-columns: 1fr 1fr; }
}

.audit-page small { font-size: 11px; }
.audit-page .upload-card span,
.audit-page .finding-state,
.audit-page .revision-upload p,
.audit-page .revision-result header > span,
.audit-page .revision-metrics span,
.audit-page .comparison-details > summary,
.audit-page .comparison-details b,
.audit-page .comparison-details span,
.audit-page .comparison-details p,
.audit-page .outstanding-title span,
.audit-page .outstanding-list dt,
.audit-page .outstanding-list dd,
.audit-page .revision-list-state,
.audit-page .handling-history b,
.audit-page .handling-history em { font-size: 12px; }
</style>
