<script setup>
import { computed } from 'vue'

const props = defineProps({
  rows: { type: Array, default: () => [] },
  metric: { type: String, default: 'events' },
  color: { type: String, default: '#4c9be8' },
  label: { type: String, default: '行为条数' },
})

const W = 1100
const H = 300
const padL = 56
const padR = 16
const padT = 16
const padB = 34

// 数值跨度从 2 万到 1058 万，必须用对数轴，否则除 1111 外全被压平。
const chart = computed(() => {
  const rows = props.rows
  if (!rows.length) return null

  const values = rows.map((r) => Number(r[props.metric] || 0))
  const positives = values.filter((v) => v > 0)
  const minV = Math.max(1, Math.min(...positives))
  const maxV = Math.max(...positives)
  const lo = Math.floor(Math.log10(minV))
  const hi = Math.ceil(Math.log10(maxV))

  const x = (i) => padL + (i * (W - padL - padR)) / Math.max(1, rows.length - 1)
  const y = (v) => {
    const lv = v > 0 ? Math.log10(v) : lo
    const t = (lv - lo) / Math.max(1, hi - lo)
    return padT + (1 - t) * (H - padT - padB)
  }

  const path = values.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ')

  const ticks = []
  for (let e = lo; e <= hi; e++) ticks.push({ value: 10 ** e, y: y(10 ** e) })

  const idx1111 = rows.findIndex((r) => Number(r.date_key) === 1111)
  const step = Math.max(1, Math.round(rows.length / 12))
  const xLabels = rows
    .map((r, i) => ({ i, text: String(r.date_key).padStart(4, '0') }))
    .filter((d) => d.i % step === 0)

  return { path, ticks, xLabels, x, y, idx1111, values, maxV }
})

function fmtAxis(v) {
  if (v >= 1e6) return (v / 1e6).toFixed(0) + 'M'
  if (v >= 1e3) return (v / 1e3).toFixed(0) + 'k'
  return String(v)
}
</script>

<template>
  <div>
    <div class="legend">
      <span><i class="dot" :style="{ background: color }"></i>{{ label }}</span>
      <span>对数纵轴（跨三个数量级）</span>
    </div>
    <svg v-if="chart" :viewBox="`0 0 ${W} ${H}`" style="width: 100%; height: auto; display: block">
      <!-- 网格与刻度 -->
      <g v-for="t in chart.ticks" :key="t.value">
        <line :x1="padL" :x2="W - padR" :y1="t.y" :y2="t.y" stroke="#2a3542" stroke-width="1" />
        <text :x="padL - 8" :y="t.y + 4" fill="#8b98a5" font-size="11" text-anchor="end">
          {{ fmtAxis(t.value) }}
        </text>
      </g>

      <!-- 1111 竖线 -->
      <line
        v-if="chart.idx1111 >= 0"
        :x1="chart.x(chart.idx1111)"
        :x2="chart.x(chart.idx1111)"
        :y1="padT"
        :y2="H - padB"
        stroke="#e8834c"
        stroke-width="1"
        stroke-dasharray="4 4"
      />
      <text
        v-if="chart.idx1111 >= 0"
        :x="chart.x(chart.idx1111) - 6"
        :y="padT + 12"
        fill="#e8834c"
        font-size="11"
        text-anchor="end"
      >
        1111
      </text>

      <!-- 折线 -->
      <path :d="chart.path" fill="none" :stroke="color" stroke-width="1.6" />

      <!-- x 轴标签 -->
      <text
        v-for="l in chart.xLabels"
        :key="l.i"
        :x="chart.x(l.i)"
        :y="H - padB + 18"
        fill="#8b98a5"
        font-size="11"
        text-anchor="middle"
      >
        {{ l.text }}
      </text>
    </svg>
    <div v-else class="loading">加载中…</div>
  </div>
</template>
