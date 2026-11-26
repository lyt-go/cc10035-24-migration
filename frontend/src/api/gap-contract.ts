/** 断点补采时间契约接口：旧断点兼容、纠正、补采单据与降采样交接。 */
import { request } from './client'

export type TimeBound = {
  raw: string
  parseable: boolean
  iso_utc: string | null
  local_iso: string | null
  tz_offset_minutes: number | null
  tz_source: string | null
  inclusive: boolean
  reason: string
}

export type TimeContract = {
  raw_start: string
  raw_end: string
  bracket: string
  parseable: boolean
  reason: string
  start: TimeBound
  end: TimeBound
}

export type SampleSlot = {
  sample_id: string
  iso_utc: string
  local_display: string
  state: string
  signed_at?: string
  signed_order_no?: string
}

export type RecoveryOrder = {
  order_no: string
  original_request_no: string
  origin_workbench: string
  status: string
  created_at: string
  requested_sample_ids: string[]
  signed_sample_ids: string[]
  active: boolean
}

export type HistoryEntry = {
  at: string
  actor: string
  action: string
  detail: string
}

export type GapView = {
  gap_no: string
  设备编号: string
  测点编号: string
  status: string
  source: string
  note: string
  raw_text: { 中断起始: string; 中断结束: string; bracket: string }
  time_contract: TimeContract
  browser_hint: string
  sample_slots: SampleSlot[]
  missing_sample_ids: string[]
  recovery_orders: RecoveryOrder[]
  history: HistoryEntry[]
  handed_to_downsample: boolean
}

export type ContractResponse = {
  ok: boolean
  message: string
  conflict?: boolean
  gap?: GapView
  requested_sample_ids?: string[]
  skipped_signed_sample_ids?: string[]
  eligible?: boolean
  blockers?: string[]
}

async function post(path: string, payload: Record<string, unknown>): Promise<ContractResponse> {
  const response = await request(path, { method: 'POST', body: JSON.stringify(payload) })
  return await response.json()
}

export async function listGaps(): Promise<{ items: GapView[]; total: number }> {
  const response = await request('/api/gap-contract/gaps')
  return await response.json()
}

export async function resetScenario(): Promise<{ ok: boolean; message: string }> {
  const response = await request('/api/gap-contract/scenario/reset', { method: 'POST' })
  return await response.json()
}

export function correctInterval(
  gapNo: string,
  payload: { raw_start: string; raw_end: string; bracket: string; actor: string },
): Promise<ContractResponse> {
  return post(`/api/gap-contract/gaps/${gapNo}/correct`, payload)
}

export function requestRecovery(
  gapNo: string,
  payload: { original_request_no: string; origin_workbench: string; actor: string },
): Promise<ContractResponse> {
  return post(`/api/gap-contract/gaps/${gapNo}/recovery`, payload)
}

export function signSamples(
  gapNo: string,
  payload: { order_no: string; actor: string; sample_ids?: string[] },
): Promise<ContractResponse> {
  return post(`/api/gap-contract/gaps/${gapNo}/sign`, payload)
}

export async function downsampleEligibility(gapNo: string): Promise<ContractResponse> {
  const response = await request(`/api/gap-contract/gaps/${gapNo}/downsample-eligibility`)
  return await response.json()
}

export function handToDownsample(
  gapNo: string,
  payload: { actor: string },
): Promise<ContractResponse> {
  return post(`/api/gap-contract/gaps/${gapNo}/hand-downsample`, payload)
}
