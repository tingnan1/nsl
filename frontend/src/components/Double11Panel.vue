<script setup>
const props = defineProps({
  data: { type: Object, default: null },
})

const fmt = (n) => Number(n).toLocaleString('zh-CN')
const mult = (n) => (n == null ? '—' : Number(n).toFixed(2) + '×')
</script>

<template>
  <div v-if="!data" class="loading">加载中…</div>
  <div v-else>
    <div class="grid cols-3">
      <div class="card kpi">
        <div class="label">行为条数</div>
        <div class="value">{{ mult(data.events_multiple) }}</div>
        <div class="sub">
          {{ fmt(data.prev_events_avg) }} → {{ fmt(data.day_events) }}
        </div>
      </div>
      <div class="card kpi">
        <div class="label">购买事件</div>
        <div class="value" style="color: var(--accent-2)">{{ mult(data.purchases_multiple) }}</div>
        <div class="sub">
          {{ fmt(data.prev_purchases_avg) }} → {{ fmt(data.day_purchases) }}
        </div>
      </div>
      <div class="card kpi">
        <div class="label">活跃用户 UV</div>
        <div class="value">{{ mult(data.uv_multiple) }}</div>
        <div class="sub">
          {{ fmt(data.prev_uv_avg) }} → {{ fmt(data.day_uv) }}
        </div>
      </div>
    </div>
    <div class="note">
      购买事件的放大倍数（88.98×）远高于行为总量（8.97×）：说明 1111 不只是「人变多了」，
      更是单位行为的转化效率大幅提升。UV 等于全量用户，是因为这份数据的用户本来就是 11.11 买家。
    </div>
  </div>
</template>
