<script setup>
import { ref, reactive, computed, onMounted, nextTick } from 'vue'
import SessionSidebar from './components/SessionSidebar.vue'
import MessageList from './components/MessageList.vue'
import Composer from './components/Composer.vue'
import {
  ensureToken, listSessions, createSession, deleteSession, fetchMessages, streamChat,
} from './api.js'

const DRAFT = '__draft__'   // 草稿态：界面已清空但还没建会话

const sessions = ref([])
const currentId = ref('')        // '' = 草稿态
const sidebarOpen = ref(false)
const fatalError = ref('')
const toast = ref('')
const scroller = ref(null)
const composer = ref(null)

/**
 * 每个会话独立保存消息与生成状态：
 * 切换会话时不打断正在进行的生成，切回来还能继续看到结果。
 * key: sessionId 或 DRAFT
 */
const stores = new Map()
function storeOf(key) {
  if (!stores.has(key)) {
    stores.set(key, { messages: reactive([]), streaming: false, controller: null })
  }
  return stores.get(key)
}

const activeKey = computed(() => currentId.value || DRAFT)
const messages = computed(() => storeOf(activeKey.value).messages)
const streaming = computed(() => storeOf(activeKey.value).streaming)

storeOf(DRAFT)   // 预建草稿态，避免在 computed 里首次创建

const EXAMPLES = [
  '我 4/3/4 想去广东读计算机',
  '校长推荐计划有什么要求',
  '广东省有哪些专业可以报',
  '中山大学计算机录取分数线',
]

/* ---------- 轻提示（替代原来的"静默失败"） ---------- */
let toastTimer = null
function notify(text) {
  toast.value = text
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => (toast.value = ''), 2800)
}

/* ---------- 滚动：只在接近底部时跟随 ---------- */
function scrollSoon(force = false) {
  nextTick(() => {
    const el = scroller.value
    if (!el) return
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120
    if (force || nearBottom) el.scrollTop = el.scrollHeight
  })
}

/* ---------- 会话列表 ---------- */
async function refreshSessions() {
  try {
    const data = await listSessions()
    sessions.value = data.sessions || []
  } catch (e) {
    fatalError.value = e.message
  }
}

function newSession() {
  // 草稿态：清空界面，不发请求；发第一条消息时才真正创建会话
  currentId.value = ''
  fatalError.value = ''
  sidebarOpen.value = false
  scrollSoon(true)
  nextTick(() => composer.value?.focus())
}

async function selectSession(id) {
  if (id === currentId.value) return
  const cached = stores.get(id)
  if (!cached || !cached.messages.length) {
    try {
      const data = await fetchMessages(id)
      const st = storeOf(id)
      st.messages.splice(0, st.messages.length,
        ...(data.messages || []).map((m) => ({ ...m, streaming: false, status: '', error: '' })))
    } catch (e) {
      fatalError.value = e.message
      return
    }
  }
  currentId.value = id          // 注意：不 abort 原会话，让它在后台继续生成
  fatalError.value = ''
  sidebarOpen.value = false
  scrollSoon(true)
}

async function removeSession(id) {
  if (!window.confirm('删除这个对话？聊天记录将不可恢复。')) return
  try {
    await deleteSession(id)
    stores.delete(id)
    if (id === currentId.value) currentId.value = ''
    await refreshSessions()
  } catch (e) {
    fatalError.value = e.message
  }
}

/* ---------- 发送 ---------- */
async function send(text) {
  const content = (text || '').trim()
  if (!content) return

  // 草稿态：先建会话，再往里写消息
  if (!currentId.value) {
    try {
      const s = await createSession()
      currentId.value = s.id
      await refreshSessions()
    } catch (e) {
      fatalError.value = e.message
      return
    }
  }

  const key = currentId.value
  const st = storeOf(key)
  if (st.streaming) {
    // 关键修复：不再静默 return，而是明确告诉用户
    notify('这个对话还在生成，请等它说完或点"停止生成"')
    return
  }

  st.messages.push({ role: 'user', content, streaming: false, status: '', error: '' })
  const reply = reactive({ role: 'assistant', content: '', status: 'thinking', streaming: true, error: '' })
  st.messages.push(reply)

  st.streaming = true
  st.controller = new AbortController()
  scrollSoon(true)

  try {
    await streamChat({
      sessionId: key,
      message: content,
      signal: st.controller.signal,
      onEvent(ev) {
        if (ev.type === 'stream') {
          reply.status = ''
          reply.content += ev.text
          if (currentId.value === key) scrollSoon()
        } else if (ev.type === 'node') {
          if (!reply.content) reply.status = 'searching'
        } else if (ev.type === 'error') {
          reply.error = ev.text
        } else if (ev.type === 'done') {
          reply.status = ''
        }
      },
    })
  } catch (e) {
    if (e.name === 'AbortError') {
      reply.content += '\n\n*（已停止生成）*'
    } else {
      reply.error = e.message || '请求失败，请稍后再试'
    }
  } finally {
    reply.streaming = false
    reply.status = ''
    st.streaming = false
    st.controller = null
    if (currentId.value === key) scrollSoon()
    refreshSessions()
  }
}

function stop() {
  const st = storeOf(activeKey.value)
  st.controller?.abort()
}

function retry(index) {
  const st = storeOf(activeKey.value)
  if (st.streaming) {
    notify('当前对话还在生成，请稍候')
    return
  }
  const target = st.messages[index]
  if (!target) return
  let userIdx = -1
  for (let i = index - 1; i >= 0; i--) {
    if (st.messages[i].role === 'user') {
      userIdx = i
      break
    }
  }
  if (userIdx < 0) return
  const userText = st.messages[userIdx].content
  st.messages.splice(userIdx)
  send(userText)
}

function useExample(text) {
  send(text)
}

onMounted(async () => {
  try {
    await ensureToken()
    await refreshSessions()
    if (sessions.value.length) await selectSession(sessions.value[0].id)
  } catch (e) {
    fatalError.value = e.message
  }
})
</script>

<template>
  <div class="layout">
    <SessionSidebar
      :sessions="sessions"
      :current-id="currentId"
      :open="sidebarOpen"
      @new="newSession"
      @select="selectSession"
      @delete="removeSession"
    />

    <div class="main">
      <button class="sidebar-toggle" @click="sidebarOpen = !sidebarOpen">☰ 对话</button>

      <div ref="scroller" class="chat-scroll">
        <div v-if="!messages.length" class="welcome">
          <h1>DSE 内地升学，从查清楚开始</h1>
          <p>查询 165 所内地高校 3794 条专业录取数据，以及招生政策原文。</p>

          <div v-if="fatalError" class="error-note">
            <span>{{ fatalError }}</span>
          </div>

          <div class="welcome__hints">
            <button v-for="q in EXAMPLES" :key="q" class="hint-card" @click="useExample(q)">
              {{ q }}
            </button>
          </div>
        </div>

        <MessageList v-else :messages="messages" @retry="retry" />
      </div>

      <Composer
        ref="composer"
        :streaming="streaming"
        :disabled="!!fatalError"
        @send="send"
        @stop="stop"
      />

      <transition name="fade">
        <div v-if="toast" class="toast">{{ toast }}</div>
      </transition>
    </div>
  </div>
</template>
