<script setup>
const props = defineProps({
  columns: { type: Array, required: true },
  rows: { type: Array, default: () => [] },
  emptyText: { type: String, default: '暂无数据' },
})

function format(value, col) {
  if (value == null) return '—'
  if (col.type === 'number') return Number(value).toLocaleString('zh-CN')
  if (col.type === 'pct') return Number(value).toFixed(2) + '%'
  return String(value)
}
</script>

<template>
  <div class="table-wrap">
    <table v-if="rows.length">
      <thead>
        <tr>
          <th v-for="col in columns" :key="col.key">{{ col.label }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(row, i) in rows" :key="i">
          <td v-for="col in columns" :key="col.key" :class="col.mono ? 'mono' : ''">
            {{ format(row[col.key], col) }}
          </td>
        </tr>
      </tbody>
    </table>
    <div v-else class="loading">{{ emptyText }}</div>
  </div>
</template>
