"""断点补采时间契约测试：解析口径、样本身份、单据冲突与降采样准入。"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services.gap_contract import gap_contract_service
from app.services.gap_time_contract import (
    enumerate_sample_slots,
    parse_bound,
    parse_interval,
    stable_sample_id,
)


# ======================== 解析器：时区与开闭区间沿用输入 ========================

def test_parse_bound_keeps_input_timezone_instead_of_server_tz():
    bound = parse_bound("2026-11-21 08:00+00:00")
    assert bound.parseable is True
    assert bound.tz_offset_minutes == 0
    # 本地展示仍是输入的 08:00 UTC，换算 UTC 也没有被偷换成 CST
    assert bound.local_iso.startswith("2026-11-21T08:00:00+00:00")
    assert bound.iso_utc == "2026-11-21T08:00:00+00:00"

    cst_bound = parse_bound("2026-11-21 08:00 CST")
    assert cst_bound.tz_offset_minutes == 8 * 60
    assert cst_bound.iso_utc == "2026-11-21T00:00:00+00:00"


def test_unparseable_text_keeps_raw_value_and_never_fabricates_today():
    bound = parse_bound("2026年11月下旬早上九点左右")
    assert bound.parseable is False
    assert bound.raw == "2026年11月下旬早上九点左右"
    assert bound.iso_utc is None
    assert "九点" in bound.reason or "格式" in bound.reason

    contract = parse_interval("2026年11月下旬早上九点左右", "待现场确认", "[]")
    assert contract["parseable"] is False
    assert contract["raw_start"] == "2026年11月下旬早上九点左右"
    assert contract["raw_end"] == "待现场确认"
    assert enumerate_sample_slots(contract) == []


def test_half_open_interval_excludes_end_bound():
    contract = parse_interval(
        "2026-11-21 08:00+00:00", "2026-11-21 08:20+00:00", "[)")
    contract["point_id"] = "TS_P-07"
    slots = enumerate_sample_slots(contract, step_minutes=10)
    # 左闭右开：08:00、08:10 在区间内，08:20 不属于区间
    assert [s["local_display"][11:16] for s in slots] == ["08:00", "08:10"]

    closed = parse_interval(
        "2026-11-21 08:00+00:00", "2026-11-21 08:20+00:00", "[]")
    closed["point_id"] = "TS_P-07"
    closed_slots = enumerate_sample_slots(closed, step_minutes=10)
    assert len(closed_slots) == 3


def test_sample_identity_stable_across_date_format_and_timezone():
    # 同一绝对时刻，换日期格式、换时区展示，身份必须一致
    a = parse_bound("2026-11-20 09:00 CST")
    b = parse_bound("20261120T010000Z")
    assert a.iso_utc == b.iso_utc
    assert stable_sample_id("TS_P-07", a.iso_utc) == stable_sample_id("TS_P-07", b.iso_utc)


def test_reversed_interval_is_rejected_but_raw_kept():
    contract = parse_interval("2026-11-20 10:00", "2026-11-20 09:00", "[]")
    assert contract["parseable"] is False
    assert "晚于" in contract["reason"]
    assert contract["raw_start"] == "2026-11-20 10:00"


# ======================== 端到端：题述升级核对场景 ========================

@pytest.fixture
def scenario():
    gap_contract_service.reset_scenario()
    return gap_contract_service


def _view(scenario, gap_no):
    return scenario.get_gap(gap_no)["gap"]


def test_legacy_three_gaps_first_signed_before_upgrade(scenario):
    gap = _view(scenario, "GAP-LEGACY-01")
    # 09:00 / 09:10 / 09:20 三处采样位置
    assert len(gap["sample_slots"]) == 3
    signed = [s for s in gap["sample_slots"] if s["state"] == "已签收"]
    missing = gap["missing_sample_ids"]
    assert len(signed) == 1 and len(missing) == 2
    # 首处 09:00 是升级前旧单据签收的
    assert signed[0]["local_display"].endswith("09:00:00+08:00")
    assert gap["recovery_orders"][0]["order_no"] == "RO-20261120-OLD-01"
    assert gap["status"] == "补采中"


def test_old_workbench_retry_then_new_workbench_takeover_merge_same_request(scenario):
    gap_no = "GAP-LEGACY-01"
    before = _view(scenario, gap_no)
    first_signed_id = next(
        s["sample_id"] for s in before["sample_slots"] if s["state"] == "已签收")

    # 旧工作台重新发起同一原申请
    old = scenario.request_recovery(
        gap_no, original_request_no="REQ-OLD-5510",
        origin_workbench="旧工作台", actor="旧值班员")
    assert old["ok"] is True and old["conflict"] is True
    # 已签收的 09:00 不在再次请求范围内
    assert first_signed_id in old["skipped_signed_sample_ids"]
    assert first_signed_id not in old["requested_sample_ids"]
    assert len(old["requested_sample_ids"]) == 2

    # 新工作台接手同一原申请：仍是同一单据，不产生第二张单
    new = scenario.request_recovery(
        gap_no, original_request_no="REQ-OLD-5510",
        origin_workbench="新工作台", actor="新值班员")
    assert new["ok"] is True and new["conflict"] is True
    orders = _view(scenario, gap_no)["recovery_orders"]
    assert len(orders) == 1
    assert orders[0]["origin_workbench"] == "旧工作台"
    # 最后核对：只有后两处被再次请求，历史原值仍可查看
    view = _view(scenario, gap_no)
    assert set(new["requested_sample_ids"]) == set(view["missing_sample_ids"])
    assert view["raw_text"]["中断起始"] == "2026-11-20 09:00"
    assert any("冲突合并" in h["action"] for h in view["history"])


def test_signed_sample_identity_not_regenerated(scenario):
    gap = _view(scenario, "GAP-LEGACY-01")
    signed_before = next(
        s["sample_id"] for s in gap["sample_slots"] if s["state"] == "已签收")
    # 用不同日期格式重新解析同一历史时段，身份应保持不变
    re_parsed = parse_interval("20261120 0900", "2026-11-20 09:20", "[]")
    re_parsed["point_id"] = "TS_P-07"
    re_slots = enumerate_sample_slots(re_parsed, step_minutes=10)
    assert re_slots[0]["sample_id"] == signed_before


def test_unparseable_gap_blocks_recovery_until_corrected(scenario):
    gap_no = "GAP-BADTEXT-03"
    blocked = scenario.request_recovery(
        gap_no, original_request_no="REQ-BAD-1", origin_workbench="新工作台")
    assert blocked["ok"] is False
    assert "纠正" in blocked["message"]
    view = _view(scenario, gap_no)
    assert view["status"] == "待纠正"
    assert "待纠正时间" in view["browser_hint"]
    # 原值仍在
    assert view["raw_text"]["中断起始"] == "2026年11月下旬早上九点左右"

    # 纠正成可解析区间后才能发起补采
    corrected = scenario.correct_interval(
        gap_no, raw_start="2026-11-22 09:00 CST",
        raw_end="2026-11-22 09:10 CST", bracket="[]", actor="纠正员")
    assert corrected["ok"] is True
    recovery = scenario.request_recovery(
        gap_no, original_request_no="REQ-BAD-1-FIXED", origin_workbench="新工作台")
    assert recovery["ok"] is True
    assert len(recovery["requested_sample_ids"]) == 2


def test_bad_correction_keeps_original_value(scenario):
    gap_no = "GAP-BADTEXT-03"
    result = scenario.correct_interval(
        gap_no, raw_start="昨天早上", raw_end="明天", bracket="[]", actor="纠正员")
    assert result["ok"] is False
    view = _view(scenario, gap_no)
    # 纠正失败不得覆盖原值
    assert view["raw_text"]["中断起始"] == "2026年11月下旬早上九点左右"
    assert view["status"] == "待纠正"


def test_only_formally_completed_interval_can_go_downsample(scenario):
    # 未补齐的断点：降采样准入被拒，阻塞原因区分待纠正与缺失位置
    legacy = scenario.downsample_eligibility("GAP-LEGACY-01")
    assert legacy["eligible"] is False
    assert any("缺失" in b for b in legacy["blockers"])

    bad = scenario.downsample_eligibility("GAP-BADTEXT-03")
    assert bad["eligible"] is False
    assert any("纠正" in b for b in bad["blockers"])

    # 待纠正断点直接交接也应被拒
    hand = scenario.hand_to_downsample("GAP-BADTEXT-03", actor="测试")
    assert hand["ok"] is False

    # 已正式补齐的区间可以交接，且只能交接一次
    done = scenario.hand_to_downsample("GAP-DONE-04", actor="降采样值班")
    assert done["ok"] is True
    again = scenario.hand_to_downsample("GAP-DONE-04", actor="降采样值班")
    assert again["ok"] is False
    assert "重复交接" in again["message"]


def test_complete_flow_then_handover(scenario):
    gap_no = "GAP-LEGACY-01"
    scenario.request_recovery(
        gap_no, original_request_no="REQ-OLD-5510", origin_workbench="新工作台")
    order_no = _view(scenario, gap_no)["recovery_orders"][0]["order_no"]
    missing = _view(scenario, gap_no)["missing_sample_ids"]
    result = scenario.sign_samples(gap_no, order_no, sample_ids=missing)
    assert result["ok"] is True
    assert _view(scenario, gap_no)["status"] == "已补齐"
    hand = scenario.hand_to_downsample(gap_no, actor="值班员")
    assert hand["ok"] is True
    assert _view(scenario, gap_no)["status"] == "已交降采样"


def test_timezone_variant_preserved_in_contract(scenario):
    gap = _view(scenario, "GAP-TZ-02")
    contract = gap["time_contract"]
    assert contract["parseable"] is True
    assert contract["bracket"] == "[)"
    assert contract["start"]["tz_offset_minutes"] == 0
    # UTC 08:00 == CST 16:00，证明沿用输入时区而不是按 CST 解析
    dt = datetime.fromisoformat(contract["start"]["iso_utc"])
    assert dt.astimezone(timezone.utc).hour == 8
