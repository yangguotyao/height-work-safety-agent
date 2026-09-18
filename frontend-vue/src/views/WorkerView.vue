<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { api } from "../api";
import TaskChainDrawer from "../components/TaskChainDrawer.vue";

const router = useRouter();
const tasks = ref<any[]>([]);
const error = ref("");
const busy = ref(false);
const taskInput = ref("");
const sessionId = ref<string | null>(null);
const taskMessages = ref<any[]>([]);
const draft = ref<any>(null);
const riskCard = ref<any>(null);
const dynamicRisk = ref<any>(null);
const chainTaskId = ref<string | null>(null);
const listening = ref(false);
let recognition: RecognitionLike | null = null;

type RecognitionLike = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  start: () => void;
  stop: () => void;
  onresult: ((event: { results: ArrayLike<{ 0: { transcript: string } }> }) => void) | null;
  onerror: (() => void) | null;
  onend: (() => void) | null;
};

const stage = computed(() => (riskCard.value ? 4 : draft.value ? 2 : taskMessages.value.length ? 1 : 0));
const speechSupported = computed(() => {
  const target = window as typeof window & {
    SpeechRecognition?: new () => RecognitionLike;
    webkitSpeechRecognition?: new () => RecognitionLike;
  };
  return Boolean(target.SpeechRecognition || target.webkitSpeechRecognition);
});
const riskTags = computed(() => riskCard.value?.scenes?.slice(0, 4) || draft.value?.scenes?.slice(0, 4) || []);
const evidenceSources = computed(() => {
  const items = riskCard.value?.evidences || [];
  const seen = new Set<string>();
  const unique = items.filter((item: any) => {
    const key = `${item.evidence_type}:${item.title}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  const diverse = ["audit", "standard", "accident", "weather"]
    .map((type) => unique.find((item: any) => item.evidence_type === type))
    .filter(Boolean);
  return [...diverse, ...unique.filter((item: any) => !diverse.includes(item))].slice(0, 5);
});

function evidenceType(type: string) {
  return { standard: "规范制度", audit: "施工方案", accident: "案例库", weather: "环境信息" }[type] || "知识来源";
}

function levelName(level: string) {
  return { red: "红色预警", yellow: "重点关注", green: "常规控制" }[level] || "待分析";
}

function warningName(level: string) {
  return { stop: "暂停条件", warning: "不利条件", confirm: "现场确认", info: "未触发限制" }[level] || "天气提示";
}

async function loadDynamicRisk(taskId: string) {
  dynamicRisk.value = null;
  const task = tasks.value.find((item) => item.id === taskId);
  if (!task?.scheduled_date) return;
  try {
    const run: any = await api(`/dynamic-risks/latest?assessment_date=${encodeURIComponent(task.scheduled_date)}`);
    dynamicRisk.value = run?.items?.find(
      (item: any) => item.task_id === taskId || item.merged_task_ids?.includes(taskId),
    ) || null;
  } catch {
    dynamicRisk.value = null;
  }
}

async function load() {
  try {
    tasks.value = await api("/api/v1/tasks/recent");
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "加载失败";
  }
}

async function sendTask() {
  const message = taskInput.value.trim();
  if (!message || busy.value) return;
  taskMessages.value.push({ role: "user", text: message });
  taskInput.value = "";
  busy.value = true;
  error.value = "";
  try {
    const result: any = await api("/worker-assistant/messages", {
      method: "POST",
      body: JSON.stringify({
        session_id: sessionId.value,
        message,
        worker_ref: "项目作业人员",
        team_ref: "现场作业班组",
        use_llm: null,
      }),
    });
    sessionId.value = result.session_id;
    draft.value = result.draft;
    riskCard.value = result.risk_card || riskCard.value;
    taskMessages.value.push({ role: "assistant", text: result.assistant_message });
    if (result.status === "completed") {
      await load();
      if (result.task_id) await loadDynamicRisk(result.task_id);
    }
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "任务对话失败";
  } finally {
    busy.value = false;
  }
}

function newTask(seed = "") {
  sessionId.value = null;
  taskMessages.value = [];
  draft.value = null;
  riskCard.value = null;
  dynamicRisk.value = null;
  taskInput.value = seed;
}

function startVoice() {
  if (listening.value) {
    recognition?.stop();
    return;
  }
  const target = window as typeof window & {
    SpeechRecognition?: new () => RecognitionLike;
    webkitSpeechRecognition?: new () => RecognitionLike;
  };
  const Recognition = target.SpeechRecognition || target.webkitSpeechRecognition;
  if (!Recognition) {
    error.value = "当前浏览器不支持语音识别，请使用文字输入。";
    return;
  }
  recognition = new Recognition();
  recognition.lang = "zh-CN";
  recognition.continuous = false;
  recognition.interimResults = false;
  recognition.onresult = (event) => {
    const transcript = event.results[0]?.[0]?.transcript?.trim();
    if (transcript) taskInput.value = transcript;
  };
  recognition.onerror = () => {
    error.value = "没有听清，请靠近麦克风后重试。";
  };
  recognition.onend = () => {
    listening.value = false;
  };
  error.value = "";
  listening.value = true;
  recognition.start();
}

function returnToEdit() {
  const seed = riskCard.value?.normalized_task || riskCard.value?.work_content || "";
  newTask(seed);
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function enterInspection() {
  if (!riskCard.value?.task_id) return;
  router.push({ path: "/hazards", query: { task: riskCard.value.task_id } });
}

async function showRisk(task: any) {
  error.value = "";
  try {
    riskCard.value = await api(`/worker-assistant/risk-cards/${task.id}`);
    draft.value = {
      normalized_task: riskCard.value.normalized_task,
      work_content: riskCard.value.work_content,
      location: riskCard.value.location,
      floor: riskCard.value.floor,
      work_time: riskCard.value.work_time,
      scenes: riskCard.value.scenes,
    };
    await loadDynamicRisk(task.id);
    window.scrollTo({ top: 0, behavior: "smooth" });
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "该任务暂未生成风险提示卡";
  }
}

onMounted(load);
onBeforeUnmount(() => recognition?.stop());
</script>

<template>
  <section class="page worker-page">
    <header class="page-heading">
      <div>
        <p class="eyebrow">PRE-JOB RISK ANALYSIS</p>
        <h1>班前风险分析</h1>
      </div>
    </header>

    <nav class="flow-steps" aria-label="班前风险分析流程">
      <div :class="{ active: stage === 0, done: stage > 0 }"><b>1</b><span>描述作业</span></div><i></i>
      <div :class="{ active: stage === 1, done: stage > 1 }"><b>2</b><span>生成任务卡</span></div><i></i>
      <div :class="{ active: stage === 2, done: stage > 3 }"><b>3</b><span>多源风险分析</span></div><i></i>
      <div :class="{ active: stage === 4 }"><b>4</b><span>班前风险提示卡</span></div>
    </nav>

    <div v-if="error" class="error-banner">{{ error }}</div>

    <div class="task-workspace">
      <article class="card chat-card">
        <div class="section-head">
          <div><p class="section-kicker">作业录入</p><h2>描述今天的作业</h2></div>
          <button class="btn btn-ghost" @click="newTask()">新任务</button>
        </div>
        <div class="task-chat">
          <div v-if="!taskMessages.length" class="task-welcome">
            <button @click="taskInput = '今天白班，架子班8人在3号楼6层拆除外脚手架'">今天白班，架子班8人在3号楼6层拆除外脚手架</button>
          </div>
          <div v-for="(message, index) in taskMessages" :key="index" class="chat-line" :class="message.role">
            <span>{{ message.role === "user" ? "我" : "安" }}</span><p>{{ message.text }}</p>
          </div>
        </div>

        <section v-if="draft" class="task-draft">
          <div class="draft-title"><b>作业任务卡</b><span>{{ riskCard ? "已完成" : "信息提取中" }}</span></div>
          <dl>
            <div><dt>作业内容</dt><dd>{{ draft.normalized_task || draft.work_content || "待补充" }}</dd></div>
            <div><dt>作业位置</dt><dd>{{ [draft.location, draft.floor].filter(Boolean).join(" · ") || "待补充" }}</dd></div>
            <div><dt>作业班组</dt><dd>{{ riskCard?.team_ref || "现场作业班组" }}</dd></div>
            <div><dt>作业时间</dt><dd>{{ draft.work_time || "待补充" }}</dd></div>
          </dl>
          <div v-if="riskTags.length" class="tag-line"><span v-for="tag in riskTags" :key="tag">{{ tag }}</span></div>
        </section>

        <div class="task-composer">
          <textarea v-model="taskInput" rows="2" placeholder="例如：今天白班，架子班8人在3号楼6层拆除外脚手架" @keydown.ctrl.enter="sendTask"></textarea>
          <button class="voice-input" :class="{ listening }" :disabled="!speechSupported" @click="startVoice">{{ listening ? "停止录音" : "语音输入" }}</button>
          <button :disabled="busy || !taskInput.trim()" @click="sendTask">{{ busy ? "分析中…" : "生成风险分析" }}</button>
        </div>
      </article>

      <article v-if="riskCard" class="card risk-card">
        <div class="risk-card-head">
          <div><span>PRE-JOB SAFETY CARD</span><h2>班前风险提示卡</h2><p>任务已结构化，风险已结合项目多源资料生成</p></div>
          <button @click="chainTaskId = riskCard.task_id">查看全链路</button>
        </div>
        <div class="risk-summary">
          <div><small>今日作业</small><strong>{{ riskCard.normalized_task || riskCard.work_content }}</strong></div>
          <div><small>作业区域</small><strong>{{ [riskCard.location, riskCard.floor].filter(Boolean).join(" · ") }}</strong></div>
          <div><small>班组与时间</small><strong>{{ riskCard.team_ref || "现场作业班组" }} · {{ riskCard.work_time }}</strong></div>
        </div>
        <div v-if="riskTags.length" class="risk-tags"><span v-for="tag in riskTags" :key="tag">{{ tag }}</span></div>

        <section class="environment-strip">
          <div class="dynamic-level" :class="dynamicRisk?.risk_level || 'green'">
            <small>当前动态风险</small><b>{{ levelName(dynamicRisk?.risk_level || "green") }}</b>
            <p>{{ dynamicRisk?.summary || "未触发红黄条件，仍需执行班前控制措施。" }}</p>
          </div>
          <div class="weather-card">
            <div class="weather-title"><span>作业时段天气</span><b>{{ riskCard.weather?.status === "ok" ? "已接入实时数据" : "需要现场复核" }}</b></div>
            <p>{{ riskCard.weather?.summary }}</p>
            <div class="weather-metrics">
              <span v-if="riskCard.weather?.temperature_c != null"><small>温度</small><b>{{ riskCard.weather.temperature_c }}℃</b></span>
              <span v-if="riskCard.weather?.max_wind_speed_kmh != null"><small>最大风速</small><b>{{ riskCard.weather.max_wind_speed_kmh }} km/h</b></span>
              <span v-if="riskCard.weather?.precipitation != null"><small>降水强度</small><b>{{ riskCard.weather.precipitation }}</b></span>
            </div>
          </div>
        </section>

        <section v-if="riskCard.weather_warnings?.length" class="weather-decisions">
          <div v-for="item in riskCard.weather_warnings.slice(0, 2)" :key="item.message" :class="item.level">
            <span>{{ warningName(item.level) }}</span><p>{{ item.message }}</p><small v-if="item.standard_code">{{ item.standard_code }} {{ item.clause }}</small>
          </div>
        </section>

        <section v-if="dynamicRisk?.triggers?.length" class="card-section trigger-list">
          <div class="mini-head"><h3>风险触发原因</h3><small>天气、方案状态等条件可动态改变风险等级</small></div>
          <div v-for="item in dynamicRisk.triggers.slice(0, 3)" :key="`${item.code}:${item.source_id}`">
            <span :class="item.level"></span><p><b>{{ item.title }}</b><small>{{ item.detail }}</small></p>
          </div>
        </section>

        <section class="card-section risk-list">
          <div class="mini-head"><h3>主要风险</h3><small>优先展示与当前作业直接相关的重点风险</small></div>
          <div v-for="(item, index) in riskCard.main_risks?.slice(0, 4)" :key="item" class="risk-row">
            <b>{{ String(Number(index) + 1).padStart(2, "0") }}</b><p>{{ item }}</p><span :class="dynamicRisk?.risk_level || 'green'">{{ levelName(dynamicRisk?.risk_level || "green") }}</span>
          </div>
        </section>

        <section class="card-section control-list">
          <div class="mini-head"><h3>关键控制措施</h3><small>用于班前提示与现场巡检核对</small></div>
          <ul><li v-for="item in riskCard.pre_job_checks?.slice(0, 5)" :key="item">{{ item }}</li></ul>
        </section>

        <section class="action-boundary">
          <div v-if="riskCard.human_confirmations?.length">
            <h3>作业前人工核查</h3><ul><li v-for="item in riskCard.human_confirmations.slice(0, 4)" :key="item">{{ item }}</li></ul>
          </div>
          <div v-if="riskCard.prohibited_behaviors?.length" class="prohibited-list">
            <h3>禁止行为</h3><ul><li v-for="item in riskCard.prohibited_behaviors.slice(0, 3)" :key="item">{{ item }}</li></ul>
          </div>
        </section>

        <section class="card-section source-list">
          <div class="mini-head"><h3>识别依据</h3><small>每项结论可追溯到项目资料</small></div>
          <div class="source-grid">
            <article v-for="item in evidenceSources" :key="`${item.evidence_type}:${item.title}`">
              <span>{{ evidenceType(item.evidence_type) }}</span><b>{{ item.title }}</b><small>{{ item.location || item.quote }}</small>
            </article>
          </div>
        </section>

        <div class="card-actions">
          <button class="btn btn-ghost" @click="returnToEdit">返回调整</button>
          <button class="btn btn-primary" @click="enterInspection">确认并进入现场隐患巡检 →</button>
        </div>
      </article>

      <article v-else class="card risk-placeholder">
        <div class="placeholder-icon">析</div><h2>等待生成班前风险提示卡</h2>
        <p>任务信息完整后，将自动关联施工方案、规范制度、事故案例与环境信息。</p>
        <div><span>施工方案</span><span>规范制度</span><span>案例库</span><span>环境信息</span></div>
      </article>
    </div>

    <article class="card panel recent-tasks">
      <div class="section-head"><div><p class="section-kicker">历史记录</p><h2>最近班前风险分析</h2></div><button class="btn btn-ghost" @click="load">同步</button></div>
      <div class="task-list">
        <div v-for="task in tasks" :key="task.id" class="task-row">
          <button class="task-open" @click="showRisk(task)"><span class="task-date">{{ task.scheduled_date || "未排期" }}</span><div><b>{{ task.normalized_task || task.work_content }}</b><small>{{ [task.work_location, task.work_floor, task.work_time].filter(Boolean).join(" · ") }}</small></div><em>查看提示卡 →</em></button>
          <button class="task-chain" @click="chainTaskId = task.id">全链路</button>
          <span v-if="task.duplicate_count > 1" class="duplicate-mark">已合并 {{ task.duplicate_count }} 条</span>
        </div>
        <p v-if="!tasks.length" class="empty">还没有班前风险分析记录</p>
      </div>
    </article>

    <TaskChainDrawer :open="!!chainTaskId" :task-id="chainTaskId" @close="chainTaskId = null" />
  </section>
</template>

<style scoped>
.flow-steps{display:grid;grid-template-columns:auto 1fr auto 1fr auto 1fr auto;align-items:center;margin:-4px 0 22px;padding:15px 20px;border:1px solid var(--line);border-radius:16px;background:rgba(255,255,255,.72)}
.flow-steps div{display:flex;align-items:center;gap:8px;color:#8a9aad;font-size:12px;font-weight:750}.flow-steps b{width:27px;height:27px;display:grid;place-items:center;border-radius:50%;background:#e7edf5;color:#6b7f96}.flow-steps i{height:1px;margin:0 14px;background:#d8e2ed}.flow-steps div.active{color:#174c84}.flow-steps div.active b{background:#2563eb;color:#fff;box-shadow:0 5px 14px rgba(37,99,235,.25)}.flow-steps div.done{color:#17735f}.flow-steps div.done b{background:#dff5ee;color:#14705c}
.task-workspace{display:grid;grid-template-columns:minmax(340px,.82fr) minmax(520px,1.18fr);gap:18px;margin-bottom:18px;align-items:start}.chat-card{padding:22px;display:flex;flex-direction:column;min-height:610px}.section-kicker{margin:0 0 5px;color:#3474b5;font-size:9px;font-weight:850;letter-spacing:.12em}.section-head h2{margin:0}.input-hint{margin:-2px 0 12px;color:var(--muted);font-size:12px;line-height:1.6}.task-chat{flex:1;max-height:265px;overflow:auto;padding:8px 1px}.task-welcome{padding:28px 10px;text-align:center}.task-welcome>span{display:block;color:var(--muted);font-size:11px}.task-welcome button{display:block;margin:12px auto;border:1px solid #cfe0ef;border-radius:12px;background:#f3f8fc;padding:12px 15px;color:#28567e;font-size:12px;font-weight:700;line-height:1.5}.chat-line{display:grid;grid-template-columns:31px 1fr;gap:9px;margin:11px 0}.chat-line>span{width:30px;height:30px;display:grid;place-items:center;border-radius:10px;background:#e8eef5;font-size:10px;font-weight:800}.chat-line.assistant>span{background:#163b5e;color:#9de2d2}.chat-line p{margin:0;padding:10px 12px;background:#f2f6f9;border-radius:4px 11px 11px;font-size:12px;line-height:1.65}
.task-draft{margin:10px 0 13px;padding:15px;border:1px solid #d6e3ed;border-radius:13px;background:#f8fbfd}.draft-title{display:flex;justify-content:space-between;align-items:center;margin-bottom:11px}.draft-title b{font-size:13px}.draft-title span{padding:4px 7px;border-radius:999px;background:#e4f5ef;color:#17725f;font-size:9px;font-weight:800}.task-draft dl{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:0}.task-draft dl div{min-width:0}.task-draft dt{color:var(--muted);font-size:9px}.task-draft dd{margin:3px 0 0;font-size:11px;font-weight:700;line-height:1.45}.tag-line,.risk-tags{display:flex;gap:6px;flex-wrap:wrap}.tag-line{margin-top:12px}.tag-line span,.risk-tags span{padding:5px 8px;border-radius:7px;background:#eaf2fb;color:#245d91;font-size:9px;font-weight:750}
.task-composer{display:grid;grid-template-columns:1fr auto auto;gap:8px;border:1px solid #bfcfdd;border-radius:13px;padding:8px;background:#fff}.task-composer:focus-within{border-color:#4e92d3;box-shadow:0 0 0 3px rgba(78,146,211,.1)}.task-composer textarea{border:0;resize:none;outline:0;font-size:12px;line-height:1.55}.task-composer button{border:0;border-radius:10px;background:#163b5e;color:#fff;padding:0 15px;font-size:11px;font-weight:750}.task-composer button.voice-input{background:#e8f1fa;color:#245d91}.task-composer button.voice-input.listening{background:#fee9e7;color:#b4332d}.task-composer button:disabled{opacity:.5}
.risk-card{padding:0;overflow:hidden;border-top:4px solid #e5644f}.risk-card-head{display:flex;justify-content:space-between;gap:15px;align-items:flex-start;padding:21px 23px;background:linear-gradient(110deg,#fff5f1,#fff)}.risk-card-head span{font:750 9px monospace;color:#b24a3d;letter-spacing:.12em}.risk-card-head h2{margin:5px 0 4px;font-size:22px}.risk-card-head p{margin:0;color:#8a6b64;font-size:10px}.risk-card-head button{border:0;border-radius:8px;padding:8px 10px;background:#163b5e;color:#fff;font-size:10px;font-weight:750}.risk-summary{display:grid;grid-template-columns:1.5fr 1fr 1fr;border-bottom:1px solid #e4eaf0;background:#fff}.risk-summary>div{padding:14px 18px;border-right:1px solid #e4eaf0}.risk-summary>div:last-child{border:0}.risk-summary small,.risk-summary strong{display:block}.risk-summary small{margin-bottom:5px;color:var(--muted);font-size:9px}.risk-summary strong{font-size:11px;line-height:1.45}.risk-tags{padding:12px 18px 2px;background:#fff}.card-section{padding:16px 18px;border-bottom:1px solid #e6ebf1}.mini-head{display:flex;justify-content:space-between;gap:15px;align-items:baseline;margin-bottom:10px}.mini-head h3{margin:0;font-size:14px}.mini-head small{color:var(--muted);font-size:9px}.risk-row{display:grid;grid-template-columns:28px 1fr auto;gap:9px;align-items:center;padding:9px 10px;margin:6px 0;border-radius:10px;background:#f6f8fa}.risk-row>b{font:800 9px monospace;color:#7490aa}.risk-row p{margin:0;font-size:11px;line-height:1.55}.risk-row span{padding:4px 7px;border-radius:999px;background:#fff0df;color:#a65e08;font-size:9px;font-weight:800}.control-list ul{display:grid;grid-template-columns:1fr 1fr;gap:7px 20px;margin:0;padding-left:19px}.control-list li{font-size:11px;line-height:1.55}.source-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}.source-grid article{min-width:0;padding:10px 11px;border:1px solid #e0e8f0;border-radius:10px;background:#fbfcfd}.source-grid span,.source-grid b,.source-grid small{display:block}.source-grid span{color:#3176b6;font-size:8px;font-weight:850}.source-grid b{margin:4px 0;font-size:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.source-grid small{color:var(--muted);font-size:9px;line-height:1.45;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}.card-actions{display:flex;justify-content:flex-end;gap:9px;padding:16px 18px;background:#f8fafc}
.environment-strip{display:grid;grid-template-columns:.8fr 1.2fr;gap:9px;padding:13px 18px;background:#fff}.dynamic-level,.weather-card{padding:13px;border-radius:12px}.dynamic-level{border-left:4px solid var(--green);background:#edf8f4}.dynamic-level.yellow{border-left-color:var(--yellow);background:#fff8e8}.dynamic-level.red{border-left-color:var(--red);background:#fff0ee}.dynamic-level small,.dynamic-level b{display:block}.dynamic-level small{color:var(--muted);font-size:9px}.dynamic-level b{margin:4px 0;font-size:16px}.dynamic-level p,.weather-card p{margin:0;font-size:10px;line-height:1.55;color:#546779}.weather-card{background:#eef6fd}.weather-title{display:flex;justify-content:space-between;align-items:center}.weather-title span{font-size:10px;font-weight:800;color:#285e91}.weather-title b{font-size:8px;color:#27705c}.weather-card p{margin:6px 0 9px}.weather-metrics{display:flex;gap:18px}.weather-metrics span,.weather-metrics small,.weather-metrics b{display:block}.weather-metrics small{color:#71869b;font-size:8px}.weather-metrics b{margin-top:2px;font-size:10px}.weather-decisions{display:grid;gap:6px;padding:0 18px 13px;background:#fff}.weather-decisions>div{display:grid;grid-template-columns:auto 1fr auto;gap:9px;align-items:center;padding:9px 11px;border-radius:9px;background:#f1f7fb}.weather-decisions>div.warning,.weather-decisions>div.confirm{background:#fff7e4}.weather-decisions>div.stop{background:#fff0ee}.weather-decisions span{padding:4px 6px;border-radius:6px;background:#fff;color:#37627f;font-size:8px;font-weight:850}.weather-decisions .stop span{color:#ae332d}.weather-decisions .warning span,.weather-decisions .confirm span{color:#97650b}.weather-decisions p{margin:0;font-size:10px;line-height:1.5}.weather-decisions small{font-size:8px;color:var(--muted);white-space:nowrap}.trigger-list>div:not(.mini-head){display:grid;grid-template-columns:9px 1fr;gap:9px;align-items:start;padding:8px 0;border-top:1px solid #eef2f6}.trigger-list>div>span{width:8px;height:8px;margin-top:5px;border-radius:50%;background:var(--green)}.trigger-list>div>span.yellow{background:var(--yellow)}.trigger-list>div>span.red{background:var(--red)}.trigger-list p,.trigger-list small{margin:0}.trigger-list p>b,.trigger-list p>small{display:block}.trigger-list p>b{font-size:10px}.trigger-list p>small{margin-top:3px;color:var(--muted);font-size:9px;line-height:1.5}.risk-row span.green{background:#e7f7ef;color:#207254}.risk-row span.red{background:#fde9e7;color:#ab322d}.action-boundary{display:grid;grid-template-columns:1fr 1fr;gap:9px;padding:16px 18px;border-bottom:1px solid #e6ebf1}.action-boundary>div{padding:12px;border-radius:11px;background:#f4f8fb}.action-boundary .prohibited-list{background:#fff1ef}.action-boundary h3{margin:0 0 8px;font-size:12px}.action-boundary ul{margin:0;padding-left:17px}.action-boundary li{margin:5px 0;font-size:10px;line-height:1.5}
.risk-placeholder{min-height:610px;display:grid;place-content:center;text-align:center;padding:38px}.placeholder-icon{width:64px;height:64px;margin:auto;display:grid;place-items:center;border-radius:20px;background:#e8f1fa;color:#245d91;font-size:22px;font-weight:850}.risk-placeholder h2{margin:16px 0 7px;font-size:17px}.risk-placeholder p{max-width:420px;margin:0 auto 18px;color:var(--muted);font-size:11px;line-height:1.6}.risk-placeholder>div:last-child{display:flex;justify-content:center;gap:7px;flex-wrap:wrap}.risk-placeholder>div:last-child span{padding:6px 9px;border:1px solid #dce6ef;border-radius:8px;background:#fff;color:#58718a;font-size:9px}
.task-row{position:relative;display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;border-bottom:1px solid #e5ebf3}.task-list .task-open{width:100%;display:grid;grid-template-columns:85px 1fr auto;gap:14px;align-items:center;padding:14px 4px;border:0;background:transparent;text-align:left}.task-list .task-open:hover{background:#f5f8fc}.task-chain{border:0;border-radius:8px;background:#eaf3fc;color:#245d91;padding:8px 10px;font-size:9px;font-weight:700}.duplicate-mark{position:absolute;right:0;bottom:3px;color:#94650b;font-size:8px}.task-date{font:700 9px monospace;color:#6f887f}.task-list b,.task-list small{display:block}.task-list b{font-size:11px}.task-list small{margin-top:4px;color:var(--muted);font-size:9px}.task-list em{color:#34725e;font-size:9px;font-style:normal}
@media(max-width:1100px){.task-workspace{grid-template-columns:1fr}.chat-card,.risk-placeholder{min-height:auto}.task-chat{min-height:220px}.flow-steps{grid-template-columns:repeat(4,1fr)}.flow-steps i{display:none}.flow-steps div{justify-content:center}}
@media(max-width:700px){.flow-steps{grid-template-columns:1fr 1fr;gap:10px}.flow-steps div{justify-content:flex-start}.risk-summary,.task-draft dl,.control-list ul,.source-grid,.environment-strip,.action-boundary{grid-template-columns:1fr}.risk-summary>div{border-right:0;border-bottom:1px solid #e4eaf0}.mini-head{display:block}.mini-head small{display:block;margin-top:4px}.weather-decisions>div{grid-template-columns:1fr}.card-actions{flex-direction:column-reverse}.card-actions .btn{width:100%}.task-list .task-open{grid-template-columns:70px 1fr}.task-list em{display:none}}

/* 保留信息层级，但避免业务卡片出现难以阅读的微小文字。 */
.section-kicker,.draft-title span,.task-draft dt,.tag-line span,.risk-tags span,
.risk-card-head span,.risk-summary small,.mini-head small,.risk-row>b,.risk-row span,
.source-grid span,.source-grid small,.dynamic-level small,.weather-metrics small,
.weather-decisions span,.weather-decisions small,.trigger-list p>small,
.risk-placeholder>div:last-child span,.task-chain,.duplicate-mark,.task-date,
.task-list small,.task-list em{font-size:11px}
.task-draft dd,.task-composer button,.risk-summary strong,.risk-row p,.control-list li,
.source-grid b,.dynamic-level p,.weather-card p,.weather-title span,.weather-title b,
.weather-metrics b,.weather-decisions p,.trigger-list p>b,.action-boundary li,
.risk-placeholder p,.task-list b{font-size:12px}
.task-welcome button,.task-composer textarea,.chat-line p{font-size:13px}
</style>
