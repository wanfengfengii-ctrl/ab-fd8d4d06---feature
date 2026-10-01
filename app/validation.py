"""Validation of API / form input."""

from __future__ import annotations

import re
from typing import Any, List

MIN_PULSES = 6
MAX_PULSES = 24
# Nanosecond timestamps are kept within a safe integer range so that all
# arithmetic stays well inside 64-bit even after offset correction.
TIME_MIN = -(10**15)
TIME_MAX = 10**15
OFFSET_MIN = -(10**12)
OFFSET_MAX = 10**12
TOL_MAX = 10**12
MIN_PAIRS_MAX = MAX_PULSES


class ValidationError(ValueError):
    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.message = message
        self.field = field


def _coerce_int(value: Any, field: str, label: str) -> int:
    if isinstance(value, bool):
        raise ValidationError(f"{label}必须是整数", field)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not value.is_integer():
            raise ValidationError(f"{label}必须是整数", field)
        return int(value)
    if isinstance(value, str):
        s = value.strip()
        if s.startswith("+"):
            s = s[1:]
        try:
            return int(s)
        except ValueError:
            raise ValidationError(f"{label}必须是整数", field)
    raise ValidationError(f"{label}必须是整数", field)


def _parse_pulse_list(value: Any, field: str, label: str) -> List[int]:
    if isinstance(value, str):
        raw = [p for p in re.split(r"[\s,;]+", value.strip()) if p]
    elif isinstance(value, (list, tuple)):
        raw = list(value)
    else:
        raise ValidationError(f"{label}的格式无效", field)

    if not (MIN_PULSES <= len(raw) <= MAX_PULSES):
        raise ValidationError(
            f"{label}需要 {MIN_PULSES}–{MAX_PULSES} 个脉冲时间，"
            f"当前为 {len(raw)} 个",
            field,
        )

    times: List[int] = []
    for k, item in enumerate(raw):
        t = _coerce_int(item, field, f"{label}第 {k + 1} 个时间")
        if not (TIME_MIN <= t <= TIME_MAX):
            raise ValidationError(
                f"{label}第 {k + 1} 个时间超出允许范围", field
            )
        if times and t <= times[-1]:
            raise ValidationError(
                f"{label}必须严格递增：第 {k + 1} 个时间 {t} "
                f"不大于前一个时间 {times[-1]}",
                field,
            )
        times.append(t)
    return times


def validate_payload(data: Any) -> dict:
    if not isinstance(data, dict):
        raise ValidationError("请求体必须是 JSON 对象")

    params = _validate_common(data)

    shared = data.get("shared_offset_review", False)
    if not isinstance(shared, bool):
        raise ValidationError(
            "共享偏移复核开关必须为布尔值", "shared_offset_review"
        )

    if shared:
        # The first round reuses the standard probe_a / probe_b fields;
        # only the second round needs its own input keys.
        params["A2"] = _parse_pulse_list(
            data.get("round2_probe_a"),
            "round2_probe_a",
            "第二轮探头 A 的脉冲",
        )
        params["B2"] = _parse_pulse_list(
            data.get("round2_probe_b"),
            "round2_probe_b",
            "第二轮探头 B 的脉冲",
        )
    params["shared"] = shared
    return params


def _validate_common(data: dict) -> dict:
    probe_a = _parse_pulse_list(data.get("probe_a"), "probe_a", "探头 A 的脉冲")
    probe_b = _parse_pulse_list(data.get("probe_b"), "probe_b", "探头 B 的脉冲")

    offset_min = _coerce_int(
        data.get("offset_min"), "offset_min", "偏移区间下限"
    )
    offset_max = _coerce_int(
        data.get("offset_max"), "offset_max", "偏移区间上限"
    )
    if not (OFFSET_MIN <= offset_min <= OFFSET_MAX) or not (
        OFFSET_MIN <= offset_max <= OFFSET_MAX
    ):
        raise ValidationError(
            f"偏移量必须在 [{OFFSET_MIN}, {OFFSET_MAX}] 纳秒以内",
            "offset_min",
        )
    if offset_min > offset_max:
        raise ValidationError(
            "偏移区间下限不能大于上限", "offset_min"
        )

    tolerance = _coerce_int(data.get("tolerance"), "tolerance", "符合容差")
    if tolerance < 0:
        raise ValidationError("符合容差不能为负", "tolerance")
    if tolerance > TOL_MAX:
        raise ValidationError("符合容差超出允许范围", "tolerance")

    min_pairs = _coerce_int(
        data.get("min_pairs"), "min_pairs", "最低配对数"
    )
    if min_pairs < 1:
        raise ValidationError("最低配对数至少为 1", "min_pairs")
    if min_pairs > MIN_PAIRS_MAX:
        raise ValidationError(
            f"最低配对数不能超过 {MIN_PAIRS_MAX}", "min_pairs"
        )

    return {
        "A": probe_a,
        "B": probe_b,
        "offset_min": offset_min,
        "offset_max": offset_max,
        "tolerance": tolerance,
        "min_pairs": min_pairs,
    }
