<script setup>
import { marked } from 'marked'
import DOMPurify from 'dompurify'

defineProps({
  messages: { type: Array, default: () => [] },
})
const emit = defineEmits(['retry'])

marked.setOptions({ breaks: true, gfm: true })

// AI 回答走 Markdown 渲染，但必须先净化：模型输出可能夹带 HTML/脚本
function renderMarkdown(text) {
  return DOMPurify.sanitize(marked.parse(text || ''))
}

function statusText(status) {
  if (status === 'searching') return '正在查询数据'
  if (status === 'thinking') return '正在思考'
  return ''
}
</script>

<template>
  <div class="chat-inner">
    <div
      v-for="(m, i) in messages"
      :key="i"
      class="msg"
      :class="m.role === 'user' ? 'msg--user' : 'msg--ai'"
    >
      <div class="bubble">
        <!-- 用户输入按纯文本展示（自动转义，绝不 v-html） -->
        <template v-if="m.role === 'user'">{{ m.content }}</template>

        <template v-else>
          <div v-if="m.status" class="status">
            <span class="dot-pulse"><i></i><i></i><i></i></span>
            {{ statusText(m.status) }}
          </div>

          <div v-if="m.content" class="markdown" v-html="renderMarkdown(m.content)"></div>
          <span v-if="m.streaming && m.content" class="cursor"></span>

          <div v-if="m.error" class="error-note">
            <span>{{ m.error }}</span>
            <button class="btn-retry" @click="emit('retry', i)">重试</button>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>
