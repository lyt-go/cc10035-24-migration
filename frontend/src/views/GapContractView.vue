<template>
  <section class="page gap-contract" data-testid="gap-contract-page">
    <header class="page-head">
      <div>
        <h2>断点补采时间契约</h2>
        <p class="page-desc">
          旧断点自由文本兼容升级：可解析时段沿用输入时区与开闭区间；无法识别的保留原值待纠正，
          不以当天日期凑区间。只有正式补回的区间才交接降采样。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn ghost" data-testid="reset-scenario" type="button" @click="onReset">
          重置演练场景
        </button>
      </div>
    </header>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">旧断点总数</span>
        <strong class="stat-value">{{ gaps.length }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">待纠正时间</span>
        <strong class="stat-value warn">{{ countByStatus('待纠正') }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">补采中/待补采</span>
        <strong class="stat-value">{{ countByStatus('补采中') + countByStatus('待补采') }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">已交降采样</span>
        <strong class="stat-value ok">{{ countByStatus('已交降采样') }}</strong>
      </article>
    </div>

    <p v-if="message" class="banner" :class="messageKind" data-testid="banner">{{ message }}</p>

    <div class="layout">
      <!-- 左：断点清单 -->
      <div class="gap-list">
        <table class="data-table">
          <thead>
            <tr>
              <th>断点编号</th>
              <th>测点</th>
              <th>来源</th>
              <th>时间契约</th>
              <th>状态</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="gap in gaps"
              :key="gap.gap_no"
              :data-testid="`gap-row-${gap.gap_no}`"
              :class="{ selected: selectedNo === gap.gap_no }"
              @click="select(gap.gap_no)"
            >
              <td>{{ gap.gap_no }}</td>
              <td>{{ gap['测点编号'] }}</td>
              <td>{{ gap.source }}</td>
              <td>
                <span :class="gap.time_contract.parseable ? 'tag ok' : 'tag warn'">
                  {{ gap.time_contract.parseable ? '可解析' : '待纠正' }}
                </span>
              </td>
              <td><span :class="['tag', statusClass(gap.status)]">{{ gap.status }}</span></td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 右：契约详情 -->
      <div v-if="selected" class="gap-detail">
        <header class="detail-head">
          <div>
            <h3>{{ selected.gap_no }}</h3>
            <p class="page-desc">{{ selected.note }}</p>
          </div>
          <span :class="['tag', statusClass(selected.status)]">{{ selected.status }}</span>
        </header>

        <!-- 1. 旧值 vs 实际请求范围 -->
        <div class="card">
          <h4>时间契约：历史原值与实际请求范围</h4>
          <dl class="kv">
            <dt>历史原值（不可覆盖）</dt>
            <dd>
              <code>{{ selected.raw_text['中断起始'] }}</code>
              <em> ~ </em>
              <code>{{ selected.raw_text['中断结束'] }}</code>
              <span class="bracket">区间记号 {{ selected.raw_text.bracket }}</span>
            </dd>
            <template v-if="selected.time_contract.parseable">
              <dt>实际请求范围（UTC 口径）</dt>
              <dd>
                <code>{{ selected.time_contract.start.iso_utc }}</code>
                <em> ~ </em>
                <code>{{ selected.time_contract.end.iso_utc }}</code>
              </dd>
              <dt>沿用输入时区</dt>
              <dd>
                起点 {{ selected.time_contract.start.tz_source }}
                （UTC{{ formatOffset(selected.time_contract.start.tz_offset_minutes) }}），
                本地展示 {{ selected.time_contract.start.local_iso }}
              </dd>
              <dt>开闭区间</dt>
              <dd>
                起点{{ selected.time_contract.start.inclusive ? '含' : '不含' }} /
                终点{{ selected.time_contract.end.inclusive ? '含' : '不含' }}
              </dd>
            </template>
            <template v-else>
              <dt>无法解析原因</dt>
              <dd class="warn-text">{{ selected.time_contract.reason }}</dd>
              <dt>处置要求</dt>
              <dd class="warn-text">保留原值供操作人纠正，禁止取当天日期凑出合法区间</dd>
            </template>
          </dl>
        </div>

        <!-- 2. 浏览器提示：待纠正 vs 确实还缺 -->
        <div class="card" :class="selected.time_contract.parseable ? 'hint-missing' : 'hint-correct'">
          <h4>浏览器提示</h4>
          <p class="hint" data-testid="browser-hint">{{ selected.browser_hint }}</p>
        </div>

        <!-- 3. 采样位置：缺失与已签收分色，身份稳定 -->
        <div class="card">
          <h4>采样位置（样本身份按 UTC 时刻生成，不随日期格式变化）</h4>
          <ul class="slot-list">
            <li
              v-for="slot in selected.sample_slots"
              :key="slot.sample_id"
              :class="slot.state === '已签收' ? 'slot signed' : 'slot missing'"
              :data-testid="`slot-${slot.sample_id}`"
            >
              <div>
                <strong>{{ slot.local_display }}</strong>
                <code class="sample-id">{{ slot.sample_id }}</code>
              </div>
              <span class="tag" :class="slot.state === '已签收' ? 'ok' : 'warn'">
                {{ slot.state }}
                <template v-if="slot.signed_order_no"> · {{ slot.signed_order_no }}</template>
              </span>
            </li>
            <li v-if="!selected.sample_slots.length" class="empty-state">
              时间契约待纠正，无法枚举采样位置
            </li>
          </ul>
        </div>

        <!-- 4. 补采单据与操作 -->
        <div class="card">
          <h4>补采单据（按原申请号幂等，新旧工作台冲突合并）</h4>
          <table class="data-table inner" v-if="selected.recovery_orders.length">
            <thead>
              <tr><th>单据号</th><th>原申请号</th><th>来源工作台</th><th>状态</th><th>已请求/已签收</th></tr>
            </thead>
            <tbody>
              <tr v-for="order in selected.recovery_orders" :key="order.order_no">
                <td>{{ order.order_no }}</td>
                <td><code>{{ order.original_request_no }}</code></td>
                <td>{{ order.origin_workbench }}</td>
                <td>{{ order.status }}</td>
                <td>{{ order.requested_sample_ids.length }} / {{ order.signed_sample_ids.length }}</td>
              </tr>
            </tbody>
          </table>
          <p v-else class="page-desc">暂无补采单据。</p>

          <div class="action-row">
            <button
              v-if="!selected.time_contract.parseable"
              class="btn primary"
              data-testid="open-correct"
              type="button"
              @click="openCorrect"
            >
              纠正时间后发起补采
            </button>
            <template v-else>
              <button class="btn primary" data-testid="open-recovery" type="button" @click="openRecovery">
                发起/接手补采
              </button>
              <button
                class="btn"
                type="button"
                :disabled="!activeOrder"
                @click="onSignMissing"
              >
                签收剩余缺失样本
              </button>
              <button class="btn" type="button" @click="onCheckEligibility">核对降采样准入</button>
              <button class="btn ghost" type="button" @click="onHandDownsample">交接降采样</button>
            </template>
          </div>
        </div>

        <!-- 5. 区间办理记录 -->
        <div class="card">
          <h4>区间办理记录</h4>
          <ol class="history">
            <li v-for="(entry, idx) in selected.history" :key="idx">
              <span class="history-at">{{ entry.at }}</span>
              <span class="history-actor">{{ entry.actor }}</span>
              <strong>{{ entry.action }}</strong>
              <p>{{ entry.detail }}</p>
            </li>
          </ol>
        </div>
      </div>
      <div v-else class="gap-detail empty-state">请选择左侧断点查看时间契约</div>
    </div>

    <!-- 纠正弹窗 -->
    <div v-if="correctForm.open" class="modal-backdrop" data-testid="correct-modal">
      <form class="modal" @submit.prevent="onCorrect">
        <h3>纠正历史时段（原值将被本次可解析文本替换）</h3>
        <label class="create-field">
          <span>中断起始原文</span>
          <input v-model="correctForm.raw_start" data-testid="correct-start" required />
        </label>
        <label class="create-field">
          <span>中断结束原文</span>
          <input v-model="correctForm.raw_end" data-testid="correct-end" required />
        </label>
        <label class="create-field">
          <span>开闭区间记号（[] / [) / (] / ()）</span>
          <input v-model="correctForm.bracket" data-testid="correct-bracket" required />
        </label>
        <label class="create-field">
          <span>操作人</span>
          <input v-model="correctForm.actor" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="correctForm.open = false">取消</button>
          <button class="btn primary" data-testid="submit-correct" type="submit">提交纠正</button>
        </div>
      </form>
    </div>

    <!-- 补采弹窗 -->
    <div v-if="recoveryForm.open" class="modal-backdrop" data-testid="recovery-modal">
      <form class="modal" @submit.prevent="onRecovery">
        <h3>发起/接手补采</h3>
        <p class="page-desc">
          同一原申请号在旧工作台与新工作台之间按同一单据处理；已签收样本不会被再次请求。
        </p>
        <label class="create-field">
          <span>原申请号（幂等键）</span>
          <input v-model="recoveryForm.original_request_no" data-testid="recovery-request-no" required />
        </label>
        <label class="create-field">
          <span>来源工作台</span>
          <select v-model="recoveryForm.origin_workbench" data-testid="recovery-workbench">
            <option>旧工作台</option>
            <option>新工作台</option>
          </select>
        </label>
        <label class="create-field">
          <span>操作人</span>
          <input v-model="recoveryForm.actor" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="recoveryForm.open = false">取消</button>
          <button class="btn primary" data-testid="submit-recovery" type="submit">确认发起</button>
        </div>
      </form>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  correctInterval,
  downsampleEligibility,
  handToDownsample,
  listGaps,
  requestRecovery,
  resetScenario,
  signSamples,
} from '@/api/gap-contract'
import type { ContractResponse, GapView } from '@/api/gap-contract'

const gaps = ref<GapView[]>([])
const selectedNo = ref('')
const message = ref('')
const messageKind = ref<'ok' | 'err'>('ok')

const correctForm = ref({
  open: false,
  raw_start: '',
  raw_end: '',
  bracket: '[]',
  actor: '操作人',
})
const recoveryForm = ref({
  open: false,
  original_request_no: '',
  origin_workbench: '新工作台',
  actor: '操作人',
})

const selected = computed(() => gaps.value.find((gap) => gap.gap_no === selectedNo.value) ?? null)
const activeOrder = computed(() =>
  selected.value?.recovery_orders.find((order) => order.active) ?? null,
)

function countByStatus(status: string): number {
  return gaps.value.filter((gap) => gap.status === status).length
}

function statusClass(status: string): string {
  if (status === '待纠正') return 'warn'
  if (status === '已补齐' || status === '已交降采样' || status === '已签收') return 'ok'
  return 'info'
}

function formatOffset(offsetMinutes: number | null): string {
  if (offsetMinutes === null) return ''
  const sign = offsetMinutes >= 0 ? '+' : '-'
  const abs = Math.abs(offsetMinutes)
  return `${sign}${String(Math.floor(abs / 60)).padStart(2, '0')}:${String(abs % 60).padStart(2, '0')}`
}

function notify(result: ContractResponse) {
  messageKind.value = result.ok ? 'ok' : 'err'
  message.value = result.message
}

async function reload(keepSelection = true) {
  const payload = await listGaps()
  gaps.value = payload.items
  if (!keepSelection || !selectedNo.value) {
    selectedNo.value = payload.items[0]?.gap_no ?? ''
  }
}

function select(gapNo: string) {
  selectedNo.value = gapNo
  message.value = ''
}

async function onReset() {
  const result = await resetScenario()
  notify(result)
  selectedNo.value = ''
  await reload(false)
}

function openCorrect() {
  if (!selected.value) return
  correctForm.value = {
    open: true,
    raw_start: selected.value.raw_text['中断起始'],
    raw_end: selected.value.raw_text['中断结束'],
    bracket: selected.value.raw_text.bracket,
    actor: '操作人',
  }
}

async function onCorrect() {
  if (!selected.value) return
  const result = await correctInterval(selected.value.gap_no, {
    raw_start: correctForm.value.raw_start,
    raw_end: correctForm.value.raw_end,
    bracket: correctForm.value.bracket,
    actor: correctForm.value.actor,
  })
  notify(result)
  correctForm.value.open = false
  await reload()
}

function openRecovery() {
  recoveryForm.value = {
    open: true,
    original_request_no: selected.value?.recovery_orders[0]?.original_request_no ?? '',
    origin_workbench: '新工作台',
    actor: '操作人',
  }
}

async function onRecovery() {
  if (!selected.value) return
  const result = await requestRecovery(selected.value.gap_no, {
    original_request_no: recoveryForm.value.original_request_no,
    origin_workbench: recoveryForm.value.origin_workbench,
    actor: recoveryForm.value.actor,
  })
  notify(result)
  if (result.conflict) {
    message.value = `工作台冲突已按原单据合并：${result.message}`
  }
  recoveryForm.value.open = false
  await reload()
}

async function onSignMissing() {
  if (!selected.value || !activeOrder.value) return
  const result = await signSamples(selected.value.gap_no, {
    order_no: activeOrder.value.order_no,
    actor: '操作人',
    sample_ids: selected.value.missing_sample_ids,
  })
  notify(result)
  await reload()
}

async function onCheckEligibility() {
  if (!selected.value) return
  const result = await downsampleEligibility(selected.value.gap_no)
  if (result.eligible) {
    messageKind.value = 'ok'
    message.value = '准入通过：区间已正式补回，可交接降采样'
  } else {
    messageKind.value = 'err'
    message.value = `准入被拒：${(result.blockers ?? []).join('；')}`
  }
}

async function onHandDownsample() {
  if (!selected.value) return
  const result = await handToDownsample(selected.value.gap_no, { actor: '操作人' })
  notify(result)
  await reload()
}

onMounted(() => void reload(false))
</script>

<style scoped>
.layout {
  display: grid;
  grid-template-columns: minmax(360px, 0.9fr) minmax(480px, 1.4fr);
  gap: 14px;
  align-items: start;
}
.gap-list .data-table tr {
  cursor: pointer;
}
.gap-list .data-table tr.selected {
  outline: 2px solid var(--brand);
  outline-offset: -2px;
}
.gap-detail {
  display: flex;
  flex-direction: column;
  gap: 12px;
  background: transparent;
}
.detail-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 14px;
}
.detail-head h3 {
  margin: 0 0 4px;
}
.card {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 14px;
}
.card h4 {
  margin: 0 0 10px;
  font-size: 14px;
}
.kv {
  display: grid;
  grid-template-columns: 150px 1fr;
  gap: 8px 12px;
  margin: 0;
  font-size: 13px;
}
.kv dt {
  color: var(--muted);
}
.kv dd {
  margin: 0;
}
.bracket {
  margin-left: 8px;
  color: var(--muted);
  font-size: 12px;
}
.tag {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 12px;
  border: 1px solid transparent;
}
.tag.ok {
  background: #dcfce7;
  color: #166534;
}
.tag.warn {
  background: #fef3c7;
  color: #92400e;
}
.tag.info {
  background: #dbeafe;
  color: #1e40af;
}
.warn-text {
  color: #b45309;
}
.banner {
  padding: 8px 12px;
  border-radius: 6px;
  font-size: 13px;
}
.banner.ok {
  background: #dcfce7;
  color: #166534;
}
.banner.err {
  background: #fee2e2;
  color: #991b1b;
}
.hint-correct {
  border-color: #f59e0b;
  background: #fffbeb;
}
.hint-missing {
  border-color: var(--brand);
  background: #eff6ff;
}
.hint {
  margin: 0;
  font-size: 13px;
}
.slot-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.slot {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 10px;
  border-radius: 6px;
  border: 1px solid var(--border);
  font-size: 13px;
}
.slot.signed {
  background: #f0fdf4;
  border-color: #86efac;
}
.slot.missing {
  background: #fff7ed;
  border-color: #fdba74;
}
.sample-id {
  margin-left: 10px;
  color: var(--muted);
  font-size: 12px;
}
.data-table.inner {
  margin-bottom: 10px;
}
.action-row {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.action-row .btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.history {
  margin: 0;
  padding-left: 18px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  font-size: 13px;
}
.history-at {
  color: var(--muted);
  margin-right: 8px;
}
.history-actor {
  margin-right: 8px;
  color: var(--brand);
}
.history p {
  margin: 2px 0 0;
  color: #374151;
}
select {
  width: 100%;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 8px;
}
</style>
