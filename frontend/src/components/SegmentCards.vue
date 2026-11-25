<script setup>
const props = defineProps({
  rows: { type: Array, default: () => [] },
})

const fmt = (n) => Number(n).toLocaleString('zh-CN')
const isOld = (r) => r.segment.includes('老客')
</script>

<template>
  <div v-if="!rows.length" class="loading">加载中…</div>
  <div v-else>
    <div class="grid cols-2">
      <div v-for="r in rows" :key="r.segment" class="card kpi">
        <div class="label">{{ r.segment }}</div>
        <div class="value" :style="{ color: isOld(r) ? 'var(--green)' : 'var(--accent)' }">
          {{ r.repurchase_rate }}<span class="unit">%</span>
        </div>
        <div class="sub">
          样本 {{ fmt(r.samples) }} 条，其中复购 {{ fmt(r.repurchase) }} 条
        </div>
      </div>
    </div>
    <div class="note">
      <strong>这是相关性，不是因果。</strong>老客复购率高可能来自更强的品类需求或更深的信任，
      也可能只是老客本身更活跃；要判断因果需要对照实验，本数据做不到。
    </div>
  </div>
</template>
