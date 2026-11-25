<script setup>
import { onMounted, ref } from 'vue'
import KpiGrid from './components/KpiGrid.vue'
import TrendChart from './components/TrendChart.vue'
import ActionBars from './components/ActionBars.vue'
import Double11Panel from './components/Double11Panel.vue'
import SegmentCards from './components/SegmentCards.vue'
import RankingTable from './components/RankingTable.vue'
import PredictForm from './components/PredictForm.vue'

const overview = ref(null)
const daily = ref([])
const actions = ref([])
const double11 = ref(null)
const segments = ref([])
const categories = ref([])
const sellers = ref([])
const merchants = ref([])
const errors = ref([])

async function api(path) {
  const resp = await fetch(path)
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}))
    throw new Error(`${path} -> ${body.detail || resp.status}`)
  }
  return resp.json()
}

async function load() {
  const tasks = [
    ['overview', '/api/overview', (d) => (overview.value = d)],
    ['daily', '/api/daily?start=511&end=1112', (d) => (daily.value = d.rows)],
    ['actions', '/api/actions', (d) => (actions.value = d.rows)],
    ['double11', '/api/double11', (d) => (double11.value = d)],
    ['segments', '/api/segments', (d) => (segments.value = d.rows)],
    ['categories', '/api/top-categories?limit=10', (d) => (categories.value = d.rows)],
    ['sellers', '/api/top-sellers?limit=10', (d) => (sellers.value = d.rows)],
    ['merchants', '/api/merchant-health?limit=10', (d) => (merchants.value = d.rows)],
  ]
  await Promise.all(
    tasks.map(async ([name, path, apply]) => {
      try {
        apply(await api(path))
      } catch (e) {
        errors.value.push(`${name}: ${e.message}`)
      }
    })
  )
}

const categoryCols = [
  { key: 'cat_id', label: '品类 ID', mono: true },
  { key: 'purchases', label: '购买事件', type: 'number' },
  { key: 'share_pct', label: '占比', type: 'pct' },
  { key: 'cumulative_pct', label: '累计占比', type: 'pct' },
]

const sellerCols = [
  { key: 'seller_id', label: '商家 ID', mono: true },
  { key: 'purchases', label: '购买事件', type: 'number' },
  { key: 'buyers', label: '购买用户', type: 'number' },
  { key: 'share_pct', label: '占比', type: 'pct' },
]

const merchantCols = [
  { key: 'merchant_id', label: '商家 ID', mono: true },
  { key: 'samples', label: '官方样本数', type: 'number' },
  { key: 'repurchase', label: '复购样本', type: 'number' },
  { key: 'repurchase_rate', label: '商家复购率', type: 'pct' },
]

onMounted(load)
</script>

<template>
  <div class="app">
    <div class="hero">
      <h1>电商用户行为分析与复购预测看板</h1>
      <p>
        数据源：天猫复购预测公开数据集（阿里云天池），行为日志 54,925,330 行 / 424,170 用户。
        所有数字均来自本地 MySQL 实时查询，前端只做展示。
      </p>
      <p>
        Vue 3 + Vite · FastAPI 后端 · MySQL 8.0 ·
        <a href="/docs" target="_blank" rel="noreferrer">接口文档</a>
      </p>
    </div>

    <div v-if="errors.length" class="caveat">
      <strong>部分数据加载失败：</strong>
      <div v-for="e in errors" :key="e" class="mono" style="margin-top: 4px">{{ e }}</div>
      <div style="margin-top: 6px">
        请确认后端已启动（<span class="mono">start_api.cmd</span>）且 MySQL 正在运行（<span class="mono">start_mysql.cmd</span>）。
      </div>
    </div>

    <div class="caveat">
      <strong>读图前必须知道的口径限制：</strong>
      ① 这份数据的 424,170 个用户全部是 11.11 当天有购买的用户，所以「购买用户占比 100%」没有信息量；
      ② 日志在 1111 结束（1112 仅 46 行），属于数据截断尾巴，<strong>不计入日均</strong>，有效窗口为 185 天；
      ③ 没有会话 ID / 订单号 / 金额，因此不存在会话级转化率、客单价、GMV；
      ④ 只有 mmdd 没有年份，所以没有工作日/周末维度。
    </div>

    <section class="section">
      <div class="section-head">
        <h2>全局概览</h2>
        <span class="hint">/api/overview</span>
      </div>
      <KpiGrid :overview="overview" />
    </section>

    <section class="section">
      <div class="section-head">
        <h2>每日行为趋势</h2>
        <span class="hint">/api/daily · 对数纵轴</span>
      </div>
      <div class="card">
        <TrendChart :rows="daily" metric="events" label="每日行为条数" color="#4c9be8" />
      </div>
      <div class="card" style="margin-top: 14px">
        <TrendChart :rows="daily" metric="purchases" label="每日购买事件" color="#e8834c" />
      </div>
      <div class="note">
        0519 → 0520 有一次 12.22 倍的跳变（18,430 → 225,198 条/天），前 9 天日均仅 21,680 条。
        判断为早期数据采集覆盖不完整，所以「全期日均」会低估后半段的真实强度。
        另外图上最后一个点（1112）骤降到 46 条，那是数据截断的尾巴，**不是真实下跌**，
        因此所有日均指标都按 185 天有效窗口计算。
      </div>
    </section>

    <section class="section">
      <div class="section-head">
        <h2>双十一对比</h2>
        <span class="hint">/api/double11 · 1111 当天 vs 前 7 天均值</span>
      </div>
      <Double11Panel :data="double11" />
    </section>

    <section class="section">
      <div class="section-head">
        <h2>行为结构</h2>
        <span class="hint">/api/actions · 186 天累计</span>
      </div>
      <div class="card">
        <ActionBars :rows="actions" />
      </div>
    </section>

    <section class="section">
      <div class="section-head">
        <h2>用户分层与复购率</h2>
        <span class="hint">/api/segments · 1111 前是否购买</span>
      </div>
      <SegmentCards :rows="segments" />
    </section>

    <section class="section">
      <div class="section-head">
        <h2>品类 TOP 10</h2>
        <span class="hint">/api/top-categories · 帕累托累计</span>
      </div>
      <div class="card">
        <RankingTable :columns="categoryCols" :rows="categories" />
      </div>
      <div class="note">
        购买事件一共分布在 1,324 个品类上，TOP10 累计只占 23.21%，说明品类高度分散、没有垄断。
      </div>
    </section>

    <section class="section">
      <div class="section-head">
        <h2>商家 TOP 10</h2>
        <span class="hint">/api/top-sellers</span>
      </div>
      <div class="card">
        <RankingTable :columns="sellerCols" :rows="sellers" />
      </div>
      <div class="note">4,995 个商家，TOP10 只占 4.28%，商家侧同样极度分散。</div>
    </section>

    <section class="section">
      <div class="section-head">
        <h2>商家健康度</h2>
        <span class="hint">/api/merchant-health · 已过滤样本量不足的商家</span>
      </div>
      <div class="card">
        <RankingTable :columns="merchantCols" :rows="merchants" />
      </div>
      <div class="note">
        按复购率降序，但已排除样本量 &lt;30 的商家——那批商家里出现过 52.94% 的极端值，
        是小样本噪声，不排除会让排行榜失真。
      </div>
    </section>

    <section class="section">
      <div class="section-head">
        <h2>复购概率预测</h2>
        <span class="hint">POST /api/predict · 梯度提升模型</span>
      </div>
      <PredictForm />
    </section>

    <footer>
      <div>
        模型：HistGradientBoosting · 验证集 ROC-AUC 0.6544 / PR-AUC 0.1196 · 5 折 CV 0.6590 ± 0.0049。
        正例率仅 6.12%，AUC 0.65 属于合理水平，不应期待更高。
      </div>
      <div style="margin-top: 6px">
        测试集没有标签，因此不对测试集报任何指标。任何声称测试集 AUC 的说法都需要解释数据来源。
      </div>
    </footer>
  </div>
</template>
