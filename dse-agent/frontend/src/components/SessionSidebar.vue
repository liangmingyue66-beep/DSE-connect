<script setup>
defineProps({
  sessions: { type: Array, default: () => [] },
  currentId: { type: String, default: '' },
  open: { type: Boolean, default: false },
})
const emit = defineEmits(['new', 'select', 'delete'])
</script>

<template>
  <aside class="sidebar" :class="{ 'is-open': open }">
    <div class="sidebar__head">
      <div class="sidebar__brand">DSE 志愿填报助手</div>
      <div class="sidebar__sub">内地高校升学 · 录取数据与政策</div>
      <button class="btn-new" @click="emit('new')">＋ 新建对话</button>
    </div>

    <div class="sidebar__list">
      <div v-if="!sessions.length" class="sidebar__empty">还没有对话</div>
      <div
        v-for="s in sessions"
        :key="s.id"
        class="session-item"
        :class="{ 'is-active': s.id === currentId }"
        @click="emit('select', s.id)"
      >
        <span class="session-item__title">{{ s.title || '新对话' }}</span>
        <span class="session-item__count">{{ s.message_count || '' }}</span>
        <button
          class="session-item__del"
          title="删除对话"
          @click.stop="emit('delete', s.id)"
        >×</button>
      </div>
    </div>

    <div class="sidebar__foot">数据来源：《内地高校招收 DSE 学生指南》</div>
  </aside>
</template>
