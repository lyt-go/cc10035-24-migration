"""断点补采时间契约路由：升级后的旧断点兼容、纠正、补采与降采样交接。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.gap_contract import gap_contract_service

router = APIRouter(prefix="/api/gap-contract", tags=["断点补采时间契约"])


class CorrectPayload(BaseModel):
    raw_start: str = Field(..., description="纠正后的中断起始原文")
    raw_end: str = Field(..., description="纠正后的中断结束原文")
    bracket: str = Field(default="[]", description="开闭区间记号，如 []、[)")
    actor: str = Field(default="操作人")


class RecoveryPayload(BaseModel):
    original_request_no: str = Field(..., description="原申请号，幂等键")
    origin_workbench: str = Field(default="新工作台")
    actor: str = Field(default="操作人")


class SignPayload(BaseModel):
    order_no: str
    actor: str = Field(default="操作人")
    sample_ids: list[str] | None = None


class HandPayload(BaseModel):
    actor: str = Field(default="操作人")


@router.post("/scenario/reset")
def reset_scenario() -> dict[str, Any]:
    """重置升级演练数据（三处 09:00/09:10/09:20 缺失等）。"""
    gap_contract_service.reset_scenario()
    return {"ok": True, "message": "演练场景已重置",
            "gaps": gap_contract_service.list_gaps().__len__()}


@router.get("/gaps")
def list_gaps() -> dict[str, Any]:
    items = [gap_contract_service.get_gap(r["gap_no"])["gap"]
             for r in gap_contract_service.list_gaps()]
    return {"ok": True, "items": items, "total": len(items)}


@router.get("/gaps/{gap_no}")
def get_gap(gap_no: str) -> dict[str, Any]:
    return gap_contract_service.get_gap(gap_no)


@router.post("/gaps/{gap_no}/correct")
def correct_interval(gap_no: str, payload: CorrectPayload) -> dict[str, Any]:
    return gap_contract_service.correct_interval(
        gap_no, raw_start=payload.raw_start, raw_end=payload.raw_end,
        bracket=payload.bracket, actor=payload.actor)


@router.post("/gaps/{gap_no}/recovery")
def request_recovery(gap_no: str, payload: RecoveryPayload) -> dict[str, Any]:
    return gap_contract_service.request_recovery(
        gap_no, original_request_no=payload.original_request_no,
        origin_workbench=payload.origin_workbench, actor=payload.actor)


@router.post("/gaps/{gap_no}/sign")
def sign_samples(gap_no: str, payload: SignPayload) -> dict[str, Any]:
    return gap_contract_service.sign_samples(
        gap_no, payload.order_no, actor=payload.actor, sample_ids=payload.sample_ids)


@router.get("/gaps/{gap_no}/downsample-eligibility")
def downsample_eligibility(gap_no: str) -> dict[str, Any]:
    return gap_contract_service.downsample_eligibility(gap_no)


@router.post("/gaps/{gap_no}/hand-downsample")
def hand_to_downsample(gap_no: str, payload: HandPayload) -> dict[str, Any]:
    return gap_contract_service.hand_to_downsample(gap_no, actor=payload.actor)
