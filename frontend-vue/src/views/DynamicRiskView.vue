<script setup lang="ts">
import * as echarts from "echarts/core";
import { GraphChart } from "echarts/charts";
import { LegendComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import { api } from "../api";
import TaskChainDrawer from "../components/TaskChainDrawer.vue";

const graphEl = ref<HTMLElement | null>(null),
  data = ref<any>(null),
  selected = ref<any>(null),
  error = ref(""),
  loading = ref(false),
  chainTaskId = ref<string | null>(null);
echarts.use([GraphChart, LegendComponent, TooltipComponent, CanvasRenderer]);
let chart: ReturnType<typeof echarts.init> | null = null;
const colors: Record<string, string> = {
  项目: "#102a43",
  评估: "#38bdf8",
  任务: "#2563eb",
  风险: "#f59e0b",
  触发因素: "#e3514a",
  干预措施: "#10a477",
};
function riskColor(level: string) {
  return level === "red"
    ? "#dd4b45"
    : level === "yellow"
      ? "#e5a62a"
      : level === "green"
        ? "#2a9b6d"
        : "";
}
function levelName(level: string) {
  return level === "red"
    ? "红色预警"
    : level === "yellow"
      ? "重点关注"
      : "常规控制";
}
function changeName(status: string) {
  return status === "escalated"
    ? "风险升高"
    : status === "reduced"
      ? "风险降低"
      : status === "changed"
        ? "条件变化"
        : status === "new"
          ? "首次纳入"
          : "保持不变";
}
function draw() {
  if (!graphEl.value || !data.value) return;
  chart?.dispose();
  chart = echarts.init(graphEl.value);
  const categories = data.value.categories.map((name: string) => ({
    name,
    itemStyle: { color: colors[name] },
  }));
  const nodes = data.value.nodes.map((node: any) => ({
    ...node,
    category: data.value.categories.indexOf(node.category),
    itemStyle: { color: riskColor(node.riskLevel) || colors[node.category] },
    label: {
      show:
        node.category === "项目" ||
        node.category === "评估" ||
        node.category === "任务",
      color: "#18324f",
      fontSize: 9,
    },
    draggable: true,
  }));
  chart.setOption({
    backgroundColor: "transparent",
    tooltip: {
      trigger: "item",
      formatter: (p: any) =>
        p.dataType === "edge"
          ? p.data.label
          : `${p.data.categoryName || ""}<br><b>${String(p.data.name).replace(/[<>]/g, "")}</b>`,
    },
    legend: [
      {
        data: categories.map((x: any) => x.name),
        bottom: 4,
        textStyle: { fontSize: 9, color: "#60756e" },
      },
    ],
    series: [
      {
        type: "graph",
        layout: "force",
        roam: true,
        zoom: 0.88,
        categories,
        data: nodes,
        links: data.value.links.map((link: any) => ({
          ...link,
          lineStyle: { color: "#9db0a8", opacity: 0.55 },
          label: {
            show: true,
            formatter: link.label,
            fontSize: 7,
            color: "#799087",
          },
        })),
        force: { repulsion: 250, edgeLength: [75, 145], gravity: 0.07 },
        emphasis: { focus: "adjacency", lineStyle: { width: 2, opacity: 1 } },
        edgeSymbol: ["none", "arrow"],
        edgeSymbolSize: 5,
      },
    ],
  });
  chart.on("click", (event: any) => {
    if (event.dataType === "node") selected.value = event.data;
  });
}
async function load() {
  loading.value = true;
  error.value = "";
  try {
    data.value = await api("/api/v1/dynamic-risk/graph");
    await nextTick();
    draw();
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "加载失败";
  } finally {
    loading.value = false;
  }
}
function resize() {
  chart?.resize();
}
onMounted(() => {
  load();
  window.addEventListener("resize", resize);
});
onBeforeUnmount(() => {
  chart?.dispose();
  window.removeEventListener("resize", resize);
});
</script>

<template>
  <section class="page risk-page">
    <header class="page-heading">
      <div>
        <p class="eyebrow">DYNAMIC RISK AGENT</p>
        <h1>今日风险分析</h1>
      </div>
      <button class="btn btn-ghost" :disabled="loading" @click="load">
        {{ loading ? "加载中…" : "刷新当前版本" }}
      </button>
    </header>
    <div v-if="error" class="error-banner">{{ error }}</div>
    <div class="risk-summary">
      <div>
        <span class="risk-dot red"></span><b>{{ data?.run?.red_count || 0 }}</b
        ><small>红色预警</small>
      </div>
      <div>
        <span class="risk-dot yellow"></span
        ><b>{{ data?.run?.yellow_count || 0 }}</b
        ><small>重点关注</small>
      </div>
      <div>
        <span class="risk-dot green"></span
        ><b>{{ data?.run?.green_count || 0 }}</b
        ><small>常规控制</small>
      </div>
      <p>
        <b>{{ data?.run?.assessment_date || "今日" }}</b
        ><span>版本 {{ data?.run?.id?.slice(0, 8) || "尚未生成" }}</span>
      </p>
    </div>

    <div v-if="data?.run" class="version-banner">
      <div>
        <span>VERSION CHANGE</span>
        <b>{{ data.run.version_change?.summary || "当前版本暂无变化说明" }}</b>
      </div>
      <div class="version-stats">
        <span class="up">↑ {{ data.run.version_change?.escalated_count || 0 }} 升高</span>
        <span class="down">↓ {{ data.run.version_change?.reduced_count || 0 }} 降低</span>
        <span>± {{ data.run.version_change?.changed_count || 0 }} 条件变化</span>
      </div>
      <span v-if="data.run.data_quality?.merged_record_count" class="quality-chip">
        已合并 {{ data.run.data_quality.merged_record_count }} 条重复记录
      </span>
    </div>

    <div class="risk-workspace">
      <article class="card panel risk-list">
        <div class="section-head">
          <div>
            <p class="section-kicker">PRIORITY LIST</p>
            <h2>风险清单</h2>
          </div>
          <span class="list-count">{{ data?.run?.items?.length || 0 }} 项逻辑任务</span>
        </div>
        <div
          v-for="(item, index) in data?.run?.items || []"
          :key="item.id"
          class="risk-item"
          :class="item.risk_level"
        >
          <div class="risk-rank">
            <small>{{ String(Number(index) + 1).padStart(2, "0") }}</small>
            <span class="risk-level" :class="item.risk_level">{{ levelName(item.risk_level) }}</span>
            <strong>{{ item.priority_score }}</strong>
          </div>
          <div class="risk-main">
            <div class="risk-title">
              <div>
                <h3>{{ item.normalized_task || item.work_content }}</h3>
                <p>{{ [item.work_location, item.work_floor, item.work_time].filter(Boolean).join(" · ") }}</p>
              </div>
              <b>{{ item.scenes?.[0] || "高处作业" }}</b>
            </div>
            <div class="change-line" :class="item.change?.direction">
              <b>{{ changeName(item.change?.status) }}</b><span>{{ item.change?.explanation }}</span>
            </div>
            <p class="risk-summary-text">{{ item.summary }}</p>
            <section class="trigger-section">
              <h4>触发原因</h4>
              <ul>
                <li v-for="trigger in item.triggers" :key="trigger.code">
                  <span :class="trigger.level"></span><div><b>{{ trigger.title }}</b><p>{{ trigger.detail }}</p></div>
                </li>
              </ul>
            </section>
            <section class="interventions">
              <h4>建议措施</h4>
              <div><span v-for="measure in item.interventions" :key="measure">{{ measure }}</span></div>
            </section>
            <div class="risk-actions">
              <span v-if="item.duplicate_count > 1">已合并 {{ item.duplicate_count }} 条原始任务</span>
              <button @click="chainTaskId = item.task_id">查看任务全链路 →</button>
            </div>
          </div>
        </div>
        <p v-if="!data?.run?.items?.length" class="empty">当前没有正式任务风险项</p>
      </article>

      <article class="card graph-section">
        <div class="graph-heading"><span>KNOWLEDGE GRAPH</span><h2>风险关系图谱</h2></div>
        <div class="graph-card"><div ref="graphEl" class="graph-canvas"></div></div>
        <aside class="detail-panel">
          <template v-if="selected"><span class="node-type">{{ selected.category }}</span><h2>{{ selected.name }}</h2><div v-if="selected.riskLevel" class="level-line"><span class="risk-dot" :class="selected.riskLevel"></span>{{ levelName(selected.riskLevel) }}</div><pre>{{ JSON.stringify(selected.detail || {}, null, 2) }}</pre></template>
          <div v-else class="detail-empty"><span>⌁</span><h2>选择图谱节点</h2></div>
        </aside>
      </article>
    </div>
    <TaskChainDrawer :open="!!chainTaskId" :task-id="chainTaskId" @close="chainTaskId = null" />
  </section>
</template>

<style scoped>
.risk-summary {
  display: grid;
  grid-template-columns: 120px 120px 120px 1fr;
  gap: 8px;
  margin-bottom: 14px;
}
.risk-summary > div,
.risk-summary > p {
  background: rgba(255, 255, 255, 0.88);
  border: 1px solid var(--line);
  border-radius: 13px;
  padding: 13px;
  margin: 0;
}
.risk-summary > div {
  display: grid;
  grid-template-columns: 10px 1fr;
  align-items: center;
  column-gap: 8px;
}
.risk-summary > div b {
  font-size: 23px;
}
.risk-summary > div small {
  grid-column: 2;
  font-size: 10px;
  color: var(--muted);
}
.risk-summary > p {
  display: flex;
  flex-direction: column;
  justify-content: center;
}
.risk-summary > p b {
  font-size: 12px;
}
.risk-summary > p span {
  font-size: 10px;
  color: var(--muted);
  margin-top: 4px;
}
.version-banner {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  align-items: center;
  gap: 18px;
  margin-bottom: 14px;
  padding: 15px 18px;
  border: 1px solid #cddbed;
  border-radius: 16px;
  background: linear-gradient(115deg, #f8fbff, #edf5ff);
  box-shadow: 0 8px 22px rgba(26, 71, 121, 0.05);
}
.version-banner > div:first-child {
  display: grid;
  gap: 4px;
}
.version-banner > div:first-child > span,
.section-kicker {
  margin: 0;
  font: 800 8px monospace;
  letter-spacing: 0.14em;
  color: #2563eb;
}
.version-banner b {
  font-size: 11px;
}
.version-banner small {
  color: var(--muted);
  font-size: 9px;
}
.version-stats {
  display: flex;
  gap: 6px;
}
.version-stats span,
.quality-chip {
  padding: 6px 8px;
  border-radius: 8px;
  background: white;
  font-size: 9px;
  font-weight: 700;
  white-space: nowrap;
}
.version-stats .up {
  color: #b42318;
  background: #fff0ee;
}
.version-stats .down {
  color: #207356;
  background: #eaf8f1;
}
.quality-chip {
  color: #8a5b0a;
  background: #fff6dd;
}
.list-count {
  font: 700 10px monospace;
  background: #eef4fb;
  padding: 7px 9px;
  border-radius: 8px;
}
.risk-workspace {
  display: grid;
  grid-template-columns: minmax(0, 3fr) minmax(360px, 2fr);
  gap: 16px;
  align-items: start;
}
.risk-item {
  display: grid;
  grid-template-columns: 105px 1fr;
  border-top: 1px solid #e5ebf3;
  padding: 22px 0;
}
.risk-item:first-of-type {
  border-top: 0;
}
.risk-rank {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 9px;
  border-right: 1px solid #e2e9f2;
}
.risk-rank > small {
  font: 700 10px monospace;
  color: #9aaba5;
}
.risk-rank > strong {
  font: 800 24px monospace;
}
.risk-level {
  font: 700 8px monospace;
  border-radius: 7px;
  padding: 6px 8px;
  background: #edf1ee;
}
.risk-level.red {
  color: #a9322e;
  background: #fde8e6;
}
.risk-level.yellow {
  color: #94650b;
  background: #fff2d5;
}
.risk-level.green {
  color: #257554;
  background: #e2f5e9;
}
.risk-main {
  padding-left: 18px;
}
.risk-title {
  display: flex;
  justify-content: space-between;
  gap: 15px;
}
.risk-title h3 {
  font-size: 17px;
  margin: 0 0 5px;
}
.risk-title p {
  font-size: 10px;
  color: var(--muted);
  margin: 0;
}
.risk-title > b {
  height: max-content;
  font-size: 9px;
  background: #edf4fb;
  border-radius: 7px;
  padding: 6px 8px;
}
.change-line {
  display: grid;
  grid-template-columns: 72px 1fr;
  gap: 8px;
  margin-top: 12px;
  padding: 9px 10px;
  border-radius: 9px;
  background: #f1f5f9;
  font-size: 9px;
  line-height: 1.55;
}
.change-line.up {
  color: #9f302d;
  background: #fff0ee;
}
.change-line.down {
  color: #216c50;
  background: #eaf8f1;
}
.risk-summary-text {
  font-size: 12px;
  line-height: 1.7;
  background: #f5f8fc;
  border-radius: 9px;
  padding: 10px 12px;
  margin: 13px 0;
}
.risk-main section h4 {
  font-size: 10px;
  color: #526d8b;
  margin: 12px 0 7px;
}
.risk-main .trigger-section h4 {
  font-size: 13px;
}
.risk-main ul {
  list-style: none;
  padding: 0;
  margin: 0;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 7px;
}
.risk-main li {
  display: grid;
  grid-template-columns: 7px 1fr;
  gap: 7px;
  background: #fbfcfe;
  border: 1px solid #e6ebf2;
  border-radius: 9px;
  padding: 9px;
}
.risk-main li > span {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #85a097;
  margin-top: 3px;
}
.risk-main li > span.red {
  background: #dd4b45;
}
.risk-main li > span.yellow {
  background: #e5a62a;
}
.risk-main li b {
  font-size: 10px;
}
.risk-main li p {
  font-size: 9px;
  color: var(--muted);
  line-height: 1.5;
  margin: 3px 0;
}
.trigger-section li b {
  font-size: 12px;
}
.trigger-section li p {
  font-size: 12px;
  line-height: 1.65;
  margin-top: 4px;
}
.interventions > div {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.interventions span {
  font-size: 9px;
  background: #eaf4ff;
  color: #28567f;
  border-radius: 7px;
  padding: 7px 9px;
}
.risk-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px dashed #dbe4ef;
}
.risk-actions > span {
  margin-right: auto;
  font-size: 9px;
  color: #94650b;
}
.risk-actions button {
  border: 0;
  border-radius: 9px;
  padding: 8px 11px;
  background: #102a43;
  color: white;
  font-size: 9px;
  font-weight: 700;
}
.graph-section {
  position: sticky;
  top: 20px;
  overflow: hidden;
}
.graph-heading {
  padding: 20px 22px;
  border-bottom: 1px solid var(--line);
}
.graph-heading span {
  font: 700 8px monospace;
  color: #2563eb;
}
.graph-heading h2 {
  font-size: 16px;
  margin: 5px 0;
}
.graph-heading p {
  font-size: 10px;
  color: var(--muted);
  margin: 0;
}
.graph-card {
  position: relative;
  overflow: hidden;
  background:
    radial-gradient(
      circle at 50% 50%,
      rgba(37, 99, 235, 0.1),
      transparent 35%
    ),
    linear-gradient(#f8fbff, #edf3f9);
}
.graph-canvas {
  height: 500px;
}
.graph-hint {
  position: absolute;
  left: 14px;
  top: 14px;
  background: rgba(255, 255, 255, 0.85);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 6px 8px;
  font-size: 9px;
  color: var(--muted);
}
.detail-panel {
  padding: 18px;
  overflow: auto;
  min-height: 155px;
  max-height: 240px;
  border-top: 1px solid var(--line);
}
.node-type {
  font: 700 8px monospace;
  color: #245d91;
  background: #e8f2fc;
  padding: 5px 7px;
  border-radius: 6px;
}
.detail-panel h2 {
  font-size: 15px;
  line-height: 1.5;
  margin: 14px 0;
}
.level-line {
  font: 700 9px monospace;
  display: flex;
  align-items: center;
  gap: 7px;
}
.detail-panel pre {
  white-space: pre-wrap;
  word-break: break-word;
  background: #f2f6fa;
  border-radius: 10px;
  padding: 10px;
  font: 8px/1.65 monospace;
  color: #526d8b;
  margin-top: 14px;
}
.detail-empty {
  text-align: center;
  margin: 18px auto;
}
.detail-empty > span {
  font-size: 36px;
  color: #9cb1a9;
}
.detail-empty p {
  color: var(--muted);
  font-size: 9px;
  line-height: 1.7;
}
@media (max-width: 900px) {
  .version-banner {
    grid-template-columns: 1fr;
  }
  .risk-summary {
    grid-template-columns: repeat(3, 1fr);
  }
  .risk-summary > p {
    grid-column: 1/-1;
  }
  .risk-item {
    grid-template-columns: 1fr;
  }
  .risk-rank {
    border-right: 0;
    border-bottom: 1px solid var(--line);
    padding-bottom: 10px;
    flex-direction: row;
    align-items: center;
  }
  .risk-rank > strong {
    margin-left: auto;
  }
  .risk-main {
    padding: 14px 0 0;
  }
  .risk-workspace {
    grid-template-columns: 1fr;
  }
  .risk-list {
    order: 1;
  }
  .graph-section {
    order: 2;
    position: static;
  }
  .detail-panel {
    height: auto;
    min-height: 150px;
  }
  .detail-empty {
    margin: 25px auto;
  }
}
@media (max-width: 600px) {
  .risk-main ul {
    grid-template-columns: 1fr;
  }
  .risk-title {
    display: block;
  }
  .risk-title > b {
    display: inline-block;
    margin-top: 8px;
  }
  .graph-canvas {
    height: 320px;
  }
}
</style>
