<script setup>
const props = defineProps({
  overview: { type: Object, default: null },
})

const fmt = (n) => (n == null ? '—' : Number(n).toLocaleString('zh-CN'))
</script>

<template>
  <div v-if="!overview" class="loading">加载中…</div>
  <div v-else class="grid cols-4">
    <div class="card kpi">
      <div class="label">行为事件总数</div>
      <div class="value">{{ fmt(overview.total_events) }}<span class="unit">条</span></div>
      <div class="sub">日均 {{ fmt(overview.avg_daily_events) }} 条（按有效窗口）</div>
    </div>
    <div class="card kpi">
      <div class="label">去重用户数</div>
      <div class="value">{{ fmt(overview.total_users) }}<span class="unit">人</span></div>
      <div class="sub">与 fact 去重结果等价（已校验）</div>
    </div>
    <div class="card kpi">
      <div class="label">购买事件</div>
      <div class="value">{{ fmt(overview.total_purchases) }}<span class="unit">条</span></div>
      <div class="sub">
        占全部行为 {{ (overview.total_purchases / overview.total_events * 100).toFixed(2) }}%
      </div>
    </div>
    <div class="card kpi">
      <div class="label">有效观测窗口</div>
      <div class="value">{{ overview.effective_days }}<span class="unit">天</span></div>
      <div class="sub mono">
        {{ overview.first_date }}–{{ overview.last_date }}（名义 {{ overview.days }} 天）
      </div>
    </div>
  </div>
</template>
