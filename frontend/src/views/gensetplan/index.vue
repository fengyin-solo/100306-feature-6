<template>
  <section class="page plan-page" data-module="genset-plan">
    <header class="page-head">
      <div>
        <h2>发电机组试机月计划</h2>
        <p class="page-desc">
          按站点列出 {{ month }} 本月该试却还没试的机组与距上次试机天数；
          油量撑不起一小时试机的机组单独标红。点「安排试机」可直接排期并回填试机日期，
          耗油按试机时长从站点油料台账同一份余量中折算扣减，本月已试机组不再重复排入。
        </p>
      </div>
      <div class="page-actions month-picker">
        <button class="btn" type="button" @click="shiftMonth(-1)">上一月</button>
        <input v-model="month" class="month-input" type="month" @change="reload" />
        <button class="btn" type="button" @click="shiftMonth(1)">下一月</button>
        <button class="btn ghost" type="button" @click="resetMonth">回到本月</button>
      </div>
    </header>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">覆盖站点</span>
        <strong class="stat-value">{{ summary['站点数'] ?? 0 }}</strong>
        <span class="stat-sub">其中 {{ summary['无机组站点数'] ?? 0 }} 站暂无机组</span>
      </article>
      <article class="stat-card">
        <span class="stat-label">本月待试机组</span>
        <strong class="stat-value">{{ summary['待试机组数'] ?? 0 }}</strong>
      </article>
      <article class="stat-card danger">
        <span class="stat-label">油量撑不起一小时</span>
        <strong class="stat-value">{{ summary['油量不足机组数'] ?? 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">本月已完成试机</span>
        <strong class="stat-value">{{ summary['本月已试机组数'] ?? 0 }}</strong>
      </article>
    </div>

    <div class="filter-bar">
      <label class="filter-item check">
        <input v-model="onlyShort" type="checkbox" />
        <span>只看油量不足机组</span>
      </label>
      <span class="anchor-tip">天数基准日：{{ anchorDate || '—' }}</span>
      <button class="btn" type="button" @click="reload">刷新计划</button>
    </div>

    <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>
    <p v-if="actionMessage" :class="actionOk ? 'ok-text' : 'error-text'">{{ actionMessage }}</p>

    <div v-if="loading" class="loading-state">计划加载中…</div>

    <div v-else class="site-list">
      <article
        v-for="group in visibleGroups"
        :key="group['站点']"
        class="site-card"
        :class="{ 'is-empty': !group['有无机组'], 'has-short': groupShortCount(group) > 0 }"
      >
        <header class="site-head">
          <h3 class="site-name">{{ group['站点'] }}</h3>
          <span v-if="!group['有无机组']" class="tag tag-muted">暂无机组</span>
          <template v-else>
            <span class="tag tag-pending">待试 {{ group['待试机组'].length }}</span>
            <span v-if="groupShortCount(group)" class="tag tag-danger">
              油量不足 {{ groupShortCount(group) }}
            </span>
            <span v-if="group['本月已试'].length" class="tag tag-done">
              已试 {{ group['本月已试'].length }}
            </span>
            <span class="site-stock">
              油罐余量：
              <strong :class="stockClass(group)">{{ formatStock(group['油料台账余量']) }}</strong>
            </span>
          </template>
        </header>

        <p v-if="!group['有无机组']" class="empty-hint">该站点暂无机组记录</p>

        <table v-else-if="visibleGensets(group).length" class="data-table plan-table">
          <thead>
            <tr>
              <th>机组编号</th>
              <th>机组型号</th>
              <th>距上次试机</th>
              <th>一小时耗油</th>
              <th>站点存油</th>
              <th>状态</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="item in visibleGensets(group)"
              :key="String(item.id)"
              :class="{ 'row-short': item['油量不足'] }"
            >
              <td>{{ item['机组编号'] }}</td>
              <td>{{ item['机组型号'] }}（{{ item['额定功率'] }}）</td>
              <td>
                <span v-if="item['本月已试']">本月 {{ item['上次试机'] }} 已试</span>
                <span v-else-if="item['从未试机']" class="warn-text">从未试机</span>
                <span v-else>{{ item['距上次试机天数'] }} 天（{{ item['上次试机'] }}）</span>
              </td>
              <td>{{ item['小时油耗'] != null ? item['小时油耗'] + ' 升/小时' : '—' }}</td>
              <td>{{ formatStock(item['站点油量储备']) }}</td>
              <td>
                <span v-if="item['本月已试']" class="tag tag-done">本月已试</span>
                <span v-else-if="item['油量不足']" class="tag tag-danger">
                  油量不足<span v-if="item['油量说明']">：{{ item['油量说明'] }}</span>
                </span>
                <span v-else class="tag tag-ok">可安排试机</span>
                <span v-if="item['机组状态'] && item['机组状态'] !== '正常'" class="tag tag-warn">
                  {{ item['机组状态'] }}
                </span>
              </td>
              <td class="row-actions">
                <button
                  v-if="!item['本月已试']"
                  class="link"
                  type="button"
                  @click="openDialog(item)"
                >
                  安排试机
                </button>
                <span v-else class="muted-text">本月无需再排</span>
              </td>
            </tr>
          </tbody>
        </table>

        <p v-else class="empty-hint">
          {{ onlyShort ? '该站点没有油量不足的待试机组' : '本月试机均已完成' }}
        </p>
      </article>

      <p v-if="!visibleGroups.length" class="loading-state">没有符合条件的站点</p>
    </div>

    <!-- 安排试机弹窗 -->
    <div v-if="dialogItem" class="modal-mask" @click.self="closeDialog">
      <div class="modal">
        <h3>安排试机：{{ dialogItem['机组编号'] }}</h3>
        <p class="modal-sub">
          {{ dialogItem['机组型号'] }} · 一小时耗油
          {{ dialogItem['小时油耗'] ?? '—' }} 升 · 当前站点存油
          {{ formatStock(dialogItem['站点油量储备']) }}
        </p>
        <div v-if="dialogItem['油量不足']" class="modal-alert">
          {{ dialogItem['油量说明'] }}。仍可先排计划，但回填完成前必须补油，否则扣油会被拦下。
        </div>

        <form class="modal-form" @submit.prevent="submitTest(false)">
          <label class="form-item">
            <span>试机月份</span>
            <input v-model="formMonth" type="month" disabled />
          </label>
          <label class="form-item">
            <span>试机日期</span>
            <input v-model="formDate" type="date" required />
          </label>
          <label class="form-item">
            <span>试机时长（小时）</span>
            <input v-model.number="formHours" type="number" min="0.1" max="24" step="0.1" required />
          </label>
          <label class="form-item">
            <span>备注</span>
            <input v-model="formRemark" type="text" placeholder="试机情况记录（选填）" />
          </label>
          <p class="estimate">
            预计耗油：<strong>{{ estimatedLiters }}</strong> 升
          </p>
          <p v-if="dialogError" class="error-text">{{ dialogError }}</p>
          <div class="modal-actions">
            <button class="btn" type="button" @click="closeDialog">取消</button>
            <button class="btn" type="submit" :disabled="submitting">仅安排计划</button>
            <button class="btn primary" type="button" :disabled="submitting" @click="submitTest(true)">
              试机完成并回填
            </button>
          </div>
        </form>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type GensetView = {
  id: number
  机组编号: string
  机组型号: string
  额定功率: string
  上次试机: string | null
  距上次试机天数: number | null
  从未试机: boolean
  小时油耗: number | null
  站点油量储备: number | null
  油量不足: boolean
  油量说明: string
  本月已试: boolean
  机组状态: string | null
}

type SiteGroup = {
  站点: string
  有无机组: boolean
  油料台账余量: number | null
  有无油料台账: boolean
  待试机组: GensetView[]
  本月已试: GensetView[]
}

const ENDPOINT = '/api/genset'

function currentMonth(): string {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
}

const month = ref(currentMonth())
const loading = ref(false)
const onlyShort = ref(false)
const errorMessage = ref('')
const actionMessage = ref('')
const actionOk = ref(false)
const groups = ref<SiteGroup[]>([])
const anchorDate = ref('')

const summary = ref<Record<string, number>>({})

const visibleGroups = computed(() => {
  if (!onlyShort.value) return groups.value
  return groups.value
    .map((group) => ({
      ...group,
      待试机组: group.待试机组.filter((item) => item.油量不足),
    }))
    .filter((group) => !group.有无机组 || group.待试机组.length > 0)
})

function visibleGensets(group: SiteGroup): GensetView[] {
  if (onlyShort.value) return group.待试机组.filter((item) => item.油量不足)
  return [...group.待试机组, ...group.本月已试]
}

function groupShortCount(group: SiteGroup): number {
  return group.待试机组.filter((item) => item.油量不足).length
}

function formatStock(value: number | null): string {
  return value == null ? '无台账' : `${value} 升`
}

function stockClass(group: SiteGroup): string {
  if (group.油料台账余量 == null) return 'warn-text'
  return groupShortCount(group) > 0 ? 'danger-text' : 'ok-text'
}

function shiftMonth(delta: number) {
  const [year, mon] = month.value.split('-').map(Number)
  const date = new Date(year, mon - 1 + delta, 1)
  month.value = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`
  void reload()
}

function resetMonth() {
  month.value = currentMonth()
  void reload()
}

async function reload() {
  loading.value = true
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/test-plan/monthly?month=${month.value}`)
    if (!response.ok) {
      const payload = await response.json().catch(() => ({ detail: '试机计划读取失败' }))
      throw new Error(payload.detail ?? '试机计划读取失败')
    }
    const payload = await response.json()
    groups.value = payload.站点 ?? []
    summary.value = payload.汇总 ?? {}
    anchorDate.value = payload.基准日期 ?? ''
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '试机计划读取失败'
    groups.value = []
  } finally {
    loading.value = false
  }
}

// ---- 安排试机弹窗 ----

const dialogItem = ref<GensetView | null>(null)
const formMonth = ref(month.value)
const formDate = ref('')
const formHours = ref(1)
const formRemark = ref('')
const dialogError = ref('')
const submitting = ref(false)

const estimatedLiters = computed(() => {
  if (dialogItem.value?.小时油耗 == null || !formHours.value) return '—'
  return (Number(dialogItem.value.小时油耗) * Number(formHours.value)).toFixed(1)
})

function openDialog(item: GensetView) {
  dialogItem.value = item
  formMonth.value = month.value
  formDate.value = month.value === currentMonth() ? new Date().toISOString().slice(0, 10) : `${month.value}-15`
  formHours.value = 1
  formRemark.value = ''
  dialogError.value = ''
}

function closeDialog() {
  dialogItem.value = null
  dialogError.value = ''
}

async function submitTest(complete: boolean) {
  if (!dialogItem.value) return
  submitting.value = true
  dialogError.value = ''
  try {
    const response = await request(`${ENDPOINT}/${dialogItem.value.id}/test-runs`, {
      method: 'POST',
      body: JSON.stringify({
        values: {
          month: formMonth.value,
          test_date: formDate.value,
          hours: formHours.value,
          complete,
        },
        remark: formRemark.value || null,
      }),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload?.ok) {
      throw new Error(payload?.message ?? '试机安排未生效，请稍后重试')
    }
    actionOk.value = true
    actionMessage.value = payload.message
    closeDialog()
    await reload()
  } catch (error) {
    dialogError.value = error instanceof Error ? error.message : '试机安排失败'
  } finally {
    submitting.value = false
  }
}

onMounted(reload)
</script>

<style scoped>
.plan-page .month-picker {
  gap: 8px;
  align-items: center;
}
.month-input {
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 6px 8px;
  font-size: 13px;
}
.stat-card.danger .stat-value {
  color: #b42318;
}
.stat-sub {
  display: block;
  font-size: 12px;
  color: var(--muted);
  margin-top: 2px;
}
.anchor-tip {
  font-size: 12px;
  color: var(--muted);
  margin-left: auto;
}
.check {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
}
.loading-state {
  text-align: center;
  color: var(--muted);
  padding: 32px 0;
  font-size: 13px;
}
.site-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.site-card {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 14px;
}
.site-card.has-short {
  border-color: #f0a8a0;
}
.site-card.is-empty {
  background: #fafbfc;
}
.site-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}
.site-name {
  margin: 0;
  font-size: 15px;
}
.site-stock {
  margin-left: auto;
  font-size: 12px;
  color: var(--muted);
}
.tag {
  display: inline-block;
  border-radius: 999px;
  padding: 2px 10px;
  font-size: 12px;
  line-height: 18px;
}
.tag-pending {
  background: #eef4ff;
  color: #1f6feb;
}
.tag-danger {
  background: #fdeceb;
  color: #b42318;
}
.tag-done {
  background: #e9f8f0;
  color: #067647;
}
.tag-ok {
  background: #f0fdf4;
  color: #15803d;
}
.tag-warn {
  background: #fffaeb;
  color: #b54708;
}
.tag-muted {
  background: #f1f5f9;
  color: var(--muted);
}
.plan-table th,
.plan-table td {
  vertical-align: middle;
}
.row-short {
  background: #fff7f6;
}
.warn-text {
  color: #b54708;
}
.danger-text {
  color: #b42318;
}
.ok-text {
  color: #067647;
}
.muted-text {
  color: var(--muted);
  font-size: 12px;
}
.empty-hint {
  color: var(--muted);
  font-size: 13px;
  margin: 8px 0 0;
}
.ok-text {
  color: #067647;
}
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 50;
}
.modal {
  width: 460px;
  background: #fff;
  border-radius: 10px;
  padding: 20px 22px;
}
.modal h3 {
  margin: 0 0 4px;
  font-size: 16px;
}
.modal-sub {
  margin: 0 0 12px;
  color: var(--muted);
  font-size: 12px;
}
.modal-alert {
  background: #fdeceb;
  color: #b42318;
  border-radius: 6px;
  padding: 8px 10px;
  font-size: 12px;
  margin-bottom: 12px;
}
.modal-form {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.form-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--muted);
}
.form-item input {
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 7px 9px;
  font-size: 13px;
  color: #1f2937;
}
.form-item input:disabled {
  background: #f8fafc;
}
.estimate {
  margin: 0;
  font-size: 13px;
}
.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 4px;
}
</style>
