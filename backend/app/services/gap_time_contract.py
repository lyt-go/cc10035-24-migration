"""断点补采时间契约：自由文本时段解析与稳定样本身份。

升级背景：旧版数据断点的「中断起始/中断结束」是自由文本，且降采样任务用的是
另一套区间口径（``开始 ~ 结束`` 的闭区间字符串）。升级后的时间契约要求：

1. 可解析的历史时段**沿用输入自带的时区和开闭区间**，不偷偷换成服务器时区；
2. 无法识别的文本**保留原值供操作人纠正**，绝不用当天日期凑出一个“合法区间”；
3. 样本身份由规范化的 UTC 时刻推导，日期展示格式变化不会重新生成身份。

本模块只做纯函数式的解析与身份计算，不触碰数据仓库，方便单测和前后端口径对齐。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

CST = timezone(timedelta(hours=8))

# 常见时区缩写 → UTC 偏移。旧工作台历史数据里出现的缩写都在这里显式登记，
# 解析时沿用输入声明的时区，而不是回退到运行机器的本地时区。
TZ_ALIASES: dict[str, timezone] = {
    "Z": timezone.utc,
    "UTC": timezone.utc,
    "GMT": timezone.utc,
    "CST": CST,
    "Asia/Shanghai": CST,
}

# 形如 2026-11-26 09:00、2026/11/26T09:00:00、20261126 0900 的时间主体
_DATETIME_RE = re.compile(
    r"^\s*"
    r"(?P<y>\d{4})[-/]?(?P<m>\d{2})[-/]?(?P<d>\d{2})"
    r"[ T]?"
    r"(?P<H>\d{2}):?(?P<M>\d{2})(?::?(?P<S>\d{2}))?"
    r"\s*$"
)
# 形如 +08:00 / +0800 / -05:30 的数字偏移
_OFFSET_RE = re.compile(r"^[+-]\d{2}:?\d{2}$")


@dataclass(frozen=True)
class BoundParse:
    """单侧边界（起点或终点）的解析结果。"""

    raw: str
    parseable: bool
    iso_utc: str | None = None          # 规范化 UTC 时刻（身份与计算口径）
    local_iso: str | None = None        # 沿用输入时区展示的本地时刻
    tz_offset_minutes: int | None = None
    tz_source: str | None = None        # 原始时区记号，便于操作人核对
    inclusive: bool = True
    reason: str = ""                    # 不可解析时的原因，原值仍保留在 raw

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw": self.raw,
            "parseable": self.parseable,
            "iso_utc": self.iso_utc,
            "local_iso": self.local_iso,
            "tz_offset_minutes": self.tz_offset_minutes,
            "tz_source": self.tz_source,
            "inclusive": self.inclusive,
            "reason": self.reason,
        }


def _split_tz_token(token: str) -> tuple[str, str | None]:
    """把 ``2026-11-26 09:00+08:00`` 拆成时间主体和时区记号。"""
    token = token.strip()
    # 数字偏移或 Z 直接贴在时间后面
    match = re.search(r"(Z|[+-]\d{2}:?\d{2})$", token)
    if match:
        return token[: match.start()].strip(), match.group(1)
    for alias in TZ_ALIASES:
        if alias != "Z" and token.endswith(alias):
            return token[: -len(alias)].strip(), alias
    return token, None


def _resolve_tz(tz_token: str | None) -> tuple[timezone | None, str | None]:
    if not tz_token:
        return None, None
    if tz_token in TZ_ALIASES:
        return TZ_ALIASES[tz_token], tz_token
    if _OFFSET_RE.match(tz_token):
        sign = 1 if tz_token[0] == "+" else -1
        digits = tz_token[1:].replace(":", "")
        offset = sign * (int(digits[:2]) * 60 + int(digits[2:4]))
        return timezone(timedelta(minutes=offset)), tz_token
    return None, tz_token


def parse_bound(raw: Any, *, inclusive: bool = True) -> BoundParse:
    """解析单侧时间文本。

    解析失败时返回 ``parseable=False`` 并保留原值；调用方负责让操作人纠正，
    本函数不会回退到“今天 00:00”之类的兜底值。
    """
    text = "" if raw is None else str(raw).strip()
    if not text:
        return BoundParse(raw=text, parseable=False, inclusive=inclusive,
                          reason="时间文本为空")

    body, tz_token = _split_tz_token(text)
    tz, tz_source = _resolve_tz(tz_token)
    if tz_token and tz is None:
        return BoundParse(raw=text, parseable=False, inclusive=inclusive,
                          reason=f"无法识别的时区记号：{tz_token}")

    match = _DATETIME_RE.match(body)
    if not match:
        return BoundParse(raw=text, parseable=False, inclusive=inclusive,
                          reason="时间主体不符合可解析格式")
    try:
        tz_for_build = tz or CST
        local_dt = datetime(
            int(match.group("y")), int(match.group("m")), int(match.group("d")),
            int(match.group("H")), int(match.group("M")),
            int(match.group("S") or 0),
            tzinfo=tz_for_build,
        )
    except ValueError as exc:  # 如 2026-13-40 这类越界日期
        return BoundParse(raw=text, parseable=False, inclusive=inclusive,
                          reason=f"非法日历日期：{exc}")

    utc_dt = local_dt.astimezone(timezone.utc)
    offset_minutes = int(tz_for_build.utcoffset(local_dt).total_seconds() // 60)
    return BoundParse(
        raw=text,
        parseable=True,
        iso_utc=utc_dt.isoformat(timespec="seconds"),
        local_iso=local_dt.isoformat(timespec="seconds"),
        tz_offset_minutes=offset_minutes,
        tz_source=tz_source or ("CST(默认)" if tz is None else tz_source),
        inclusive=inclusive,
    )


def parse_interval(raw_start: Any, raw_end: Any,
                   bracket: str = "[]") -> dict[str, Any]:
    """解析一对起止文本，返回完整时间契约。

    ``bracket`` 沿用历史单据的开闭区间记号：``[``/``(`` 表示起点含/不含，
    ``]``/``)`` 表示终点含/不含。无法识别的括号按闭区间处理但会原样记录。
    """
    start_inclusive = not bracket.startswith("(")
    end_inclusive = not bracket.endswith(")")
    start = parse_bound(raw_start, inclusive=start_inclusive)
    end = parse_bound(raw_end, inclusive=end_inclusive)

    parseable = start.parseable and end.parseable
    reason = ""
    if parseable:
        if datetime.fromisoformat(start.iso_utc) > datetime.fromisoformat(end.iso_utc):
            parseable = False
            reason = "起始时刻晚于结束时刻"
    else:
        failed = [b for b in (start, end) if not b.parseable]
        reason = "；".join(f"{name}{b.reason}" for name, b in
                           (("起始", start), ("结束", end)) if not b.parseable)
        reason = reason or failed[0].reason

    return {
        "raw_start": start.raw,
        "raw_end": end.raw,
        "bracket": bracket,
        "parseable": parseable,
        "reason": reason,
        "start": start.to_dict(),
        "end": end.to_dict(),
    }


def stable_sample_id(point_id: str, iso_utc: str) -> str:
    """按规范化 UTC 时刻生成稳定样本身份。

    身份只与「测点 + 绝对时刻」有关：同一段历史时段无论用哪种日期格式、
    哪个时区重新展示，解析出的 UTC 时刻不变，样本身份就不会重新生成。
    """
    dt = datetime.fromisoformat(iso_utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    utc_text = dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_point = re.sub(r"[^0-9A-Za-z]+", "", point_id) or "POINT"
    return f"SMP-{safe_point}-{utc_text}"


def enumerate_sample_slots(contract: dict[str, Any], step_minutes: int = 10) -> list[dict[str, Any]]:
    """按契约的时区与开闭区间枚举区间内的采样位置。

    枚举在输入时区的本地时间网格上步进，再换算回 UTC 生成身份，保证跨时区
    场景下网格仍然对齐输入侧的整点/整十分。
    """
    if not contract["parseable"]:
        return []
    start_bound = contract["start"]
    end_bound = contract["end"]
    start_dt = datetime.fromisoformat(start_bound["iso_utc"])
    end_dt = datetime.fromisoformat(end_bound["iso_utc"])
    tz = timezone(timedelta(minutes=start_bound["tz_offset_minutes"] or 0))

    cursor = start_dt.astimezone(tz)
    end_local = end_dt.astimezone(tz)
    # 开区间起点本身不属于区间，先推进一步到输入时区的下一个采样网格
    if not start_bound["inclusive"]:
        cursor += timedelta(minutes=step_minutes)
    slots: list[dict[str, Any]] = []
    step = timedelta(minutes=step_minutes)
    # 防御性上限，避免异常数据导致死循环
    for _ in range(10_000):
        if cursor > end_local or (cursor == end_local and not end_bound["inclusive"]):
            break
        iso_utc = cursor.astimezone(timezone.utc).isoformat(timespec="seconds")
        slots.append({
            "sample_id": stable_sample_id(contract.get("point_id", "POINT"), iso_utc),
            "iso_utc": iso_utc,
            "local_display": cursor.isoformat(timespec="seconds"),
        })
        cursor += step
    return slots
