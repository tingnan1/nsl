<script setup>
import { computed } from 'vue'

const props = defineProps({
  rows: { type: Array, default: () => [] },
})

const COLORS = ['#4c9be8', '#e8834c', '#4cb782', '#b58ce8']

// 事件数从 7.6 万到 4855 万，跨两个数量级，条形宽度用对数才有可比性。
const bars = computed(() => {
  const rows = props.rows
  if (!rows.length) return []
  const maxLog = Math.log10(Math.max(...rows.map((r) => r.events)) + 1)
  return rows.map((r, i) => ({
    ...r,
    color: COLORS[i % COLORS.length],
    widthPct: (Math.log10(r.events + 1) / maxLog) * 100,
  }))
})

const fmt = (n) => Number(n).toLocaleString('zh-CN')
</script>

<template>
  <div v-if="!bars.length" class="loading">加载中…</div>
  <div v-else>
    <div v-for="b in bars" :key="b.action_type" style="margin-bottom: 14px">
      <div style="display: flex; justify-content: space-between; font-size: 13px; margin-bottom: 4px">
        <span>
          <i class="dot" :style="{ background: b.color, display: 'inline-block', width: '9px', height: '9px', borderRadius: '50%', marginRight: '7px' }"></i>
          {{ b.action_name }}
        </span>
        <span class="mono" style="color: var(--muted)">
          {{ fmt(b.events) }} 条 · {{ fmt(b.users) }} 人 · {{ b.event_share_pct }}%
        </span>
      </div>
      <div style="background: var(--panel-2); border-radius: 5px; height: 10px; overflow: hidden">
        <div :style="{ width: b.widthPct + '%', background: b.color, height: '100%', borderRadius: '5px' }"></div>
      </div>
    </div>
    <div class="note">
      条形宽度是按对数的，因为点击量是加购量的 632 倍，线性刻度下后者几乎不可见。
    </div>
  </div>
</template>
