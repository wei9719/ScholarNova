"""Opt-in, capped real-provider text probes; no retries, fallback or private papers.

This exercises the production LLM gateway, not the complete desktop workflow.
Only a maximum of 12 synthetic requests at concurrency 1/2/3 are permitted.
Read credentials from the explicitly selected local runtime; never copy them.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import math
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

CASES = [
    ("title_translation", "translation", "把标题 Retrieval-Augmented Generation for Scientific Literature Review 翻译成中文，只输出译文。"),
    ("grounded_answer", "assistant", "只用材料回答，40字以内并保留来源号。材料[S1]：方法A在20篇模拟论文上检索到16篇相关论文。问题：召回率是多少？"),
    ("query_json", "query_planning", '为“食品制造中的大语言模型应用”给出两个英文检索词。只输出JSON对象，字段queries是两个字符串的数组。'),
    ("product_help", "assistant", "你是ScholarNova助手。产品事实：先搜索文献，再分析论文，再保存知识库，最后按研究文件夹问答。用户问：我该怎么开始？用40字内回答。"),
    ("research_question", "recommendation", "材料：小样本论文检索在跨领域数据上尚未验证。给出一个可检验的研究问题和一个评价指标，合计60字以内。"),
    ("history_followup", "assistant", "对话历史：用户想研究文献检索，已决定比较BM25和向量检索，下一步是选指标。当前问题：那应该选哪些？用50字以内回答，不再重复介绍软件。"),
    ("paper_summary", "analysis", "模拟论文：目标为降低检索延迟。方法是缓存重复查询；在100次合成查询中平均耗时从200ms降为120ms。局限为未测真实用户。按目标、方法、结果、局限四项总结，不超过80字。"),
    ("conflicting_evidence", "assistant", "只依据材料回答，不调和矛盾：材料[S1]报告方法A优于B；材料[S2]在另一数据集报告B优于A。问：A是否总是更好？40字内，保留两个来源号。"),
    ("missing_evidence", "assistant", "只依据材料回答，不补充不存在的数字。材料[S1]只介绍系统设计，没有用户量和准确率。问：准确率达到99%了吗？40字以内。"),
    ("route_steps", "recommendation", "研究目标：比较关键词检索与混合检索的效果。列出三个先后步骤，每步10字以内，不编造实验结果。"),
    ("long_input", "analysis", "以下为合成测试材料，不代表真实论文。" + "研究仅使用模拟数据，不能外推到真实用户。" * 80 + "最终结果：测试编号SN731，平均延迟120毫秒。只返回测试编号与平均延迟。"),
    ("evidence_json", "analysis", '将材料转换成JSON，只含method和limitation两个字符串字段。材料：采用混合检索；未验证跨领域泛化。'),
]


def validate_case(name, text):
    if name in {"query_json", "evidence_json"}:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            data = json.loads(cleaned)
        except ValueError:
            return False
        if name == "query_json":
            return isinstance(data, dict) and isinstance(data.get("queries"), list) and len(data["queries"]) == 2 and all(isinstance(x, str) for x in data["queries"])
        return isinstance(data, dict) and all(isinstance(data.get(k), str) and data[k] for k in ("method", "limitation"))
    if name == "grounded_answer":
        return "80" in text and "S1" in text
    if name == "conflicting_evidence":
        return "S1" in text and "S2" in text
    if name == "long_input":
        return "SN731" in text and "120" in text
    return bool(text.strip())  # Human review is still required for these cases.


def safe_error(exc):
    cause = exc.__cause__ or exc
    status = getattr(cause, "status_code", None)
    return {"error_type": type(cause).__name__, "http_status": status if isinstance(status, int) else None}


def validate_output_path(runtime, output):
    """Never replace an old report or write into the user's runtime."""
    if output.exists() or output.is_symlink():
        raise ValueError("Output already exists; choose a new report file.")
    if output.resolve().is_relative_to(runtime.resolve()):
        raise ValueError("Output must be outside the selected user runtime.")


async def run(args):
    os.environ["RUNTIME_DIR"] = str(args.runtime.resolve())
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.config import get_model_for_task
    from app.services.inference.model_router import _request_options
    from app.services.llm.gateway import LLMGateway

    class ObservedGateway(LLMGateway):
        finish_reason = None

        async def _invoke_text_request(self, call, **kwargs):
            response = await super()._invoke_text_request(call, **kwargs)
            choices = getattr(response, "choices", None)
            if choices:
                self.finish_reason = choices[0].finish_reason
            return response

    logging.disable(logging.CRITICAL)
    profiles = {task: get_model_for_task(task) for _, task, _ in CASES}
    allowed = {"zhipu": "open.bigmodel.cn", "siliconflow": "api.siliconflow.cn"}
    for profile in profiles.values():
        url = urlparse(profile.get("base_url") or "")
        if not profile.get("api_key") or url.scheme != "https" or allowed.get(profile["provider"]) != url.hostname or url.username or url.password or url.port not in {None, 443}:
            raise ValueError("A text task has missing credentials or is not an approved configured cloud endpoint")
    report = {
        "started_at": datetime.now(UTC).isoformat(),
        "scope": "Real cloud API through production gateway; synthetic prompts, not desktop end-to-end or saturation capacity.",
        "limits": {"requests": 12, "max_tokens_each": 128, "concurrency": [1, 2, 3], "retries": 0, "fallback": False, "timeout_seconds": 45},
        "network": {"proxy_env_present": any(os.environ.get(k) for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"))},
        "stages": [], "requests": [],
    }

    def save():
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    async def request(case, semaphore, level):
        name, task, prompt = case
        async with semaphore:
            profile = profiles[task]
            gateway = ObservedGateway.from_profile(profile)
            started = time.perf_counter()
            result = {"case": name, "task": task, "provider": profile["provider"], "model": profile["model"], "concurrency": level}
            try:
                text = await asyncio.wait_for(gateway.chat(
                    messages=[{"role": "user", "content": prompt}], temperature=0,
                    max_tokens=128, _max_retries=0, **_request_options(profile["provider"]),
                ), timeout=45)
                result.update(transport_success=True, content_check=validate_case(name, text), response=text[:1200], finish_reason=gateway.finish_reason)
            except Exception as exc:
                result.update(transport_success=False, content_check=False, **safe_error(exc))
            finally:
                await gateway._discard_openai_client()
                result["elapsed_seconds"] = round(time.perf_counter() - started, 3)
                result["usage"] = gateway.usage
                report["requests"].append(result)
                save()
                print(json.dumps({k: result[k] for k in ("case", "provider", "model", "transport_success", "elapsed_seconds", "usage")}, ensure_ascii=False), flush=True)
            return result

    offset = 0
    for concurrency, count in ((1, 2), (2, 4), (3, 6)):
        started = time.perf_counter()
        semaphore = asyncio.Semaphore(concurrency)
        outcomes = await asyncio.gather(*(request(case, semaphore, concurrency) for case in CASES[offset:offset + count]))
        offset += count
        times = sorted(row["elapsed_seconds"] for row in outcomes)
        report["stages"].append({"concurrency": concurrency, "requests": count, "completed": sum(x["transport_success"] for x in outcomes), "wall_seconds": round(time.perf_counter() - started, 3), "latency_p50_seconds": times[math.ceil(len(times)*.5)-1], "latency_p95_seconds": times[math.ceil(len(times)*.95)-1]})
        save()
        if any(not item["transport_success"] for item in outcomes):
            report["stopped_reason"] = "At least one transport failure; no escalation or automatic retry. Already scheduled calls (including queued calls) in this stage were allowed to finish."
            break
        await asyncio.sleep(2)
    report["finished_at"] = datetime.now(UTC).isoformat()
    report["totals"] = {key: sum(r["usage"].get(key, 0) for r in report["requests"]) for key in ("request_attempts", "responses_received", "usage_reports", "prompt_tokens", "completion_tokens", "total_tokens")}
    report["billing_note"] = "Token counts are provider-reported, not a bill. Missing usage does not mean zero charge. Reasoning is disabled; output length was requested, not independently metered."
    save()
    print(json.dumps({"report": str(args.output), "totals": report["totals"], "stopped_reason": report.get("stopped_reason")}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Explicitly authorize the capped billable requests")
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.live:
        parser.error("No calls made. --live is required for billable probes.")
    if not (args.runtime / "model_config.json").is_file():
        parser.error("Configured runtime not found.")
    try:
        validate_output_path(args.runtime, args.output)
    except ValueError as exc:
        parser.error(str(exc))
    before = hashlib.sha256((args.runtime / "model_config.json").read_bytes()).hexdigest()
    try:
        asyncio.run(run(args))
    finally:
        assert hashlib.sha256((args.runtime / "model_config.json").read_bytes()).hexdigest() == before, "Model configuration was changed during the probe"


if __name__ == "__main__":
    main()
