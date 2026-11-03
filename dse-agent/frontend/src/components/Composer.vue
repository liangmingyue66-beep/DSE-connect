<script setup>
import { ref, nextTick, watch } from 'vue'

const props = defineProps({
  disabled: { type: Boolean, default: false },
  streaming: { type: Boolean, default: false },
  draft: { type: String, default: '' },
})
const emit = defineEmits(['send', 'stop'])

const text = ref(props.draft || '')
const el = ref(null)

watch(() => props.draft, (v) => {
  if (v !== undefined) text.value = v
})

// 输入框随内容自动长高（上限由 CSS max-height 控制）
function resize() {
  const node = el.value
  if (!node) return
  node.style.height = 'auto'
  node.style.height = Math.min(node.scrollHeight, 160) + 'px'
}

function submit() {
  const value = text.value.trim()
  if (!value || props.disabled) return
  emit('send', value)
  text.value = ''
  nextTick(resize)
}

function onKeydown(e) {
  // Enter 发送，Shift+Enter 换行
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault()
    submit()
  }
}

defineExpose({
  focus() {
    el.value?.focus()
  },
  setText(v) {
    text.value = v
    nextTick(resize)
  },
})
</script>

<template>
  <div class="composer">
    <div class="composer__inner">
      <textarea
        ref="el"
        v-model="text"
        rows="1"
        placeholder="问点什么，例如：我 4/3/4 想去广东读计算机"
        @input="resize"
        @keydown="onKeydown"
      ></textarea>
      <div class="composer__bar">
        <span class="composer__hint">Enter 发送 · Shift+Enter 换行</span>
        <button v-if="streaming" class="btn-stop" @click="emit('stop')">停止生成</button>
        <button v-else class="btn-send" :disabled="!text.trim() || disabled" @click="submit">
          发送
        </button>
      </div>
    </div>
  </div>
</template>
