<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { api } from "../api";
import { useProjectStore } from "../stores/project";

type AssistantMessage = {
  role: "user" | "assistant";
  text: string;
  agentLabel?: string;
  sources?: Array<{ title: string; url: string; siteName?: string }>;
  target?: { path: string; label: string } | null;
};

type AgentResponse = {
  conversation_id: string;
  answer: string;
  agent_name: string;
  agent_label: string;
  metadata?: {
    agents?: string[];
    tools?: string[];
    results?: Array<{ items?: Array<Record<string, unknown>> }>;
  };
};

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

const router = useRouter();
const projects = useProjectStore();
const open = ref(false);
const busy = ref(false);
const listening = ref(false);
const input = ref("");
const error = ref("");
const conversationId = ref<string | null>(null);
const messages = ref<AssistantMessage[]>([]);
const messageArea = ref<HTMLElement | null>(null);
const speakingIndex = ref<number | null>(null);
const speechPaused = ref(false);
let recognition: RecognitionLike | null = null;

const starters = [
  "脚手架拆除作业有哪些安全要求？",
  "查看今天的动态风险",
  "联网查询最新建筑施工安全政策",
];

const speechSupported = computed(() => {
  const target = window as typeof window & {
    SpeechRecognition?: new () => RecognitionLike;
    webkitSpeechRecognition?: new () => RecognitionLike;
  };
  return Boolean(target.SpeechRecognition || target.webkitSpeechRecognition);
});

watch(
  () => projects.active?.id,
  () => {
    conversationId.value = null;
    messages.value = [];
    error.value = "";
  },
);

onBeforeUnmount(() => {
  recognition?.stop();
  window.speechSynthesis?.cancel();
});

function routeFor(result: AgentResponse) {
  const agents = result.metadata?.agents || [result.agent_name];
  if (agents.some((item) => item === "worker_agent" || item === "risk_agent")) {
    return { path: "/worker", label: "查看班前风险分析" };
  }
  if (agents.includes("audit_agent")) return { path: "/audit", label: "查看施工方案审查" };
  if (agents.includes("safety_log_agent")) return { path: "/agent", label: "查看安全日志" };
  return null;
}

function sourcesFor(result: AgentResponse) {
  if (!(result.metadata?.tools || []).includes("web.search")) return [];
  const items = result.metadata?.results?.flatMap((item) => item.items || []) || [];
  return items
    .filter((item) => typeof item.url === "string" && typeof item.title === "string")
    .slice(0, 5)
    .map((item) => ({
      title: String(item.title),
      url: String(item.url),
      siteName: typeof item.site_name === "string" ? item.site_name : undefined,
    }));
}

function sourceHost(url: string) {
  try {
    return new URL(url).hostname;
  } catch {
    return "网页来源";
  }
}

async function scrollToLatest() {
  await nextTick();
  if (messageArea.value) messageArea.value.scrollTop = messageArea.value.scrollHeight;
}

async function send(message = input.value, fromVoice = false) {
  const text = message.trim();
  if (!text || busy.value) return;
  messages.value.push({ role: "user", text });
  input.value = "";
  busy.value = true;
  error.value = "";
  await scrollToLatest();
  try {
    const result = await api<AgentResponse>("/api/v1/agent/messages", {
      method: "POST",
      body: JSON.stringify({ conversation_id: conversationId.value, message: text }),
    });
    conversationId.value = result.conversation_id;
    const target = routeFor(result);
    messages.value.push({
      role: "assistant",
      text: result.answer,
      agentLabel: result.agent_label,
      sources: sourcesFor(result),
      target,
    });
    await scrollToLatest();
    if (fromVoice && target?.path === "/worker") {
      await router.push(target.path);
    }
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "智能助手暂时无法回答，请稍后重试。";
  } finally {
    busy.value = false;
  }
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
    if (transcript) void send(transcript, true);
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

function toggleSpeech(text: string, index: number) {
  if (!("speechSynthesis" in window)) return;
  if (speakingIndex.value === index && window.speechSynthesis.speaking) {
    if (window.speechSynthesis.paused) {
      window.speechSynthesis.resume();
      speechPaused.value = false;
    } else {
      window.speechSynthesis.pause();
      speechPaused.value = true;
    }
    return;
  }
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = "zh-CN";
  utterance.rate = 0.95;
  utterance.onend = () => {
    if (speakingIndex.value === index) speakingIndex.value = null;
    speechPaused.value = false;
  };
  utterance.onerror = () => {
    if (speakingIndex.value === index) speakingIndex.value = null;
    speechPaused.value = false;
  };
  speakingIndex.value = index;
  speechPaused.value = false;
  window.speechSynthesis.speak(utterance);
}

function speechLabel(index: number) {
  if (speakingIndex.value !== index) return "朗读回答";
  return speechPaused.value ? "继续朗读" : "暂停朗读";
}

function newConversation() {
  conversationId.value = null;
  messages.value = [];
  input.value = "";
  error.value = "";
  window.speechSynthesis?.cancel();
  speakingIndex.value = null;
  speechPaused.value = false;
}

async function go(target: { path: string }) {
  await router.push(target.path);
}
</script>

<template>
  <div class="smart-assistant">
    <button
      class="assistant-launcher"
      :class="{ active: open }"
      aria-label="打开智能助手"
      :aria-expanded="open"
      @click="open = !open"
    >
      <span class="assistant-launcher-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24"><path d="M12 2l1.55 5.2L19 9l-5.45 1.8L12 16l-1.55-5.2L5 9l5.45-1.8L12 2Zm7 12 .85 2.65L22.5 17.5l-2.65.85L19 21l-.85-2.65-2.65-.85 2.65-.85L19 14Z"/></svg>
      </span>
      <span><b>智能助手</b></span>
      <i></i>
    </button>

    <section v-if="open" class="assistant-panel" role="dialog" aria-label="智能助手">
      <header class="assistant-head">
        <h2>智能助手</h2>
        <button title="新对话" aria-label="新对话" @click="newConversation">↻</button>
        <button title="关闭" aria-label="关闭" @click="open = false">×</button>
      </header>

      <div ref="messageArea" class="assistant-messages">
        <div v-if="!messages.length" class="assistant-welcome">
          <span class="welcome-mark">AI</span>
          <h3>您好，需要我帮您查什么？</h3>
          <div class="starter-list">
            <button v-for="item in starters" :key="item" @click="send(item)">{{ item }}</button>
          </div>
        </div>

        <article v-for="(message, index) in messages" :key="index" class="assistant-message" :class="message.role">
          <span v-if="message.role === 'user'">我</span>
          <div>
            <small v-if="message.agentLabel">{{ message.agentLabel }}</small>
            <p>{{ message.text }}</p>
            <div v-if="message.sources?.length" class="assistant-sources">
              <b>联网来源</b>
              <a v-for="source in message.sources" :key="source.url" :href="source.url" target="_blank" rel="noreferrer">
                {{ source.title }}<small>{{ source.siteName || sourceHost(source.url) }}</small>
              </a>
            </div>
            <div v-if="message.role === 'assistant'" class="message-actions">
              <button :class="{ speaking: speakingIndex === index }" @click="toggleSpeech(message.text, index)">{{ speechLabel(index) }}</button>
              <button v-if="message.target" class="business-link" @click="go(message.target)">{{ message.target.label }} →</button>
            </div>
          </div>
        </article>
        <div v-if="busy" class="assistant-thinking"><span></span><span></span><span></span>正在查询与分析</div>
      </div>

      <p v-if="error" class="assistant-error">{{ error }}</p>
      <footer class="assistant-composer">
        <textarea v-model="input" rows="2" placeholder="输入问题，也可以按住下方麦克风说话" @keydown.ctrl.enter="send()"></textarea>
        <div>
          <button class="voice-button" :class="{ listening }" :disabled="!speechSupported && listening" @click="startVoice">
            <svg viewBox="0 0 24 24"><path d="M12 14a3 3 0 0 0 3-3V5a3 3 0 1 0-6 0v6a3 3 0 0 0 3 3Zm-7-3h2a5 5 0 0 0 10 0h2a7 7 0 0 1-6 6.92V21h-2v-3.08A7 7 0 0 1 5 11Z"/></svg>
            {{ listening ? "正在聆听…" : "语音提问" }}
          </button>
          <button class="send-button" :disabled="busy || !input.trim()" @click="send()">{{ busy ? "回答中" : "发送" }}</button>
        </div>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.smart-assistant { margin-top: 13px; }
.assistant-launcher { width: 100%; display: grid; grid-template-columns: 38px 1fr 7px; align-items: center; gap: 10px; padding: 10px 11px; border: 1px solid rgba(125,211,252,.2); border-radius: 14px; color: #eaf4ff; background: linear-gradient(115deg,rgba(37,99,235,.18),rgba(56,189,248,.07)); text-align: left; transition: .2s ease; }
.assistant-launcher:hover,.assistant-launcher.active { border-color: rgba(125,211,252,.45); background: linear-gradient(115deg,rgba(37,99,235,.3),rgba(56,189,248,.13)); transform: translateY(-1px); }
.assistant-launcher-icon { width: 38px; height: 38px; display: grid; place-items: center; border-radius: 12px; background: linear-gradient(145deg,#67e8f9,#60a5fa); box-shadow: 0 8px 20px rgba(37,99,235,.28); }
.assistant-launcher-icon svg { width: 21px; fill: #09203a; }
.assistant-launcher b { display: block; }
.assistant-launcher b { font-size: 13px; }
.assistant-launcher i { width: 7px; height: 7px; border-radius: 50%; background: #38d9c5; box-shadow: 0 0 0 4px rgba(56,217,197,.1); }
.assistant-panel { position: fixed; left: 266px; bottom: 22px; z-index: 80; width: min(430px,calc(100vw - 292px)); height: min(680px,calc(100vh - 44px)); display: grid; grid-template-rows: auto minmax(0,1fr) auto auto; overflow: hidden; border: 1px solid #cad9e9; border-radius: 22px; background: #f8fafc; color: #132238; box-shadow: 0 28px 80px rgba(5,25,52,.27); }
.assistant-head { display: grid; grid-template-columns: 1fr 34px 34px; gap: 9px; align-items: center; padding: 16px 17px; color: white; background: linear-gradient(120deg,#102a43,#1d4f91); }
.assistant-head h2 { margin: 0; font-size: 17px; }
.assistant-head > button { width: 34px; height: 34px; border: 1px solid rgba(255,255,255,.14); border-radius: 10px; color: #dcecff; background: rgba(255,255,255,.06); font-size: 20px; }
.assistant-messages { min-height: 0; overflow-y: auto; padding: 18px; background: radial-gradient(circle at 100% 0,rgba(96,165,250,.1),transparent 34%),#f3f7fb; }
.assistant-welcome { padding: 12px 4px; text-align: center; }
.welcome-mark { display: grid; place-items: center; width: 54px; height: 54px; margin: 0 auto 13px; border-radius: 18px; color: #17426c; background: #dff3ff; font: 850 14px monospace; }
.assistant-welcome h3 { margin: 0 0 7px; color: #173454; font-size: 18px; }
.starter-list { display: grid; gap: 8px; margin-top: 18px; }
.starter-list button { padding: 11px 13px; border: 1px solid #d6e3ef; border-radius: 11px; color: #315777; background: white; font-size: 13px; text-align: left; }
.starter-list button:hover { border-color: #82b6e2; background: #f3f9ff; }
.assistant-message { display: grid; grid-template-columns: minmax(0,1fr); gap: 8px; margin-bottom: 15px; }
.assistant-message.user { grid-template-columns: minmax(0,1fr) 31px; }
.assistant-message > span { width: 31px; height: 31px; display: grid; place-items: center; border-radius: 10px; background: #173f66; color: white; font-size: 11px; font-weight: 800; }
.assistant-message.user > span { grid-column: 2; grid-row: 1; background: #2563eb; }
.assistant-message > div { min-width: 0; }
.assistant-message.user > div { grid-column: 1; grid-row: 1; justify-self: end; }
.assistant-message > div > small { display: block; margin: 0 0 5px 2px; color: #56708a; font-size: 11px; font-weight: 750; }
.assistant-message p { margin: 0; padding: 11px 13px; border: 1px solid #dbe5ef; border-radius: 4px 14px 14px; background: white; color: #243c54; font-size: 13px; line-height: 1.72; white-space: pre-wrap; overflow-wrap: anywhere; box-shadow: 0 5px 16px rgba(31,65,105,.04); }
.assistant-message.user p { max-width: 320px; border-color: #2563eb; border-radius: 14px 4px 14px 14px; background: #2563eb; color: white; }
.assistant-sources { display: grid; gap: 6px; margin-top: 8px; padding: 10px; border: 1px solid #d9e6f1; border-radius: 11px; background: #f9fcff; }
.assistant-sources > b { color: #526d8b; font-size: 12px; }
.assistant-sources a { padding: 6px 7px; border-radius: 8px; color: #245d91; background: #eef7ff; font-size: 12px; line-height: 1.4; }
.assistant-sources a small { display: block; margin-top: 2px; color: #7890a5; font-size: 11px; }
.message-actions { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 7px; }
.message-actions button { padding: 7px 9px; border: 1px solid #d2dfeb; border-radius: 8px; color: #536b82; background: white; font-size: 12px; font-weight: 700; }
.message-actions button.speaking { border-color: #8db8dc; color: #14578c; background: #eaf6ff; }
.message-actions .business-link { border-color: #acd0ec; color: #14578c; background: #eaf6ff; }
.assistant-thinking { display: flex; align-items: center; gap: 5px; color: #71869a; font-size: 12px; }
.assistant-thinking span { width: 5px; height: 5px; border-radius: 50%; background: #60a5fa; animation: assistant-pulse 1s infinite alternate; }
.assistant-thinking span:nth-child(2) { animation-delay: .2s; }.assistant-thinking span:nth-child(3) { animation-delay: .4s; }
@keyframes assistant-pulse { to { opacity: .25; transform: translateY(-2px); } }
.assistant-error { margin: 0; padding: 8px 16px; color: #a13b35; background: #fff0ee; border-top: 1px solid #f0d0cd; font-size: 12px; }
.assistant-composer { padding: 12px 14px 13px; border-top: 1px solid #dbe5ef; background: white; }
.assistant-composer textarea { width: 100%; resize: none; padding: 9px 10px; border: 0; outline: none; color: #213a53; font-size: 13px; line-height: 1.5; }
.assistant-composer > div { display: flex; align-items: center; gap: 8px; padding-top: 8px; border-top: 1px solid #edf1f5; }
.assistant-composer > div { justify-content: space-between; }
.voice-button,.send-button { display: flex; align-items: center; gap: 5px; border: 0; border-radius: 9px; padding: 8px 10px; font-size: 12px; font-weight: 750; }
.voice-button { color: #245d91; background: #ebf5ff; }.voice-button.listening { color: #a33831; background: #fff0ee; }
.voice-button svg { width: 14px; fill: currentColor; }
.send-button { padding-inline: 14px; color: white; background: #1e64bb; }.send-button:disabled { cursor: not-allowed; opacity: .45; }
@media (max-width: 900px) {
  .assistant-panel { left: 12px; right: 12px; bottom: 12px; width: auto; height: min(680px,calc(100vh - 24px)); }
}
</style>
