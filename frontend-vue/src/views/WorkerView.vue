<script setup lang="ts">
import { onMounted, ref } from "vue";
import { api } from "../api";
import TaskChainDrawer from "../components/TaskChainDrawer.vue";

const tab = ref<"task" | "quiz" | "learning">("task");
const tasks = ref<any[]>([]),
  profile = ref<any>(null),
  bank = ref<any>(null);
const error = ref(""),
  busy = ref(false);
const taskInput = ref(""),
  sessionId = ref<string | null>(null),
  taskMessages = ref<any[]>([]),
  draft = ref<any>(null),
  riskCard = ref<any>(null),
  chainTaskId = ref<string | null>(null);
const scene = ref(""),
  quiz = ref<any>(null),
  quizResult = ref<any>(null),
  answers = ref<Record<string, string>>({});

function workerRef() {
  return "项目作业人员";
}
async function load() {
  try {
    tasks.value = await api("/api/v1/tasks/recent");
    const worker = encodeURIComponent(workerRef());
    profile.value = await api(
      `/worker-assistant/workers/${worker}/learning-records`,
    );
    bank.value = await api("/worker-assistant/question-bank/status");
    if (!scene.value && bank.value?.scenes?.[0])
      scene.value = bank.value.scenes[0].key;
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
        worker_ref: workerRef(),
        team_ref: "",
        use_llm: null,
      }),
    });
    sessionId.value = result.session_id;
    draft.value = result.draft;
    riskCard.value = result.risk_card || riskCard.value;
    taskMessages.value.push({
      role: "assistant",
      text: result.assistant_message,
    });
    if (result.status === "completed") await load();
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "任务对话失败";
  } finally {
    busy.value = false;
  }
}
function newTask() {
  sessionId.value = null;
  taskMessages.value = [];
  draft.value = null;
  riskCard.value = null;
  taskInput.value = "";
}
async function showRisk(task: any) {
  tab.value = "task";
  error.value = "";
  try {
    riskCard.value = await api(`/worker-assistant/risk-cards/${task.id}`);
    window.scrollTo({ top: 0, behavior: "smooth" });
  } catch (cause) {
    error.value =
      cause instanceof Error ? cause.message : "该任务暂未生成风险卡";
  }
}
async function createQuiz() {
  if (!scene.value || busy.value) return;
  busy.value = true;
  error.value = "";
  quizResult.value = null;
  answers.value = {};
  try {
    quiz.value = await api("/worker-assistant/quizzes", {
      method: "POST",
      body: JSON.stringify({ worker_ref: workerRef(), scene: scene.value }),
    });
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "生成测验失败";
  } finally {
    busy.value = false;
  }
}
async function submitQuiz() {
  if (!quiz.value) return;
  const payload = quiz.value.questions.map((item: any) => ({
    question_id: item.id,
    answer: answers.value[item.id],
  }));
  if (payload.some((item: any) => !item.answer)) {
    error.value = "请完成全部5道题后再提交";
    return;
  }
  busy.value = true;
  error.value = "";
  try {
    quizResult.value = await api(
      `/worker-assistant/quizzes/${quiz.value.id}/submit`,
      { method: "POST", body: JSON.stringify({ answers: payload }) },
    );
    await load();
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "提交失败";
  } finally {
    busy.value = false;
  }
}
function correct(questionId: string) {
  return quizResult.value?.answers?.find(
    (item: any) => item.question_id === questionId,
  );
}
onMounted(load);
</script>

<template>
  <section class="page worker-page">
    <header class="page-heading">
      <div>
        <p class="eyebrow">WORKER SAFETY COMPANION</p>
        <h1>安全培训</h1>
      </div>
    </header>
    <nav class="worker-tabs">
      <button :class="{ active: tab === 'task' }" @click="tab = 'task'">
        每日任务与风险卡</button
      ><button :class="{ active: tab === 'quiz' }" @click="tab = 'quiz'">
        场景测验</button
      ><button
        :class="{ active: tab === 'learning' }"
        @click="tab = 'learning'"
      >
        学习与错题
      </button>
    </nav>
    <div v-if="error" class="error-banner">{{ error }}</div>

    <template v-if="tab === 'task'">
      <div class="task-workspace">
        <article class="card chat-card">
          <div class="section-head">
            <div>
              <h2>描述今天的作业</h2>
            </div>
            <button class="btn btn-ghost" @click="newTask">新任务</button>
          </div>
          <div class="task-chat">
            <div v-if="!taskMessages.length" class="task-welcome">
              <b>例如：</b
              ><button @click="taskInput = '今天下午在12层拆除外墙模板'">
                今天下午在12层拆除外墙模板
              </button>
            </div>
            <div
              v-for="(message, index) in taskMessages"
              :key="index"
              class="chat-line"
              :class="message.role"
            >
              <span>{{ message.role === "user" ? "我" : "安" }}</span>
              <p>{{ message.text }}</p>
            </div>
          </div>
          <div v-if="draft" class="draft-line">
            <span
              >作业：{{
                draft.normalized_task || draft.work_content || "待补充"
              }}</span
            ><span>位置：{{ draft.location || "待补充" }}</span
            ><span>楼层/高度：{{ draft.floor || "待补充" }}</span
            ><span>时间：{{ draft.work_time || "待补充" }}</span>
          </div>
          <div class="task-composer">
            <textarea
              v-model="taskInput"
              rows="2"
              placeholder="直接说出作业内容、位置、楼层/高度和时间…"
              @keydown.ctrl.enter="sendTask"
            ></textarea
            ><button :disabled="busy || !taskInput.trim()" @click="sendTask">
              {{ busy ? "处理中…" : "发送" }}
            </button>
          </div>
        </article>
        <article v-if="riskCard" class="card risk-card">
          <div class="risk-card-head">
            <div>
              <span>WORK RISK CARD</span>
              <h2>作业风险提示卡</h2>
            </div>
            <div><b>{{ riskCard.scenes?.[0] || "高处作业" }}</b><button @click="chainTaskId = riskCard.task_id">查看全链路</button></div>
          </div>
          <div class="task-facts">
            <div>
              <small>作业内容</small
              ><strong>{{
                riskCard.normalized_task || riskCard.work_content
              }}</strong>
            </div>
            <div>
              <small>作业位置</small><strong>{{ riskCard.location }}</strong>
            </div>
            <div>
              <small>楼层/高度</small><strong>{{ riskCard.floor }}</strong>
            </div>
            <div>
              <small>作业时间</small><strong>{{ riskCard.work_time }}</strong>
            </div>
          </div>
          <div class="weather-line">
            <span>天气</span><b>{{ riskCard.weather?.summary }}</b
            ><small>{{
              riskCard.weather?.max_wind_speed_kmh != null
                ? `最大风速 ${riskCard.weather.max_wind_speed_kmh} km/h`
                : ""
            }}</small>
          </div>
          <div class="risk-columns">
            <section>
              <h3>主要风险</h3>
              <ul>
                <li v-for="item in riskCard.main_risks" :key="item">
                  {{ item }}
                </li>
              </ul>
            </section>
            <section>
              <h3>作业前检查</h3>
              <ul>
                <li v-for="item in riskCard.pre_job_checks" :key="item">
                  {{ item }}
                </li>
              </ul>
            </section>
            <section class="prohibited">
              <h3>禁止行为</h3>
              <ul>
                <li v-for="item in riskCard.prohibited_behaviors" :key="item">
                  {{ item }}
                </li>
              </ul>
            </section>
          </div>
          <details v-if="riskCard.similar_accidents?.length" class="accidents">
            <summary>
              查看 {{ riskCard.similar_accidents.length }} 个相似事故案例
            </summary>
            <div v-for="item in riskCard.similar_accidents" :key="item.case_id">
              <b>{{ item.title }}</b>
              <p>{{ item.consequence || item.evidence }}</p>
            </div>
          </details>
          <p class="notice">{{ riskCard.safety_notice }}</p>
        </article>
        <article v-else class="card risk-placeholder">
          <span>卡</span>
          <h2>风险卡将在任务信息完整后生成</h2>
        </article>
      </div>
      <article class="card panel recent-tasks">
        <div class="section-head">
          <h2>最近作业任务</h2>
          <button class="btn btn-ghost" @click="load">同步</button>
        </div>
        <div class="task-list">
          <div v-for="task in tasks" :key="task.id" class="task-row">
            <button class="task-open" @click="showRisk(task)"><span class="task-date">{{ task.scheduled_date || "未排期" }}</span><div><b>{{ task.normalized_task || task.work_content }}</b><small>{{ [task.work_location, task.work_floor, task.work_time].filter(Boolean).join(" · ") }}</small></div><em>查看风险卡 →</em></button>
            <button class="task-chain" @click="chainTaskId = task.id">全链路</button>
            <span v-if="task.duplicate_count > 1" class="duplicate-mark">已合并 {{ task.duplicate_count }} 条</span>
          </div>
          <p v-if="!tasks.length" class="empty">还没有每日作业任务</p>
        </div>
      </article>
    </template>

    <template v-else-if="tab === 'quiz'">
      <article class="card panel quiz-toolbar">
        <div>
          <h2>5题场景测验</h2>
        </div>
        <select v-model="scene" class="input">
          <option
            v-for="item in bank?.scenes || []"
            :key="item.key"
            :value="item.key"
          >
            {{ item.name || item.key }}
          </option></select
        ><button
          class="btn btn-primary"
          :disabled="busy || !scene"
          @click="createQuiz"
        >
          生成测验
        </button>
      </article>
      <div v-if="quiz" class="question-list">
        <article
          v-for="(item, index) in quiz.questions"
          :key="item.id"
          class="card panel question"
        >
          <span
            >{{ String(Number(index) + 1).padStart(2, "0") }} ·
            {{ item.type === "single_choice" ? "单选题" : "判断题" }}</span
          >
          <h3>{{ item.stem }}</h3>
          <label
            v-for="option in item.options"
            :key="option.key"
            :class="{ chosen: answers[item.id] === option.key }"
            ><input
              v-model="answers[item.id]"
              type="radio"
              :name="item.id"
              :value="option.key"
              :disabled="!!quizResult"
            /><b>{{ option.key }}</b
            >{{ option.text }}</label
          >
          <p
            v-if="correct(item.id)"
            :class="correct(item.id).is_correct ? 'right' : 'wrong'"
          >
            {{
              correct(item.id).is_correct
                ? "回答正确"
                : `正确答案：${correct(item.id).correct_answer}`
            }}。{{ correct(item.id).explanation }}
          </p>
        </article>
        <button
          v-if="!quizResult"
          class="btn btn-accent submit-quiz"
          :disabled="busy"
          @click="submitQuiz"
        >
          提交全部答案
        </button>
        <div v-else class="quiz-score">
          本次答对 <b>{{ quizResult.correct_count }}</b> /
          {{ quizResult.total }} 题，结果已进入个人学习记录。
        </div>
      </div>
      <div v-else class="card panel empty">选择作业场景后生成一组测验。</div>
    </template>

    <template v-else>
      <div class="learning-stats">
        <article class="card">
          <strong>{{ profile?.quiz_attempts?.length || 0 }}</strong
          ><small>测验记录</small>
        </article>
        <article class="card">
          <strong>{{ profile?.active_wrong_count || 0 }}</strong
          ><small>待复习错题</small>
        </article>
      </div>
      <div class="learning-grid">
        <article class="card panel">
          <div class="section-head">
            <h2>错题复习</h2>
            <span class="status-pill">连续答对后移出</span>
          </div>
          <div
            v-for="item in profile?.wrong_questions || []"
            :key="item.id"
            class="learning-item"
          >
            <b>{{ item.stem }}</b
            ><small>{{ item.scene_name }} · {{ item.category }}</small>
          </div>
          <p v-if="!profile?.wrong_questions?.length" class="empty">
            当前没有待复习错题
          </p>
        </article>
        <article class="card panel">
          <div class="section-head">
            <h2>推荐学习</h2>
            <span class="status-pill">按个人记录</span>
          </div>
          <div
            v-for="(item, index) in profile?.recommendations || []"
            :key="index"
            class="learning-item"
          >
            <b>{{
              item.title || item.scene_name || item.scene || "巩固练习"
            }}</b
            ><small>{{
              item.reason || item.message || "建议完成对应场景测验"
            }}</small>
          </div>
          <p v-if="!profile?.recommendations?.length" class="empty">
            完成测验后生成推荐
          </p>
        </article>
      </div>
    </template>
    <TaskChainDrawer :open="!!chainTaskId" :task-id="chainTaskId" @close="chainTaskId = null" />
  </section>
</template>

<style scoped>
.worker-tabs {
  display: flex;
  gap: 6px;
  padding: 5px;
  background: #eaf0ec;
  border-radius: 13px;
  margin-bottom: 14px;
  overflow: auto;
}
.worker-tabs button {
  flex: 1;
  min-width: 130px;
  border: 0;
  background: transparent;
  border-radius: 9px;
  padding: 10px;
  font-size: 10px;
  font-weight: 700;
  color: #71857e;
}
.worker-tabs button.active {
  background: white;
  color: var(--forest);
  box-shadow: 0 3px 12px rgba(18, 61, 47, 0.08);
}
.task-workspace {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
  margin-bottom: 14px;
}
.chat-card {
  padding: 20px;
  display: flex;
  flex-direction: column;
  min-height: 540px;
}
.task-chat {
  flex: 1;
  max-height: 330px;
  overflow: auto;
  padding: 8px 1px;
}
.task-welcome {
  padding: 25px 15px;
  text-align: center;
}
.task-welcome button {
  display: block;
  margin: 10px auto;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: #f5f8f5;
  padding: 10px;
  font-weight: 700;
}
.task-welcome p {
  font-size: 9px;
  color: var(--muted);
}
.chat-line {
  display: grid;
  grid-template-columns: 28px 1fr;
  gap: 8px;
  margin: 10px 0;
}
.chat-line > span {
  width: 27px;
  height: 27px;
  display: grid;
  place-items: center;
  border-radius: 9px;
  background: #e8efea;
  font-size: 9px;
  font-weight: 800;
}
.chat-line.assistant > span {
  background: var(--forest);
  color: var(--mint);
}
.chat-line p {
  margin: 0;
  padding: 9px 11px;
  background: #f1f5f2;
  border-radius: 3px 10px 10px;
  font-size: 10px;
  line-height: 1.6;
}
.draft-line {
  display: flex;
  gap: 5px;
  flex-wrap: wrap;
  margin: 6px 0;
}
.draft-line span {
  font-size: 8px;
  background: #eef3ef;
  border-radius: 6px;
  padding: 5px 7px;
}
.task-composer {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 8px;
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 7px;
}
.task-composer textarea {
  border: 0;
  resize: none;
  outline: 0;
  font-size: 11px;
}
.task-composer button {
  border: 0;
  border-radius: 9px;
  background: var(--forest);
  color: #fff;
  padding: 0 14px;
  font-weight: 700;
}
.risk-card {
  padding: 0;
  overflow: hidden;
  border-top: 4px solid #df604f;
}
.risk-card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 20px 22px;
  background: #fff7f4;
}
.risk-card-head span {
  font: 700 10px monospace;
  color: #a95a4e;
}
.risk-card-head h2 {
  margin: 5px 0 0;
  font-size: 20px;
}
.risk-card-head > div:last-child {
  display: flex;
  align-items: center;
  gap: 7px;
}
.risk-card-head > div:last-child > b {
  font-size: 11px;
  background: #ffe5df;
  color: #a43f35;
  padding: 6px 8px;
  border-radius: 7px;
}
.risk-card-head > div:last-child > button {
  border: 0;
  border-radius: 7px;
  padding: 6px 8px;
  background: #102a43;
  color: white;
  font-size: 11px;
  font-weight: 700;
}
.task-facts {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1px;
  background: #e9eeeb;
}
.task-facts div {
  background: white;
  padding: 12px 18px;
}
.task-facts small,
.task-facts strong {
  display: block;
}
.task-facts small {
  font-size: 10px;
  color: var(--muted);
  margin-bottom: 4px;
}
.task-facts strong {
  font-size: 12px;
}
.weather-line {
  display: grid;
  grid-template-columns: 40px 1fr auto;
  gap: 8px;
  padding: 12px 18px;
  background: #edf5e8;
  font-size: 11px;
}
.weather-line small {
  color: var(--muted);
}
.risk-columns {
  display: grid;
  grid-template-columns: 1fr 1fr;
  padding: 8px 18px;
  gap: 8px;
}
.risk-columns section {
  background: #f5f7f5;
  border-radius: 9px;
  padding: 11px;
}
.risk-columns section.prohibited {
  grid-column: 1/-1;
  background: #fff0ed;
}
.risk-columns h3 {
  font-size: 12px;
  margin: 0 0 7px;
}
.risk-columns ul {
  padding-left: 15px;
  margin: 0;
}
.risk-columns li {
  font-size: 11px;
  line-height: 1.6;
  margin: 3px 0;
}
.accidents {
  margin: 4px 18px 12px;
  border-top: 1px solid var(--line);
  padding-top: 10px;
}
.accidents summary {
  font-size: 11px;
  font-weight: 700;
  cursor: pointer;
}
.accidents div {
  padding: 9px 0;
}
.accidents b {
  font-size: 11px;
}
.accidents p {
  font-size: 10px;
  color: var(--muted);
  margin: 3px 0;
}
.notice {
  margin: 0;
  padding: 10px 18px;
  background: #102a43;
  color: #d7e6e0;
  font-size: 10px;
  line-height: 1.5;
}
.risk-placeholder {
  min-height: 540px;
  display: grid;
  place-content: center;
  text-align: center;
  padding: 30px;
}
.risk-placeholder > span {
  margin: auto;
  width: 60px;
  height: 60px;
  border-radius: 20px;
  display: grid;
  place-items: center;
  background: #eaf3fc;
  color: var(--forest);
  font-size: 22px;
}
.risk-placeholder h2 {
  font-size: 16px;
}
.risk-placeholder p {
  font-size: 9px;
  color: var(--muted);
}
.task-row {
  position: relative;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  border-bottom: 1px solid #e5ebf3;
}
.task-list .task-open {
  width: 100%;
  display: grid;
  grid-template-columns: 85px 1fr auto;
  gap: 14px;
  align-items: center;
  padding: 14px 4px;
  border: 0;
  border-bottom: 0;
  background: transparent;
  text-align: left;
}
.task-list .task-open:hover {
  background: #f5f8fc;
}
.task-chain {
  border: 0;
  border-radius: 8px;
  background: #eaf3fc;
  color: #245d91;
  padding: 8px 10px;
  font-size: 9px;
  font-weight: 700;
}
.duplicate-mark {
  position: absolute;
  right: 0;
  bottom: 3px;
  color: #94650b;
  font-size: 8px;
}
.task-date {
  font: 700 9px monospace;
  color: #6f887f;
}
.task-list b,
.task-list small {
  display: block;
}
.task-list b {
  font-size: 11px;
}
.task-list small {
  font-size: 9px;
  color: var(--muted);
  margin-top: 4px;
}
.task-list em {
  font-style: normal;
  font-size: 9px;
  color: #34725e;
}
.quiz-toolbar {
  display: grid;
  grid-template-columns: 1fr 280px auto;
  align-items: end;
  gap: 12px;
  margin-bottom: 12px;
}
.quiz-toolbar h2 {
  margin: 0;
}
.quiz-toolbar p {
  font-size: 9px;
  color: var(--muted);
  margin-bottom: 0;
}
.question-list {
  display: grid;
  gap: 10px;
}
.question > span {
  font: 700 8px monospace;
  color: #668077;
}
.question h3 {
  font-size: 13px;
  line-height: 1.6;
}
.question label {
  display: flex;
  gap: 9px;
  align-items: flex-start;
  padding: 9px;
  border: 1px solid var(--line);
  border-radius: 9px;
  margin: 5px 0;
  font-size: 10px;
}
.question label.chosen {
  background: #edf6ff;
  border-color: #a9cc86;
}
.question label b {
  font: 700 10px monospace;
}
.question > p {
  font-size: 9px;
  padding: 9px;
  border-radius: 8px;
}
.question > p.right {
  background: #e4f5e9;
  color: #277853;
}
.question > p.wrong {
  background: #fdebe8;
  color: #a43f35;
}
.submit-quiz {
  justify-self: start;
  padding: 13px 24px;
}
.quiz-score {
  background: var(--forest);
  color: white;
  border-radius: 12px;
  padding: 16px;
  text-align: center;
  font-size: 11px;
}
.quiz-score b {
  font-size: 20px;
  color: var(--mint);
}
.learning-stats {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 10px;
  margin-bottom: 14px;
}
.learning-stats article {
  padding: 19px;
}
.learning-stats strong,
.learning-stats small {
  display: block;
}
.learning-stats strong {
  font-size: 28px;
}
.learning-stats small {
  font-size: 9px;
  color: var(--muted);
  margin-top: 4px;
}
.learning-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
}
.learning-item {
  padding: 12px 2px;
  border-bottom: 1px solid var(--line);
}
.learning-item b,
.learning-item small {
  display: block;
}
.learning-item b {
  font-size: 10px;
  line-height: 1.5;
}
.learning-item small {
  font-size: 8px;
  color: var(--muted);
  margin-top: 5px;
}
@media (max-width: 950px) {
  .task-workspace,
  .learning-grid {
    grid-template-columns: 1fr;
  }
  .quiz-toolbar {
    grid-template-columns: 1fr 1fr;
  }
  .quiz-toolbar > div {
    grid-column: 1/-1;
  }
  .risk-placeholder {
    min-height: 240px;
  }
}
@media (max-width: 600px) {
  .task-facts,
  .risk-columns,
  .learning-stats {
    grid-template-columns: 1fr;
  }
  .risk-columns section.prohibited {
    grid-column: auto;
  }
  .task-list button {
    grid-template-columns: 70px 1fr;
  }
  .task-list em {
    display: none;
  }
  .quiz-toolbar {
    grid-template-columns: 1fr;
  }
  .quiz-toolbar > div {
    grid-column: auto;
  }
}
</style>
