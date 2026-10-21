"""Bounded numeric metric checks; no code, LaTeX or symbolic expressions are executed."""

from __future__ import annotations

import json
import math
import re

from app.schemas.reader import ReaderCalculationCheck

MAX_EXAMPLES = 3
MAX_VALUES = 50
MAX_ABSOLUTE_VALUE = 1e9
NUMERIC_SCOPE = (
    "只对下列输入按给定指标作本地数值验算；不证明数据来自论文、指标选择、公式转录或推导正确。"
)


def _numbers(value: object) -> list[float]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_VALUES:
        raise ValueError("真实值与预测值须为各含 1 至 50 个数值的列表。")
    if any(type(item) not in (int, float) or abs(item) > MAX_ABSOLUTE_VALUE
           or not math.isfinite(item) for item in value):
        raise ValueError("输入须为有限数值，不能是布尔值，且绝对值不超过 10^9。")
    return [float(item) for item in value]


def _check_example(value: object) -> ReaderCalculationCheck:
    metric = value.get("metric") if isinstance(value, dict) else None
    label = metric if metric in ("mae", "mse", "rmse") else "unknown"
    try:
        if not isinstance(value, dict) or label == "unknown":
            raise ValueError("仅支持明确指定 MAE、MSE 或 RMSE 的结构化数值例子。")
        actual, predicted = _numbers(value.get("y_true")), _numbers(value.get("y_pred"))
        if len(actual) != len(predicted):
            raise ValueError("真实值与预测值数量不一致，未计算。")
        mask = value.get("mask")
        if mask is not None and (
            not isinstance(mask, list) or len(mask) != len(actual)
            or any(type(item) is not int or item not in (0, 1) for item in mask)
        ):
            raise ValueError("mask 须与数值等长且仅含整数 0、1；1 表示参与验算。")
        differences = [p - y for index, (y, p) in enumerate(zip(actual, predicted, strict=True))
                       if mask is None or mask[index] == 1]
        if not differences:
            raise ValueError("mask 未选中任何位置，未计算。")
        claimed = value.get("claimed_result")
        if claimed is not None and (
            type(claimed) not in (int, float) or abs(claimed) > 1e20
            or not math.isfinite(claimed)
        ):
            raise ValueError("声称结果须为有限数值，不能是布尔值，且绝对值不超过 10^20。")
        count = len(differences)
        if label == "mae":
            computed = math.fsum(abs(error) for error in differences) / count
            formula = "MAE = 绝对差之和 / 参与位置数"
        else:
            computed = math.fsum(error * error for error in differences) / count
            formula = "MSE = 平方差之和 / 参与位置数"
            if label == "rmse":
                computed = math.sqrt(computed)
                formula = "RMSE = √(平方差之和 / 参与位置数)"
        status = "computed" if claimed is None else (
            "matched" if math.isclose(computed, claimed, rel_tol=1e-9, abs_tol=1e-12)
            else "mismatch"
        )
        return ReaderCalculationCheck(
            metric=label, status=status, y_true=actual, y_pred=predicted, mask=mask,
            claimed_result=claimed, computed_result=computed,
            detail=f"{formula}，本例参与 {count} 个位置；声称值比较允许 10^-9 相对或 10^-12 绝对误差。",
        )
    except (ValueError, OverflowError) as exc:
        # Invalid data is not echoed, coerced or run through an alternate evaluator.
        return ReaderCalculationCheck(metric=label, status="unsupported", detail=str(exc))


def check_calculations(content: str) -> list[ReaderCalculationCheck]:
    """Missing examples stay empty; malformed requests cannot turn into a checked claim."""
    value = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.I)
    try:
        payload = json.loads(value)
    except (ValueError, TypeError):
        return []
    examples = payload.get("calculation_checks") if isinstance(payload, dict) else None
    if examples is None:
        return []
    if not isinstance(examples, list) or len(examples) > MAX_EXAMPLES:
        return [ReaderCalculationCheck(metric="unknown", status="unsupported",
                                       detail="单次仅接受最多 3 个结构化数值例子，未计算。")]
    return [_check_example(example) for example in examples]


def calculation_notes(checks: list[ReaderCalculationCheck]) -> str:
    if not checks:
        return ""
    labels = {"computed": "已计算，未给声称结果", "matched": "数值一致（仅此算例）",
              "mismatch": "数值不一致", "unsupported": "无法验算"}
    return NUMERIC_SCOPE + "\n" + "\n\n".join(
        f"{check.metric.upper()}：{labels[check.status]}\n"
        f"真实值={check.y_true}；预测值={check.y_pred}；mask={check.mask}（1参与）\n"
        f"模型声称={check.claimed_result}；本地计算={check.computed_result}\n{check.detail}"
        for check in checks
    )
