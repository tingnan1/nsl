<script setup>
import { ref } from 'vue'

const userId = ref(34176)
const merchantId = ref(4356)
const loading = ref(false)
const error = ref('')
const result = ref(null)

async function submit() {
  error.value = ''
  result.value = null
  const uid = Number(userId.value)
  const mid = Number(merchantId.value)
  if (!Number.isInteger(uid) || uid < 1 || uid > 424170) {
    error.value = 'user_id 需在 1 ~ 424170 之间'
    return
  }
  if (!Number.isInteger(mid) || mid < 1 || mid > 4995) {
    error.value = 'merchant_id 需在 1 ~ 4995 之间'
    return
  }
  loading.value = true
  try {
    const resp = await fetch('/api/predict', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: uid, merchant_id: mid }),
    })
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}))
      throw new Error(body.detail || `HTTP ${resp.status}`)
    }
    result.value = await resp.json()
  } catch (e) {
    error.value = String(e.message || e)
  } finally {
    loading.value = false
  }
}

async function pickSample(label) {
  try {
    const resp = await fetch(`/api/sample-pairs?label=${label}&limit=1`)
    const data = await resp.json()
    if (data.rows && data.rows.length) {
      userId.value = data.rows[0].user_id
      merchantId.value = data.rows[0].merchant_id
      result.value = null
    }
  } catch (e) {
    error.value = String(e)
  }
}
</script>

<template>
  <div class="card">
    <div class="form-row">
      <div class="field">
        <label>user_id</label>
        <input v-model="userId" type="number" min="1" max="424170" />
      </div>
      <div class="field">
        <label>merchant_id</label>
        <input v-model="merchantId" type="number" min="1" max="4995" />
      </div>
      <button :disabled="loading" @click="submit">
        {{ loading ? '预测中…' : '预测复购概率' }}
      </button>
      <button class="secondary" @click="pickSample(1)">取一个真实复购样本</button>
      <button class="secondary" @click="pickSample(0)">取一个真实未复购样本</button>
    </div>

    <div v-if="error" class="error" style="margin-top: 12px">{{ error }}</div>

    <div v-if="result" class="result">
      <span class="prob" :class="{ low: result.repurchase_probability < 0.1 }">
        {{ (result.repurchase_probability * 100).toFixed(2) }}%
      </span>
      <span style="color: var(--muted); font-size: 13px">
        user {{ result.user_id }} × merchant {{ result.merchant_id }}
        <span v-if="result.cold_start" class="tag" style="margin-left: 8px">冷启动：日志中无行为</span>
      </span>
    </div>

    <div v-if="result" class="table-wrap" style="margin-top: 14px">
      <table>
        <thead>
          <tr>
            <th>特征</th>
            <th>取值</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(v, k) in result.features" :key="k">
            <td class="mono">{{ k }}</td>
            <td>{{ typeof v === 'number' && !Number.isInteger(v) ? v.toFixed(6) : v }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="note">
      特征由后端 SQL 现算，不是硬编码。阈值 0.5 对这份数据不合理（正例率仅 6.12%），
      实际使用时应按业务能接受的触达量另定阈值。
    </div>
  </div>
</template>
