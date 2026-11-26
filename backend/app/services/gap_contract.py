"""断点补采时间契约领域服务。

承载升级后的时间契约行为，与旧版 ``DataGapService`` 的通用 CRUD 并存：

- 旧断点的自由文本原值永不覆盖，解析结果单独挂在 ``time_contract`` 上；
- 补采按「原申请号」幂等，旧工作台与新工作台接手同一原申请会识别为冲突；
- 已签收样本的身份由 UTC 时刻决定，日期换格式不会重新生成；
- 只有正式补回（全部样本签收）的区间才允许交给降采样。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.gap_time_contract import (
    CST,
    enumerate_sample_slots,
    parse_interval,
    stable_sample_id,
)
from app.store import store

CST_TZ = CST
STEP_MINUTES = 10

# 断点业务状态：待纠正优先于待补采，前端据此区分两类浏览器提示
GAP_STATUS = ["待纠正", "待补采", "补采中", "已补齐", "已交降采样", "放弃补采"]
ORDER_STATUS = ["补采中", "已签收", "冲突合并"]


def _now() -> str:
    return datetime.now(CST_TZ).isoformat(timespec="seconds")


class GapContractService:
    MODULE = "data_gap"

    # ------------------------------------------------------------------
    # 场景初始化（升级演练数据，独立于通用种子，可随时重置）
    # ------------------------------------------------------------------
    def reset_scenario(self) -> None:
        """重置升级演练场景：三处 09:00/09:10/09:20 缺失 + 无法解析旧串。"""
        rows = store.rows(self.MODULE)
        # 清掉此前演练写入的契约断点，保留通用种子里的 DATA-000x
        for row in [row for row in rows if row.get("_scenario")]:
            rows.remove(row)

        rows.append(self._build_gap(
            gap_no="GAP-LEGACY-01",
            device="DEV-EDGE-07", point="TS_P-07",
            raw_start="2026-11-20 09:00", raw_end="2026-11-20 09:20",
            bracket="[]", step=STEP_MINUTES,
            source="旧工作台",
            note="升级前已存在的旧断点，首处 09:00 在升级前签收",
        ))
        gap = rows[-1]
        # 升级前已存在的补采单据：正在补采，且 09:00 样本已签收
        pre_order = {
            "order_no": "RO-20261120-OLD-01",
            "original_request_no": "REQ-OLD-5510",
            "origin_workbench": "旧工作台",
            "status": "补采中",
            "created_at": "2026-11-20T10:02:00+08:00",
            "requested_sample_ids": [s["sample_id"] for s in gap["sample_slots"]],
            "signed_sample_ids": [gap["sample_slots"][0]["sample_id"]],
            "active": True,
        }
        gap["recovery_orders"].append(pre_order)
        for slot in gap["sample_slots"]:
            if slot["sample_id"] in pre_order["signed_sample_ids"]:
                slot["state"] = "已签收"
                slot["signed_at"] = "2026-11-20T10:05:00+08:00"
                slot["signed_order_no"] = pre_order["order_no"]
        gap["status"] = "补采中"
        gap["history"].append({
            "at": "2026-11-20T10:02:00+08:00",
            "actor": "旧工作台",
            "action": "升级前发起补采",
            "detail": "原申请 REQ-OLD-5510，区间按旧文本沿用 [2026-11-20 09:00, 2026-11-20 09:20]",
        })
        gap["history"].append({
            "at": "2026-11-20T10:05:00+08:00",
            "actor": "旧工作台",
            "action": "签收样本",
            "detail": f"样本 {pre_order['signed_sample_ids'][0]}（09:00 位置）签收，身份不因升级改变",
        })

        # 可解析的时区/开闭区间变体，验证时区与开闭口径沿用输入
        rows.append(self._build_gap(
            gap_no="GAP-TZ-02",
            device="DEV-EDGE-07", point="TS_P-07",
            raw_start="2026-11-21 08:00+00:00", raw_end="2026-11-21 08:20+00:00",
            bracket="[)", step=STEP_MINUTES,
            source="旧工作台",
            note="UTC 时区、左闭右开区间，解析须沿用输入口径",
        ))

        # 无法识别的旧字符串：保留原值，禁止拿当天凑区间
        rows.append(self._build_gap(
            gap_no="GAP-BADTEXT-03",
            device="DEV-EDGE-08", point="TS_P-08",
            raw_start="2026年11月下旬早上九点左右", raw_end="待现场确认",
            bracket="[]", step=STEP_MINUTES,
            source="旧工作台",
            note="无法解析的自由文本，必须由操作人纠正后才能发起补采",
        ))

        # 已正式补齐的区间，用于演示降采样准入与交接
        rows.append(self._build_gap(
            gap_no="GAP-DONE-04",
            device="DEV-EDGE-09", point="TS_P-09",
            raw_start="2026-11-18 14:00 CST", raw_end="2026-11-18 14:20 CST",
            bracket="[]", step=STEP_MINUTES,
            source="新工作台",
            note="全部样本已正式补回并签收，可交接降采样",
        ))
        done_gap = rows[-1]
        done_order = {
            "order_no": "RO-20261119-NEW-04",
            "original_request_no": "REQ-NEW-2032",
            "origin_workbench": "新工作台",
            "status": "已签收",
            "created_at": "2026-11-19T09:00:00+08:00",
            "requested_sample_ids": [s["sample_id"] for s in done_gap["sample_slots"]],
            "signed_sample_ids": [s["sample_id"] for s in done_gap["sample_slots"]],
            "active": False,
        }
        done_gap["recovery_orders"].append(done_order)
        for slot in done_gap["sample_slots"]:
            slot["state"] = "已签收"
            slot["signed_at"] = "2026-11-19T09:30:00+08:00"
            slot["signed_order_no"] = done_order["order_no"]
        done_gap["status"] = "已补齐"
        done_gap["history"].append({
            "at": "2026-11-19T09:30:00+08:00",
            "actor": "新工作台",
            "action": "全部样本签收",
            "detail": "区间正式补回，等待交接降采样",
        })

    def _build_gap(self, *, gap_no: str, device: str, point: str,
                   raw_start: str, raw_end: str, bracket: str,
                   step: int, source: str, note: str) -> dict[str, Any]:
        contract = parse_interval(raw_start, raw_end, bracket)
        contract["point_id"] = point
        slots = enumerate_sample_slots(contract, step_minutes=step) if contract["parseable"] else []
        gap = {
            "id": max((int(r.get("id", 0)) for r in store.rows(self.MODULE)), default=0) + 1,
            "gap_no": gap_no,
            "记录编号": gap_no,
            "设备编号": device,
            "测点编号": point,
            # 旧自由文本原值原样保留，通用工作台仍显示这两列
            "中断起始": raw_start,
            "中断结束": raw_end,
            "bracket": bracket,
            "step_minutes": step,
            "source": source,
            "note": note,
            "time_contract": contract,
            "sample_slots": slots,
            "recovery_orders": [],
            "history": [{
                "at": _now(),
                "actor": source,
                "action": "升级迁移：解析历史时段",
                "detail": (
                    "时段可解析，沿用输入时区与开闭区间"
                    if contract["parseable"]
                    else f"时段无法解析，保留原值待纠正（{contract['reason']}）"
                ),
            }],
            "handed_to_downsample": False,
            "_scenario": True,
            "pending": True,
            "abnormal": not contract["parseable"],
        }
        gap["status"] = "待补采" if contract["parseable"] else "待纠正"
        gap["处理状态"] = gap["status"]
        return gap

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def list_gaps(self) -> list[dict[str, Any]]:
        return [r for r in store.rows(self.MODULE) if r.get("_scenario")]

    def _find(self, gap_no: str) -> dict[str, Any] | None:
        for row in self.list_gaps():
            if row.get("gap_no") == gap_no:
                return row
        return None

    def get_gap(self, gap_no: str) -> dict[str, Any]:
        gap = self._find(gap_no)
        if gap is None:
            return {"ok": False, "message": f"断点 {gap_no} 不存在", "gap": None}
        return {"ok": True, "message": "ok", "gap": self._view(gap)}

    def _view(self, gap: dict[str, Any]) -> dict[str, Any]:
        """组装前端视图：区分待纠正时间与确实还缺的采样位置。"""
        contract = gap["time_contract"]
        signed = {sid for order in gap["recovery_orders"]
                  for sid in order.get("signed_sample_ids", [])}
        slots = []
        for slot in gap["sample_slots"]:
            slots.append({**slot, "state": slot.get(
                "state", "已签收" if slot["sample_id"] in signed else "缺失")})
        missing_slots = [s for s in slots if s["state"] != "已签收"]
        return {
            "gap_no": gap["gap_no"],
            "设备编号": gap["设备编号"],
            "测点编号": gap["测点编号"],
            "status": gap["status"],
            "source": gap["source"],
            "note": gap["note"],
            "raw_text": {"中断起始": gap["中断起始"], "中断结束": gap["中断结束"],
                         "bracket": gap["bracket"]},
            "time_contract": contract,
            "browser_hint": (
                "待纠正时间：原值无法解析，不能发起补采"
                if not contract["parseable"]
                else f"确实还缺的采样位置 {len(missing_slots)} 处（共 {len(slots)} 处）"
            ),
            "sample_slots": slots,
            "missing_sample_ids": [s["sample_id"] for s in missing_slots],
            "recovery_orders": gap["recovery_orders"],
            "history": gap["history"],
            "handed_to_downsample": gap["handed_to_downsample"],
        }

    # ------------------------------------------------------------------
    # 纠正无法解析的旧文本
    # ------------------------------------------------------------------
    def correct_interval(self, gap_no: str, *, raw_start: str, raw_end: str,
                         bracket: str, actor: str) -> dict[str, Any]:
        gap = self._find(gap_no)
        if gap is None:
            return {"ok": False, "message": f"断点 {gap_no} 不存在"}
        contract = parse_interval(raw_start, raw_end, bracket or gap["bracket"])
        if not contract["parseable"]:
            # 仍然不覆盖任何原值，只记录一次失败的纠正尝试
            gap["history"].append({
                "at": _now(), "actor": actor or "操作人",
                "action": "纠正被驳回",
                "detail": f"新文本仍无法解析：{contract['reason']}；原值保持不变",
            })
            return {"ok": False,
                    "message": f"纠正后的时段仍无法解析：{contract['reason']}，原值未改动",
                    "gap": self._view(gap)}

        contract["point_id"] = gap["测点编号"]
        gap["中断起始"], gap["中断结束"], gap["bracket"] = raw_start, raw_end, contract["bracket"]
        gap["time_contract"] = contract
        gap["sample_slots"] = enumerate_sample_slots(
            contract, step_minutes=gap["step_minutes"])
        gap["status"] = "待补采"
        gap["处理状态"] = gap["status"]
        gap["abnormal"] = False
        gap["history"].append({
            "at": _now(), "actor": actor or "操作人",
            "action": "纠正历史时段",
            "detail": f"原值已更新为可解析区间 {contract['bracket']}"
                      f"[{raw_start}, {raw_end}]，样本身份按 UTC 时刻生成",
        })
        return {"ok": True, "message": "时段已纠正，可以发起补采", "gap": self._view(gap)}

    # ------------------------------------------------------------------
    # 发起补采（原申请号幂等 + 新旧工作台冲突识别）
    # ------------------------------------------------------------------
    def request_recovery(self, gap_no: str, *, original_request_no: str,
                         origin_workbench: str, actor: str = "操作人") -> dict[str, Any]:
        gap = self._find(gap_no)
        if gap is None:
            return {"ok": False, "message": f"断点 {gap_no} 不存在"}
        contract = gap["time_contract"]
        if not contract["parseable"]:
            return {"ok": False,
                    "message": "时间契约无法解析，须先纠正旧文本，禁止按当天日期凑区间发起补采",
                    "gap": self._view(gap)}

        signed = {sid for order in gap["recovery_orders"]
                  for sid in order.get("signed_sample_ids", [])}
        all_ids = [s["sample_id"] for s in gap["sample_slots"]]
        # 已签收样本不再请求：身份稳定，不随日期换格式重新生成
        want_ids = [sid for sid in all_ids if sid not in signed]

        if not want_ids:
            return {"ok": False, "message": "区间内样本均已签收，无需再次补采",
                    "gap": self._view(gap)}

        # 同一原申请号：旧工作台重发与新工作台接手视为同一单据
        existing = next((o for o in gap["recovery_orders"]
                         if o["original_request_no"] == original_request_no), None)
        conflict = False
        if existing is not None:
            conflict = True
            # 幂等复用：只重新请求仍缺失的位置，已签收位置保持原身份不重复请求
            existing["requested_sample_ids"] = sorted(
                set(existing.get("requested_sample_ids", [])) | set(want_ids))
            gap["history"].append({
                "at": _now(), "actor": actor or origin_workbench,
                "action": "补采冲突合并",
                "detail": (
                    f"{origin_workbench} 与 {existing['origin_workbench']} 指向同一原申请 "
                    f"{original_request_no}，沿用原单据继续，仅再次请求 "
                    f"{len(want_ids)} 处缺失位置"
                ),
            })
            order = existing
        else:
            # 同断点存在其它活动单据时也提示工作台冲突，但仍按新原申请挂单
            active = next((o for o in gap["recovery_orders"] if o.get("active")), None)
            order = {
                "order_no": f"RO-{_now()[:10].replace('-', '')}-{len(gap['recovery_orders']) + 1:02d}",
                "original_request_no": original_request_no,
                "origin_workbench": origin_workbench,
                "status": "补采中",
                "created_at": _now(),
                "requested_sample_ids": list(want_ids),
                "signed_sample_ids": [],
                "active": True,
            }
            gap["recovery_orders"].append(order)
            gap["history"].append({
                "at": _now(), "actor": actor or origin_workbench,
                "action": "发起补采",
                "detail": (
                    f"{origin_workbench} 新建补采单 {order['order_no']}，"
                    f"实际请求范围 {len(want_ids)}/{len(all_ids)} 个采样位置（已签收位置不再请求）"
                    + (f"；注意已有活动单据 {active['order_no']}，存在工作台冲突" if active else "")
                ),
            })
            conflict = active is not None

        gap["status"] = "补采中"
        gap["处理状态"] = gap["status"]
        return {
            "ok": True,
            "conflict": conflict,
            "message": (
                f"原申请 {original_request_no} 已存在，按原单据继续，实际再次请求 "
                f"{len(want_ids)} 处缺失位置" if conflict
                else f"补采单 {order['order_no']} 已发起，请求 {len(want_ids)} 处缺失位置"
            ),
            "requested_sample_ids": want_ids,
            "skipped_signed_sample_ids": sorted(signed),
            "gap": self._view(gap),
        }

    # ------------------------------------------------------------------
    # 签收样本 / 补齐区间
    # ------------------------------------------------------------------
    def sign_samples(self, gap_no: str, order_no: str, *, actor: str = "操作人",
                     sample_ids: list[str] | None = None) -> dict[str, Any]:
        gap = self._find(gap_no)
        if gap is None:
            return {"ok": False, "message": f"断点 {gap_no} 不存在"}
        order = next((o for o in gap["recovery_orders"] if o["order_no"] == order_no), None)
        if order is None:
            return {"ok": False, "message": f"补采单 {order_no} 不存在"}

        valid_ids = {s["sample_id"] for s in gap["sample_slots"]}
        targets = sample_ids or list(order["requested_sample_ids"])
        rejected = [sid for sid in targets if sid not in valid_ids]
        if rejected:
            return {"ok": False, "message": f"样本身份不属于该区间：{rejected}"}

        signed_set = set(order["signed_sample_ids"])
        signed_set.update(targets)
        order["signed_sample_ids"] = sorted(signed_set)
        for slot in gap["sample_slots"]:
            if slot["sample_id"] in signed_set:
                slot["state"] = "已签收"
                slot.setdefault("signed_at", _now())
                slot.setdefault("signed_order_no", order_no)

        all_signed = signed_set >= {s["sample_id"] for s in gap["sample_slots"]}
        if all_signed:
            order["status"] = "已签收"
            order["active"] = False
            gap["status"] = "已补齐"
            gap["处理状态"] = gap["status"]
            gap["pending"] = False
        gap["history"].append({
            "at": _now(), "actor": actor or order["origin_workbench"],
            "action": "签收样本",
            "detail": f"补采单 {order_no} 签收 {len(targets)} 个样本，身份沿用 UTC 时刻未重新生成",
        })
        return {"ok": True,
                "message": "区间已全部补齐" if all_signed else "样本已签收，区间仍有缺失位置",
                "gap": self._view(gap)}

    # ------------------------------------------------------------------
    # 降采样准入：只有正式补回的区间才能交接
    # ------------------------------------------------------------------
    def downsample_eligibility(self, gap_no: str) -> dict[str, Any]:
        gap = self._find(gap_no)
        if gap is None:
            return {"ok": False, "message": f"断点 {gap_no} 不存在"}
        contract = gap["time_contract"]
        signed = {sid for order in gap["recovery_orders"]
                  for sid in order.get("signed_sample_ids", [])}
        missing = [s["sample_id"] for s in gap["sample_slots"]
                   if s["sample_id"] not in signed]

        blockers: list[str] = []
        if not contract["parseable"]:
            blockers.append("时间契约待纠正，不能确定降采样区间口径")
        if missing:
            blockers.append(f"仍有 {len(missing)} 处采样位置缺失：{missing}")
        if gap["handed_to_downsample"]:
            blockers.append("区间已交接降采样，避免重复交接")
        eligible = not blockers
        return {
            "ok": True,
            "eligible": eligible,
            "gap_no": gap_no,
            "blockers": blockers,
            "contract_range": (
                None if not contract["parseable"]
                else {"bracket": contract["bracket"],
                      "start_utc": contract["start"]["iso_utc"],
                      "end_utc": contract["end"]["iso_utc"],
                      "sample_count": len(gap["sample_slots"])}
            ),
        }

    def hand_to_downsample(self, gap_no: str, *, actor: str) -> dict[str, Any]:
        eligibility = self.downsample_eligibility(gap_no)
        if not eligibility.get("eligible"):
            return {"ok": False,
                    "message": "降采样交接被拒绝：" + "；".join(eligibility["blockers"])}
        gap = self._find(gap_no)
        gap["handed_to_downsample"] = True
        gap["status"] = "已交降采样"
        gap["处理状态"] = gap["status"]
        gap["pending"] = False
        gap["history"].append({
            "at": _now(), "actor": actor or "操作人",
            "action": "交接降采样",
            "detail": "仅正式补回的区间交接，降采样区间口径与时间契约 UTC 时刻对齐",
        })
        return {"ok": True, "message": f"断点 {gap_no} 区间已正式交接降采样",
                "gap": self._view(gap)}


gap_contract_service = GapContractService()
