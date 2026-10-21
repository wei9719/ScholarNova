"""Objective output checks are not a generic judge of all natural-language requests."""

from types import SimpleNamespace

import pytest

from app.services.inference.answer_constraints import check_output_contract, parse_output_contract


def history(answer):
    return [SimpleNamespace(role="user", content="模型采用什么数据集？"),
            SimpleNamespace(role="assistant", content=answer)]


@pytest.mark.parametrize("question,answer,expected", [
    ("只回答两句", "精度为 3.14，版本为 Qwen2.5。[S1] 还需复核原文。[S2]", "passed"),
    ("只回答两句", "原文信息\n精度为 3.14。[S1]\n教学解释\n需要先核对输入。[S1]", "passed"),
    ("Only two sentences", "Dr. Li reports 3.14 in the U.S. dataset [S1]. Please verify Fig. 2 [S1].", "passed"),
    ("Only two sentences", "The estimate is approx. 3.14 [S1]. The result needs checking [S1].", "passed"),
    ("只回答两句", "原文信息：缺失掩码在观测点上随机\n采样，遮挡比例为70%。[S1]\n教学解释：连续缺失遮住一段时间。[S1]", "passed"),
    ("只回答两句", "- 随机遮挡观测点 [S1]\n- 连续遮挡时间段 [S1]", "not_checked"),
    ("只回答两句", "随机遮挡观测点 [S1]\n连续遮挡时间段 [S1]", "not_checked"),
    ("只回答两句", "只有一个事实需要返回原文核对。[S1]", "failed"),
    ("最多两句", "只有一个事实需要返回原文核对。[S1]", "passed"),
    ("最多两句", "", "failed"),
    ("只回答两句", "一项观察需要核对。[S1] 另一项需要核对。[S1] 最后一项也需要核对。[S1]", "failed"),
    ("不要只回答两句", "按证据详细展开。[S1]", "not_applicable"),
    ("不要再只回答两句，请详细解释", "按证据详细展开。[S1]", "not_applicable"),
    ("不必仅用两句话，详细解释", "按证据详细展开。[S1]", "not_applicable"),
    ("别再只用两句，请详细解释", "按证据详细展开。[S1]", "not_applicable"),
    ("Do not use only two sentences", "Explain the supplied evidence [S1].", "not_applicable"),
    ("只回答25句", "按证据详细展开。[S1]", "not_checked"),
    ("最多0句", "按证据详细展开。[S1]", "not_checked"),
    ("不用区分原文与教学", "按证据说明即可。[S1]", "not_applicable"),
    ("区分原文与教学", "原文信息：采样是随机的。[S1]\n教学解释：每个位置可能被抽中。[S1]", "passed"),
    ("区分原文与教学", "采样是随机的。[S1] 每个位置可能被抽中。[S1]", "failed"),
])
def test_sentence_and_label_checks(question, answer, expected):
    assert check_output_contract(answer, parse_output_contract(question)).status == expected


def test_dataset_constraint_does_not_ban_model_or_metric_identifiers():
    contract = parse_output_contract("不要重复上一问的数据集", history(
        "STD-PLM 使用 SensorBench-08 数据集，采用 MSE 指标。[S1]",
    ))
    assert contract.forbidden == ("SensorBench-08",)
    assert check_output_contract("STD-PLM 的 MSE 指标需要核对。[S1]", contract).status == "passed"
    assert check_output_contract("SensorBench-08 的缺失掩码需要核对。[S1]", contract).status == "failed"


def test_unidentifiable_object_is_reported_not_checked_not_guessed():
    contract = parse_output_contract("不要重复上一问的数据集", history("STD-PLM 采用随机掩码。[S1]"))
    assert contract.forbidden == () and contract.unchecked
    check = check_output_contract("STD-PLM 的随机掩码需要核对。[S1]", contract)
    assert check.status == "not_checked" and not check.issues and check.unchecked


def test_unsupported_repeat_topic_is_not_claimed_verified():
    contract = parse_output_contract("不要重复上一问的原因", history("因为 MSE 的定义有误。[S1]"))
    assert check_output_contract("需要回到定义核对。[S1]", contract).status == "not_checked"


def test_current_named_dataset_is_not_banned_by_historical_object_check():
    contract = parse_output_contract("SensorBench-08 的方法是什么，不要重复上一问的数据集", history("采用 SensorBench-08 数据集。[S1]"))
    assert contract.forbidden == ()
    assert check_output_contract("SensorBench-08 需要先核对方法。[S1]", contract).status == "not_checked"


def test_later_positive_sentence_requirement_is_not_hidden_by_a_negated_one():
    contract = parse_output_contract("不要再只回答两句，请只回答三句。")
    assert contract.sentence_count == 3
    assert check_output_contract("第一个事实需要核对。[S1] 第二个事实需要核对。[S1] 第三个事实需要核对。[S1]", contract).status == "passed"
