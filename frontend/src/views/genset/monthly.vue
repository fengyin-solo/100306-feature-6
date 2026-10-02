<template>
  <section class="page" data-module="genset-monthly">
    <header class="page-head">
      <div>
        <h2>发电机组月度试机视图</h2>
        <p class="page-desc">
          按月列出各站点本月该试机却还没试的机组与距上次试机天数；油量撑不起一小时试机的机组单独标红。
        </p>
      </div>
      <div class="page-actions">
        <RouterLink class="btn" to="/genset">返回发电机组台账</RouterLink>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value" :class="{ 'warn-text': item.warn }">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>试机月份</span>
        <input v-model="month" type="month" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetMonth">回到本月</button>
    </form>

    <p v-if="successMessage" class="success-text">{{ successMessage }}</p>
    <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>

    <div v-for="site in sites" :key="site.站点" class="site-card">
      <header class="site-head">
        <h3 class="site-title">{{ site.站点 }}</h3>
        <span v-if="site.暂无机组" class="badge badge-muted">暂无机组</span>
        <template v-else>
          <span class="site-meta">
            油量储备：<b :class="{ 'warn-text': site.油量不足数 > 0 }">{{ formatStock(site.油量储备) }}</b> 升
          </span>
          <span v-if="site.无油料台账" class="badge badge-warn">无油料台账</span>
          <span v-if="site.待试机数" class="badge badge-info">待试机 {{ site.待试机数 }}</span>
          <span v-if="site.油量不足数" class="badge badge-danger">油量不足 {{ site.油量不足数 }}</span>
          <span v-if="site.本月已试数" class="badge badge-ok">本月已试 {{ site.本月已试数 }}</span>
          <span v-if="site.不可排数" class="badge badge-muted">暂不可排 {{ site.不可排数 }}</span>
        </template>
      </header>

      <p v-if="site.暂无机组" class="empty-inline">该站点暂无发电机组记录，不计入待试机。</p>

      <table v-else class="data-table">
        <thead>
          <tr>
            <th>机组编号</th>
            <th>机组型号</th>
            <th>机组状态</th>
            <th>上次试机</th>
            <th>距上次试机</th>
            <th>小时油耗（升/小时）</th>
            <th>油量储备（升）</th>
            <th>一小时试机</th>
            <th>本月状态</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="unit in site.机组" :key="String(unit.id)" :class="{ 'row-short': unit.油量不足 }">
            <td>{{ unit.机组编号 }}</td>
            <td>{{ unit.机组型号 ?? '—' }}</td>
            <td>{{ unit.机组状态 ?? '—' }}</td>
            <td>{{ unit.上次试机 || '从未试机' }}</td>
            <td>{{ unit.距上次试机天数 === null ? '—' : `${unit.距上次试机天数} 天` }}</td>
            <td>{{ unit.小时油耗 === null ? '—' : unit.小时油耗 }}</td>
            <td :class="{ 'warn-text': unit.油量不足 }">{{ formatStock(unit.油量储备) }}</td>
            <td>
              <span v-if="unit.够跑一小时 === null">—</span>
              <span v-else-if="unit.够跑一小时" class="badge badge-ok">够跑一小时</span>
              <span v-else class="badge badge-danger">撑不起一小时</span>
            </td>
            <td>
              <span v-if="unit.本月已试" class="badge badge-ok">本月已试</span>
              <span v-else-if="unit.不可排试" class="badge badge-muted">{{ unit.不可排原因 }}</span>
              <span v-else-if="unit.无油料台账" class="badge badge-warn">待试机 · 无油料台账</span>
              <span v-else-if="unit.油量不足" class="badge badge-danger">待试机 · 油量不足</span>
              <span v-else class="badge badge-info">待试机</span>
            </td>
            <td class="row-actions">
              <button
                v-if="unit.待试机"
                class="link"
                type="button"
                @click="openTrial(unit, site)"
              >
                安排试机
              </button>
              <span v-else class="text-muted">—</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <footer class="page-foot">
      <span>共 {{ sites.length }} 个站点 · {{ planSummary.机组总数 ?? 0 }} 台机组</span>
      <span v-if="loading">加载中…</span>
    </footer>

    <div v-if="dialog.unit" class="modal-mask" @click.self="closeTrial">
      <div class="modal">
        <header class="modal-head">
          <h3>安排试机 · {{ dialog.unit.机组编号 }}</h3>
          <button class="link" type="button" @click="closeTrial">关闭</button>
        </header>
        <div class="modal-body">
          <p class="modal-line">
            所属站点：{{ dialog.site?.站点 }} ｜ 机组型号：{{ dialog.unit.机组型号 ?? '—' }}
          </p>
          <p class="modal-line">
            小时油耗：{{ dialog.unit.小时油耗 ?? '—' }} 升/小时 ｜
            站点油量储备：<b :class="{ 'warn-text': dialog.unit.油量不足 }">{{ formatStock(dialog.unit.油量储备) }}</b> 升
          </p>
          <p v-if="dialog.unit.油量不足" class="error-text">
            当前油量撑不起一小时试机，可缩短试机时长或先到油料管理补油后再排。
          </p>
          <label class="filter-item">
            <span>试机日期（回填到机组台账）</span>
            <input v-model="dialog.form.date" type="date" />
          </label>
          <label class="filter-item">
            <span>试机时长（小时）</span>
            <input v-model.number="dialog.form.duration" type="number" min="0.25" max="24" step="0.25" />
          </label>
          <p class="modal-line text-muted">
            预计耗油：{{ estimatedBurn }} 升（按小时油耗 × 试机时长折算，从油料台账当前存量扣除）
          </p>
        </div>
        <footer class="modal-foot">
          <button class="btn" type="button" @click="closeTrial">取消</button>
          <button class="btn primary" type="button" :disabled="dialog.submitting" @click="submitTrial">
            {{ dialog.submitting ? '提交中…' : '确认试机并回填' }}
          </button>
        </footer>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type MaybeNum = number | null

interface PlanUnit {
  id: number
  机组编号: string
  机组型号: string | null
  额定功率: MaybeNum
  机组状态: string | null
  上次试机: string
  距上次试机天数: number | null
  小时油耗: MaybeNum
  油量储备: MaybeNum
  够跑一小时: boolean | null
  油量不足: boolean
  无油料台账: boolean
  本月已试: boolean
  待试机: boolean
  不可排试: boolean
  不可排原因: string
}

interface PlanSite {
  站点: string
  暂无机组: boolean
  无油料台账: boolean
  油量储备: MaybeNum
  待试机数: number
  本月已试数: number
  油量不足数: number
  不可排数: number
  机组: PlanUnit[]
}

interface MonthlyPlan {
  月份: string
  机组总数: number
  待试机数: number
  本月已试数: number
  油量不足数: number
  暂不可排数: number
  站点: PlanSite[]
}

function currentMonth(): string {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
}

const month = ref(currentMonth())
const sites = ref<PlanSite[]>([])
const planSummary = ref<Partial<MonthlyPlan>>({})
const loading = ref(false)
const errorMessage = ref('')
const successMessage = ref('')

const stats = computed(() => [
  { label: `待试机机组（${month.value}）`, value: planSummary.value.待试机数 ?? 0, warn: false },
  { label: '本月已试', value: planSummary.value.本月已试数 ?? 0, warn: false },
  { label: '油量撑不起一小时', value: planSummary.value.油量不足数 ?? 0, warn: (planSummary.value.油量不足数 ?? 0) > 0 },
  { label: '故障/维修暂不可排', value: planSummary.value.暂不可排数 ?? 0, warn: false },
])

interface DialogState {
  unit: PlanUnit | null
  site: PlanSite | null
  form: { date: string; duration: number }
  submitting: boolean
}

const dialog = ref<DialogState>({
  unit: null,
  site: null,
  form: { date: '', duration: 1 },
  submitting: false,
})

const estimatedBurn = computed(() => {
  const { unit, form } = dialog.value
  if (!unit || unit.小时油耗 === null) return '—'
  return round1(unit.小时油耗 * Number(form.duration || 0))
})

function round1(value: number): number {
  return Math.round(value * 100) / 100
}

function formatStock(value: MaybeNum): string {
  if (value === null || value === undefined) return '—'
  return String(value)
}

function resetMonth() {
  month.value = currentMonth()
  void reload()
}

function openTrial(unit: PlanUnit, site: PlanSite) {
  errorMessage.value = ''
  dialog.value = {
    unit,
    site,
    form: { date: new Date().toISOString().slice(0, 10), duration: 1 },
    submitting: false,
  }
}

function closeTrial() {
  dialog.value.unit = null
  dialog.value.site = null
  dialog.value.submitting = false
}

async function submitTrial() {
  const unit = dialog.value.unit
  if (!unit) return
  if (!dialog.value.form.date) {
    errorMessage.value = '请选择试机日期'
    return
  }
  const duration = Number(dialog.value.form.duration)
  if (!Number.isFinite(duration) || duration <= 0) {
    errorMessage.value = '请填写有效的试机时长（小时）'
    return
  }
  dialog.value.submitting = true
  errorMessage.value = ''
  try {
    const response = await request(`/api/genset/${unit.id}/trial`, {
      method: 'POST',
      body: JSON.stringify({
        values: {
          试机日期: dialog.value.form.date,
          试机时长: duration,
        },
      }),
    })
    const payload = (await response.json()) as { ok: boolean; message: string }
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message || '试机安排未生效，请稍后重试')
    }
    const doneUnit = unit.机组编号
    closeTrial()
    await reload()
    successMessage.value = `${doneUnit}：${payload.message}`
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '试机安排失败'
    dialog.value.submitting = false
  }
}

async function reload() {
  loading.value = true
  errorMessage.value = ''
  successMessage.value = ''
  try {
    const response = await request(`/api/genset/monthly-plan?month=${encodeURIComponent(month.value)}`)
    if (!response.ok) {
      const detail = (await response.json().catch(() => null)) as { detail?: string } | null
      throw new Error(detail?.detail || '月度试机视图读取失败')
    }
    const payload = (await response.json()) as MonthlyPlan
    sites.value = payload.站点 ?? []
    planSummary.value = payload
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '月度试机视图读取失败'
    sites.value = []
  } finally {
    loading.value = false
  }
}

onMounted(reload)
</script>
