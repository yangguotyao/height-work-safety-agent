from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, Protocol

from openai import BadRequestError, OpenAI

from ..config import Settings
from ..enums import ApplicabilityStatus, AuditResult, PlanObligation
from ..schemas import LLMDecision

if TYPE_CHECKING:
    from .model_call_monitor import ModelCallMonitor

RESULT_ALIASES = {
    "compliant": AuditResult.COMPLIANT.value,
    "noncompliant": AuditResult.NONCOMPLIANT.value,
    "not_specified": AuditResult.NOT_SPECIFIED.value,
    "not_applicable": AuditResult.NOT_APPLICABLE.value,
    "out_of_scope": AuditResult.OUT_OF_SCOPE.value,
    "needs_human_review": AuditResult.NEEDS_HUMAN_REVIEW.value,
    "uncertain": AuditResult.NEEDS_HUMAN_REVIEW.value,
    "needs_review": AuditResult.NEEDS_HUMAN_REVIEW.value,
    "human_review": AuditResult.NEEDS_HUMAN_REVIEW.value,
    "待确认": AuditResult.NEEDS_HUMAN_REVIEW.value,
    "满足": AuditResult.COMPLIANT.value,
    "通过": AuditResult.COMPLIANT.value,
    "合规": AuditResult.COMPLIANT.value,
    "不满足": AuditResult.NONCOMPLIANT.value,
    "不通过": AuditResult.NONCOMPLIANT.value,
    "不合规": AuditResult.NONCOMPLIANT.value,
    "缺失": AuditResult.NOT_SPECIFIED.value,
    "缺少": AuditResult.NOT_SPECIFIED.value,
    "未提及": AuditResult.NOT_SPECIFIED.value,
}


def _strip_json_wrapper(content: str) -> str:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```json").removeprefix("```")
        cleaned = cleaned.removesuffix("```").strip()
    if not cleaned.startswith("{"):
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start : end + 1]
    return cleaned


def _recover_json_array_objects(content: str, key: str) -> list[dict[str, Any]]:
    """Recover valid top-level objects from one malformed JSON array.

    Compatible providers occasionally append commentary or corrupt one decision in a
    long bundle. A single bad object must not discard the other independently usable
    rule decisions.
    """
    match = re.search(rf'["\']{re.escape(key)}["\']\s*:\s*\[', content)
    if not match:
        return []
    start = match.end()
    entries: list[dict[str, Any]] = []
    object_start: int | None = None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(content)):
        char = content[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{" and depth == 0:
            object_start = index
            depth = 1
        elif char == "{" and depth:
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and object_start is not None:
                candidate = content[object_start : index + 1]
                try:
                    value = json.loads(candidate)
                except json.JSONDecodeError:
                    try:
                        value = json.loads(re.sub(r",\s*([}\]])", r"\1", candidate))
                    except json.JSONDecodeError:
                        value = None
                if isinstance(value, dict):
                    entries.append(value)
                object_start = None
        elif char == "]" and depth == 0:
            break
    return entries


def normalize_decision_payload(parsed: dict, rule: dict) -> dict:
    result = parsed.get("result", AuditResult.NEEDS_HUMAN_REVIEW.value)
    result = RESULT_ALIASES.get(str(result).strip().lower(), result)
    if result not in {item.value for item in AuditResult}:
        result = AuditResult.NEEDS_HUMAN_REVIEW.value
    if parsed.get("issue"):
        issue = str(parsed["issue"]).strip()
    elif result == AuditResult.NOT_SPECIFIED.value:
        issue = f"方案未说明以下要求：{rule.get('requirement') or '本条安全控制要求'}"
    elif result == AuditResult.COMPLIANT.value:
        issue = "方案已提供满足本条要求的原文证据。"
    elif result == AuditResult.NONCOMPLIANT.value:
        issue = "方案现有表述与本条安全要求存在冲突。"
    else:
        issue = "现有方案证据不足以形成确定审计结论。"
    risk_consequence = str(
        parsed.get("risk_consequence")
        or rule.get("hazards")
        or "措施缺失或执行不到位可能增加高处坠落或物体打击风险。"
    ).strip()
    if parsed.get("suggestion"):
        suggestion = str(parsed["suggestion"]).strip()
    elif result == AuditResult.COMPLIANT.value:
        suggestion = "保持现有措施，并确保方案、交底与现场执行一致。"
    else:
        suggestion = "请专业审查人员结合所引条款补充或修订相关措施。"
    selected_ids = parsed.get("selected_plan_segment_ids", parsed.get("plan_segment_ids", []))
    if not isinstance(selected_ids, list):
        selected_ids = []
    try:
        confidence = float(parsed.get("confidence", 0.3))
    except (TypeError, ValueError):
        confidence = 0.3
    if 1 < confidence <= 100:
        confidence /= 100
    confidence = max(0.0, min(confidence, 1.0))
    applicability_status = str(
        parsed.get("applicability_status", ApplicabilityStatus.UNCERTAIN.value)
    ).strip().lower()
    if applicability_status not in {item.value for item in ApplicabilityStatus}:
        applicability_status = ApplicabilityStatus.UNCERTAIN.value
    if result == AuditResult.NOT_APPLICABLE.value:
        applicability_status = ApplicabilityStatus.NOT_APPLICABLE.value
    elif applicability_status == ApplicabilityStatus.NOT_APPLICABLE.value:
        result = AuditResult.NOT_APPLICABLE.value
    plan_obligation = str(
        parsed.get(
            "plan_obligation",
            rule.get("_plan_scope", {}).get(
                "obligation", PlanObligation.CONDITIONAL_MUST_STATE.value
            ),
        )
    ).strip().lower()
    if plan_obligation not in {item.value for item in PlanObligation}:
        plan_obligation = PlanObligation.CONDITIONAL_MUST_STATE.value
    deterministic_scope = rule.get("_plan_scope", {}).get("obligation")
    if deterministic_scope in {
        PlanObligation.MUST_STATE.value,
        PlanObligation.EXTERNAL_ONLY.value,
    }:
        plan_obligation = deterministic_scope
    unique_selected_ids = list(
        dict.fromkeys(str(value).strip() for value in selected_ids if str(value).strip())
    )[:3]
    decision_basis = str(parsed.get("decision_basis") or "text").strip().lower()
    if decision_basis not in {"text", "numeric", "missing", "scope"}:
        decision_basis = "text"
    numeric_comparison = parsed.get("numeric_comparison")
    required_numeric = {
        "object",
        "plan_value",
        "plan_unit",
        "operator",
        "standard_value",
        "standard_unit",
        "plan_segment_id",
        "standard_source_id",
    }
    if not isinstance(numeric_comparison, dict) or not required_numeric.issubset(
        numeric_comparison
    ):
        numeric_comparison = None
    else:
        try:
            numeric_comparison = {
                "object": str(numeric_comparison["object"]).strip(),
                "plan_value": float(numeric_comparison["plan_value"]),
                "plan_unit": str(numeric_comparison["plan_unit"]).strip(),
                "operator": str(numeric_comparison["operator"]).strip(),
                "standard_value": float(numeric_comparison["standard_value"]),
                "standard_unit": str(numeric_comparison["standard_unit"]).strip(),
                "plan_segment_id": str(numeric_comparison["plan_segment_id"]).strip(),
                "standard_source_id": str(
                    numeric_comparison["standard_source_id"]
                ).strip(),
            }
            if numeric_comparison["operator"] not in {">=", "<=", ">", "<", "=="}:
                numeric_comparison = None
        except (TypeError, ValueError):
            numeric_comparison = None
    requirement_checks = []
    for check in parsed.get("requirement_checks", []):
        if not isinstance(check, dict):
            continue
        try:
            item_index = int(check.get("item_index"))
        except (TypeError, ValueError):
            continue
        status = str(check.get("status") or "uncertain").strip().lower()
        if status not in {"satisfied", "conflicting", "missing", "not_applicable", "uncertain"}:
            status = "uncertain"
        evidence_ids = check.get("evidence_ids", [])
        if not isinstance(evidence_ids, list):
            evidence_ids = []
        requirement_checks.append(
            {
                "item_index": item_index,
                "status": status,
                "evidence_ids": list(
                    dict.fromkeys(str(value) for value in evidence_ids if str(value).strip())
                )[:3],
            }
        )
    return {
        "result": result,
        "applicability_status": applicability_status,
        "applicability_reason": str(
            parsed.get("applicability_reason")
            or "模型未提供充分的规则适用性说明。"
        ).strip(),
        "plan_obligation": plan_obligation,
        "plan_obligation_reason": str(
            parsed.get("plan_obligation_reason")
            or rule.get("_plan_scope", {}).get("reason")
            or "未能确认该内容是否必须在施工方案中说明。"
        ).strip(),
        "issue": issue,
        "risk_consequence": risk_consequence,
        "suggestion": suggestion,
        "selected_plan_segment_ids": unique_selected_ids,
        "decision_basis": decision_basis,
        "numeric_comparison": numeric_comparison,
        "requirement_checks": requirement_checks,
        "confidence": confidence,
    }


def requirement_items(rule: dict[str, Any]) -> list[str]:
    """Expose compound requirements as a checklist without changing rule identity."""
    requirement = str(rule.get("requirement") or "").strip()
    if not requirement:
        return []
    parts = [
        part.strip(" ；;，,")
        for part in re.split(r"[；;。]|(?:，|,)(?=并|且|严禁|不得|应)", requirement)
        if part.strip(" ；;，,")
    ]
    return list(dict.fromkeys(parts))[:8]


def _specific_variant_supported(rule: dict[str, Any], evidence: list[dict]) -> bool:
    rule_text = " ".join(
        str(rule.get(field, ""))
        for field in ("trigger_condition", "requirement", "original_text")
    )
    plan_text = " ".join(str(item.get("text", "")) for item in evidence)
    specific_variants = (
        (("固定式直梯", "直梯"), ("固定式直梯", "直梯")),
        (("折梯", "人字梯"), ("折梯", "人字梯")),
        (("单梯",), ("单梯",)),
        (("梯道", "斜道"), ("梯道", "斜道")),
        (("附着式升降脚手架", "升降脚手架", "爬架"), ("附着式升降脚手架", "升降脚手架", "爬架")),
        (("钢结构", "钢梁"), ("钢结构", "钢梁")),
        (("预制构件", "屋架", "大型构件", "大中型构件"), ("预制构件", "屋架", "大型构件", "大中型构件")),
        (("钢筋绑扎", "钢筋骨架"), ("钢筋绑扎", "钢筋骨架")),
        (("混凝土浇筑",), ("混凝土浇筑", "浇筑混凝土")),
    )
    for rule_terms, evidence_terms in specific_variants:
        if any(term in rule_text for term in rule_terms) and not any(
            term in plan_text for term in evidence_terms
        ):
            return False
    return True


class AuditModel(Protocol):
    provider_name: str
    model_name: str | None

    def identify_scene_instances(
        self,
        context: str,
        available_scenes: list[str],
        keyword_hints: list[dict],
    ) -> list[dict]: ...

    def select_applicable_controls(
        self,
        scene_instance: dict,
        controls: list[dict],
        evidence: list[dict],
        standard_candidates: list[dict] | None = None,
    ) -> dict[str, dict]: ...

    def judge_rule(
        self, rule: dict, evidence: list[dict], standard_evidence: list[dict]
    ) -> tuple[LLMDecision, dict]: ...

    def judge_rule_bundle(
        self,
        rules: list[dict],
        evidence: list[dict],
        standard_evidence: dict[str, list[dict]],
    ) -> tuple[dict[str, LLMDecision], dict]: ...

    def resolve_pending_bundle(
        self,
        rules: list[dict],
        evidence: list[dict],
        previous_decisions: dict[str, LLMDecision],
    ) -> tuple[dict[str, LLMDecision], dict]: ...


class MockAuditModel:
    provider_name = "mock"
    model_name = "conservative-mock"

    def identify_scene_instances(
        self,
        context: str,
        available_scenes: list[str],
        keyword_hints: list[dict],
    ) -> list[dict]:
        return []

    def select_applicable_controls(
        self,
        scene_instance: dict,
        controls: list[dict],
        evidence: list[dict],
        standard_candidates: list[dict] | None = None,
    ) -> dict[str, dict]:
        return {
            control["key"]: {
                "status": "applicable",
                "reason": "本地保守模式不缩减业务控制项。",
                "applicable_rule_ids": [rule["rule_id"] for rule in control["rules"]],
                "evidence_ids": [item["id"] for item in control["evidence"][:3]],
            }
            for control in controls
        }

    def judge_rule(
        self, rule: dict, evidence: list[dict], standard_evidence: list[dict]
    ) -> tuple[LLMDecision, dict]:
        result = AuditResult.NEEDS_HUMAN_REVIEW
        issue = "mock 模式不判断具体触发条件和方案说明义务，本条保留为待人工确认。"
        suggestion = "请专业审查人员核对该规则是否适用、是否属于方案必须说明内容及原文是否充分。"
        selected_ids = [evidence[0]["id"]] if evidence else []
        confidence = 0.35
        scope = rule.get("_plan_scope", {})
        decision = LLMDecision(
            result=result,
            applicability_status=ApplicabilityStatus.UNCERTAIN,
            applicability_reason=(
                "mock 模式仅确认已识别对应场景，不对规则中的具体触发条件作语义强判。"
            ),
            plan_obligation=PlanObligation(
                scope.get("obligation", PlanObligation.CONDITIONAL_MUST_STATE.value)
            ),
            plan_obligation_reason=scope.get(
                "reason", "mock 模式不对方案说明义务作语义强判。"
            ),
            issue=issue,
            risk_consequence=rule["hazards"] or "可能增加高处坠落或物体打击风险。",
            suggestion=suggestion,
            selected_plan_segment_ids=selected_ids,
            confidence=confidence,
        )
        return decision, {"provider": "mock", "reason": "development fallback"}

    def judge_rule_bundle(
        self,
        rules: list[dict],
        evidence: list[dict],
        standard_evidence: dict[str, list[dict]],
    ) -> tuple[dict[str, LLMDecision], dict]:
        decisions = {
            rule["rule_id"]: self.judge_rule(
                rule,
                rule.get("_plan_evidence", evidence),
                standard_evidence.get(rule["rule_id"], []),
            )[0]
            for rule in rules
        }
        return decisions, {
            "provider": "mock",
            "reason": "one conservative bundle call",
            "rule_count": len(rules),
        }

    def resolve_pending_bundle(
        self,
        rules: list[dict],
        evidence: list[dict],
        previous_decisions: dict[str, LLMDecision],
    ) -> tuple[dict[str, LLMDecision], dict]:
        return previous_decisions, {
            "provider": "mock",
            "reason": "mock mode does not perform a second semantic judgment",
        }


class OpenAICompatibleAuditModel:
    provider_name = "openai"

    def __init__(
        self,
        settings: Settings,
        call_monitor: ModelCallMonitor | None = None,
        run_id: str | None = None,
    ):
        if not settings.model_api_key:
            raise ValueError("MODEL_PROVIDER=openai 时必须设置 MODEL_API_KEY")
        if not settings.model_name:
            raise ValueError("MODEL_PROVIDER=openai 时必须设置 MODEL_NAME")
        client_args = {
            "api_key": settings.model_api_key,
            "timeout": settings.model_timeout_seconds,
            "max_retries": 0,
        }
        if settings.model_base_url:
            client_args["base_url"] = settings.model_base_url
        self.client = OpenAI(**client_args)
        self.model_name = settings.model_name
        self.call_monitor = call_monitor
        self.run_id = run_id
        if "dashscope.aliyuncs.com" in (settings.model_base_url or "").lower():
            self._dashscope_thinking = {
                "enable_thinking": settings.model_enable_thinking
            }
            self._dashscope_thinking_budgets = {
                "scene_identification": settings.model_routing_thinking_budget,
                "control_applicability": settings.model_routing_thinking_budget,
                "rule_bundle_audit": settings.model_audit_thinking_budget,
                "pending_rule_resolution": settings.model_resolution_thinking_budget,
            }
            self._dashscope_default_thinking_budget = settings.model_thinking_budget
        else:
            self._dashscope_thinking = None
            self._dashscope_thinking_budgets = {}
            self._dashscope_default_thinking_budget = None
        if "api.deepseek.com" in (settings.model_base_url or "").lower():
            self._deepseek_options = {
                "thinking": {
                    "type": (
                        "enabled" if settings.model_deepseek_enable_thinking else "disabled"
                    )
                }
            }
        else:
            self._deepseek_options = None
        self._max_output_tokens = {
            "scene_identification": settings.model_routing_max_tokens,
            "control_applicability": settings.model_routing_max_tokens,
            "rule_bundle_audit": settings.model_audit_max_tokens,
            "pending_rule_resolution": settings.model_resolution_max_tokens,
        }

    def _dashscope_options(self, purpose: str) -> dict[str, Any] | None:
        if self._dashscope_thinking is None:
            return None
        options = dict(self._dashscope_thinking)
        if options["enable_thinking"]:
            budget = self._dashscope_thinking_budgets.get(
                purpose, self._dashscope_default_thinking_budget
            )
            if budget:
                options["thinking_budget"] = budget
        return options

    def _provider_extra_body(self, purpose: str) -> dict[str, Any] | None:
        dashscope = self._dashscope_options(purpose)
        if dashscope is not None:
            return dashscope
        return getattr(self, "_deepseek_options", None)

    def _max_tokens_for(self, purpose: str) -> int | None:
        return getattr(self, "_max_output_tokens", {}).get(purpose)

    def _request_with_monitor(
        self,
        request: dict[str, Any],
        *,
        purpose: str,
        metadata: dict[str, Any] | None,
        attempt: int,
        response_format: bool,
    ):
        call_state = None
        if self.call_monitor is not None and self.run_id is not None:
            recorded_request = {
                **request,
                "response_format": {"type": "json_object"} if response_format else None,
            }
            call_state = self.call_monitor.start(
                run_id=self.run_id,
                purpose=purpose,
                attempt=attempt,
                provider=self.provider_name,
                model_name=self.model_name,
                request=recorded_request,
                metadata=metadata,
            )
        try:
            if response_format:
                response = self.client.chat.completions.create(
                    **request, response_format={"type": "json_object"}
                )
            else:
                response = self.client.chat.completions.create(**request)
        except Exception as exc:
            if call_state is not None:
                self.call_monitor.fail(call_state[0], call_state[1], exc)
            raise
        content = response.choices[0].message.content or "{}"
        raw = {
            "id": response.id,
            "model": response.model,
            "content": content,
            "finish_reason": response.choices[0].finish_reason,
            "usage": response.usage.model_dump() if response.usage else None,
        }
        if call_state is not None:
            self.call_monitor.http_completed(call_state[0], call_state[1], raw)
            raw["_monitor_call_id"] = call_state[0]
        return response, raw

    def _json_completion(
        self,
        system_prompt: str,
        payload: dict,
        *,
        purpose: str,
        metadata: dict[str, Any] | None = None,
        recover_array_key: str | None = None,
        retry_empty_array: bool = False,
    ) -> tuple[dict, dict]:
        request = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "temperature": 0,
        }
        provider_options = self._provider_extra_body(purpose)
        if provider_options is not None:
            request["extra_body"] = provider_options
        max_tokens = self._max_tokens_for(purpose)
        if max_tokens is not None:
            request["max_tokens"] = max_tokens
        request_attempt = 1
        try:
            response, raw = self._request_with_monitor(
                request,
                purpose=purpose,
                metadata=metadata,
                attempt=1,
                response_format=True,
            )
        except BadRequestError:
            request_attempt = 2
            response, raw = self._request_with_monitor(
                request,
                purpose=purpose,
                metadata=metadata,
                attempt=2,
                response_format=False,
            )
        content = response.choices[0].message.content or "{}"
        cleaned = _strip_json_wrapper(content)
        call_id = raw.get("_monitor_call_id")
        if raw.get("finish_reason") == "length":
            error = ValueError("模型输出达到 max_tokens 上限，拒绝使用可能截断的结论。")
            if call_id and self.call_monitor is not None:
                self.call_monitor.processing_failed(call_id, "parse", error)
            raise error
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            recovered = (
                _recover_json_array_objects(content, recover_array_key)
                if recover_array_key
                else []
            )
            if recovered:
                parsed = {
                    recover_array_key: recovered,
                    "_partial_parse_error": str(exc),
                }
                if call_id and self.call_monitor is not None:
                    self.call_monitor.mark_parse(call_id, partial=True)
            elif retry_empty_array and recover_array_key:
                if call_id and self.call_monitor is not None:
                    self.call_monitor.processing_failed(call_id, "parse", exc)
                retry_response, retry_raw = self._request_with_monitor(
                    request,
                    purpose=purpose,
                    metadata=metadata,
                    attempt=request_attempt + 1,
                    response_format=True,
                )
                retry_content = retry_response.choices[0].message.content or "{}"
                parsed = json.loads(_strip_json_wrapper(retry_content))
                raw = retry_raw
                call_id = raw.get("_monitor_call_id")
                if call_id and self.call_monitor is not None:
                    self.call_monitor.mark_parse(call_id)
            else:
                if call_id and self.call_monitor is not None:
                    self.call_monitor.processing_failed(call_id, "parse", exc)
                raise
        else:
            required_entries = payload.get("rules") or payload.get("business_controls")
            should_retry_empty = bool(
                retry_empty_array
                and recover_array_key
                and isinstance(required_entries, list)
                and required_entries
                and not parsed.get(recover_array_key)
            )
            if should_retry_empty:
                if call_id and self.call_monitor is not None:
                    self.call_monitor.processing_failed(
                        call_id,
                        "validation",
                        ValueError(f"模型返回空的 {recover_array_key} 数组"),
                    )
                try:
                    retry_response, retry_raw = self._request_with_monitor(
                        request,
                        purpose=purpose,
                        metadata=metadata,
                        attempt=request_attempt + 1,
                        response_format=True,
                    )
                    retry_content = retry_response.choices[0].message.content or "{}"
                    retry_parsed = json.loads(_strip_json_wrapper(retry_content))
                    if retry_parsed.get(recover_array_key):
                        parsed, raw = retry_parsed, retry_raw
                        call_id = raw.get("_monitor_call_id")
                except Exception:
                    # Preserve the first traceable response. The caller will convert
                    # any still-missing rule IDs to a non-user-facing uncertain item.
                    pass
            if call_id and self.call_monitor is not None:
                self.call_monitor.mark_parse(call_id)
        return parsed, raw

    def identify_scene_instances(
        self,
        context: str,
        available_scenes: list[str],
        keyword_hints: list[dict],
    ) -> list[dict]:
        parsed, raw = self._json_completion(
            "你是施工方案高处作业场景实例识别器。场景实例是方案中实际存在的一项施工内容，"
            "既可能出现在标题中，也可能只在正文、表格或一条措施中出现。当前输入是长文档的一个"
            "连续批次；请逐段检查并返回本批次内全部实际施工内容，不得只看标题。一次明确描述即可"
            "形成实例，但不得把编制依据、材料或劳动力计划、计算书、验算公式、允许偏差表、监测"
            "预警值或验收记录本身当作新的施工实例；这些内容只能作为已识别施工实例的辅助证据。"
            "不得因只有规范名称、目录词或通用安全口号而臆造场景。只能使用受控场景"
            "标签，并且每个实例必须引用输入中真实存在、能够证明该施工内容的segment_id。"
            "keyword_recall_hints只是关键词程序提供的阅读提示，可能误报，也可能漏报；它们不能"
            "直接证明场景存在，不得照抄。你必须用document_evidence独立确认最终场景；即使没有"
            "关键词提示，只要原文明确描述实际施工内容，也必须识别。"
            "受控标签允许重叠，并非互斥分类：原文明示系挂安全带、安全绳或生命绳时，应独立返回"
            "安全带使用；原文明示高处作业的人员条件、交底、检查、天气、警戒或过程管理时，应返回"
            "一个高处作业综合管理实例；不能因为同一段已经归入悬空作业或个体防护而省略这些标签。"
            "高处作业综合管理在一个文档中最多返回一个实例，并引用最有代表性的管理段落。"
            "必须区分脚手架用途：模板支撑体系、支模架、满堂支撑架用于承受模板及混凝土荷载，"
            "不等于供人员施工的作业脚手架；不能仅因支模文字使用‘脚手架’一词，就推断存在"
            "作业脚手架、作业层、连墙件或外脚手架。"
            "相同场景发生在不同部位、工序或章节时应分别返回。返回JSON："
            '{"instances":[{"scene":"受控标签","title":"方案中的场景标题",'
            '"segment_ids":["原文ID"]}]}。',
            {
                "available_scenes": available_scenes,
                "keyword_recall_hints": [
                    {
                        "scene": item["scene"],
                        "title": item["title"],
                        "segment_ids": item["segment_ids"][:5],
                    }
                    for item in keyword_hints
                ],
                "document_evidence": context,
            },
            purpose="scene_identification",
            metadata={"segment_count": context.count("<!-- segment:")},
        )
        instances = parsed.get("instances", [])
        result = [item for item in instances if isinstance(item, dict)]
        call_id = raw.get("_monitor_call_id")
        if call_id and self.call_monitor is not None:
            self.call_monitor.mark_validation(call_id)
        return result

    def select_applicable_controls(
        self,
        scene_instance: dict,
        controls: list[dict],
        evidence: list[dict],
        standard_candidates: list[dict] | None = None,
    ) -> dict[str, dict]:
        standard_candidates = standard_candidates or []
        parsed, raw = self._json_completion(
            "你是施工方案业务控制项适用性路由器。当前施工场景实例已经由方案原文确认存在。"
            "你的任务不是审查安全措施是否写全，而是判断每个业务控制项及其下属规则的施工对象、"
            "设备类型、作业方式、部位、天气或尺寸等前提是否确实适用于本方案。"
            "严禁因为方案没有写某项安全措施，就把该控制项判为不适用；例如已确认脚手架搭设，"
            "搭设警戒和验收属于核心控制，即使方案没写也仍适用。只有额外条件有明确相斥证据时"
            "才判not_applicable；证据不足时判uncertain。对于applicable控制项，必须返回其中"
            "实际适用的rule_id：场景通用规则应保留，带有特定梯型、平台类型、天气、材料、尺寸"
            "或施工方法前提的规则，只有该前提已由方案证据确认时才保留。不得引用输入之外的"
            "segment_id。independent_standard_candidates是仅由场景原文向量召回的规范提示，"
            "可用于交叉验证规则映射，但它不能代替方案场景或触发条件证据，也不能仅凭规范中"
            "存在某要求就把控制项判为适用。"
            "返回JSON：{\"controls\":[{\"control_key\":\"原键\"," 
            "\"status\":\"applicable|not_applicable|uncertain\",\"reason\":\"理由\","
            "\"applicable_rule_ids\":[\"规则ID\"],\"evidence_ids\":[\"片段ID\"]}]}。",
            {
                "scene_instance": {
                    "scene": scene_instance["scene"],
                    "title": scene_instance["title"],
                    "location": scene_instance["location"],
                    "object_type": scene_instance.get("object_type", "general"),
                },
                "business_controls": [
                    {
                        "control_key": control["key"],
                        "title": control["title"],
                        "rules": [
                            {
                                "rule_id": rule["rule_id"],
                                "trigger_condition": rule["trigger_condition"],
                                "requirement_summary": rule["requirement"][:300],
                                "trigger_evidence_ids": control.get(
                                    "rule_evidence_ids", {}
                                ).get(str(rule["rule_id"]), []),
                            }
                            for rule in control["rules"]
                        ],
                    }
                    for control in controls
                ],
                "whole_document_evidence": [
                    {
                        "segment_id": item["id"],
                        "location": item["location"],
                        "quote": item["text"][:900],
                    }
                    for item in evidence
                ],
                "independent_standard_candidates": [
                    {
                        "chunk_id": item["id"],
                        "standard_code": item.get("standard_code", ""),
                        "clause": item.get("clause", ""),
                        "page": item.get("page_start", 0),
                        "quote": str(item.get("text", ""))[:900],
                        "score": item.get("score", 0),
                        "matched_rule_ids": item.get("matched_rule_ids", []),
                    }
                    for item in standard_candidates[:6]
                ],
            },
            purpose="control_applicability",
            metadata={
                "scene": scene_instance["scene"],
                "control_keys": [control["key"] for control in controls],
                "rule_count": sum(len(control["rules"]) for control in controls),
            },
            recover_array_key="controls",
            retry_empty_array=True,
        )
        control_by_key = {control["key"]: control for control in controls}
        decisions: dict[str, dict] = {}
        for entry in parsed.get("controls", []):
            if not isinstance(entry, dict):
                continue
            key = str(entry.get("control_key") or "")
            control = control_by_key.get(key)
            if control is None or key in decisions:
                continue
            status = str(entry.get("status") or "uncertain").strip().lower()
            if status not in {"applicable", "not_applicable", "uncertain"}:
                status = "uncertain"
            valid_rule_ids = {str(rule["rule_id"]) for rule in control["rules"]}
            valid_evidence_ids = {str(item["id"]) for item in evidence}
            selected_rules = list(
                dict.fromkeys(
                    str(value)
                    for value in entry.get("applicable_rule_ids", [])
                    if str(value) in valid_rule_ids
                )
            )
            selected_rules = [
                rule_id
                for rule_id in selected_rules
                if _specific_variant_supported(
                    next(rule for rule in control["rules"] if str(rule["rule_id"]) == rule_id),
                    evidence,
                )
            ]
            evidence_ids = list(
                dict.fromkeys(
                    str(value)
                    for value in entry.get("evidence_ids", [])
                    if str(value) in valid_evidence_ids
                )
            )[:4]
            if status != "applicable":
                selected_rules = []
            decisions[key] = {
                "status": status,
                "reason": str(entry.get("reason") or "模型未提供适用性理由.")[:1000],
                "applicable_rule_ids": selected_rules,
                "evidence_ids": evidence_ids,
            }
        for control in controls:
            decisions.setdefault(
                control["key"],
                {
                    "status": "uncertain",
                    "reason": "模型未返回该业务控制项，未展开原子规则。",
                    "applicable_rule_ids": [],
                    "evidence_ids": [],
                },
            )
        call_id = raw.get("_monitor_call_id")
        if call_id and self.call_monitor is not None:
            incomplete = any(
                value["status"] == "applicable" and not value["applicable_rule_ids"]
                for value in decisions.values()
            )
            self.call_monitor.mark_validation(call_id, partial=incomplete)
        return decisions

    def judge_rule(
        self, rule: dict, evidence: list[dict], standard_evidence: list[dict]
    ) -> tuple[LLMDecision, dict]:
        parsed, raw = self._json_completion(
            "你是建筑施工高处作业方案审查助手。只依据输入的方案原文和规范规则判断。"
            "第一步先判断规则适用性：applicable=方案明确涉及触发对象或条件；"
            "not_applicable=方案原文明示了与规则触发条件相斥的对象、类型或参数；"
            "uncertain=证据不足以确定。不得仅因方案未提到某对象就判定不适用。"
            "符合=方案有明确且完整的满足证据；不符合=方案有明确冲突；未说明=方案缺少必要内容；"
            "不适用=仅限已有方案证据明确证明规则条件不成立；"
            "待人工确认=对象对应、条件或证据存在歧义。不得把未说明写成不符合，不得引用输入之外的内容。"
            "若判不适用，必须引用证明触发条件不成立的方案segment_id。"
            "返回字段：result、applicability_status、applicability_reason、issue、risk_consequence、"
            "suggestion、selected_plan_segment_ids、confidence。",
            {
                "rule": {
                    "rule_id": rule["rule_id"],
                    "scene": rule["scene"],
                    "process": rule["process"],
                    "trigger_condition": rule["trigger_condition"],
                    "requirement": rule["requirement"],
                    "threshold": rule["threshold"],
                    "rule_effect": rule["rule_effect"],
                    "hazards": rule["hazards"],
                    "standard_name": rule["standard_name"],
                    "standard_code": rule["standard_code"],
                    "clause": rule["clause"],
                    "original_text": rule["original_text"],
                },
                "scene_instances": [
                    {
                        "instance_id": item["id"],
                        "title": item["title"],
                        "location": item["location"],
                    }
                    for item in rule.get("_scene_instances", [])
                ],
                "deterministic_applicability_prefilter": rule.get("_applicability"),
                "plan_evidence": [
                    {
                        "segment_id": item["id"],
                        "location": item["location"],
                        "quote": item["text"],
                    }
                    for item in evidence
                ],
                "standard_rag_evidence": [
                    {
                        "chunk_id": item["id"],
                        "standard_code": item["standard_code"],
                        "clause": item["clause"],
                        "page": item["page_start"],
                        "quote": item["text"],
                        "retrieval_type": item["retrieval_type"],
                        "independent_scene_match": bool(
                            item.get("independent_scene_match", False)
                        ),
                    }
                    for item in standard_evidence
                ],
            },
            purpose="single_rule_audit",
            metadata={"rule_ids": [rule["rule_id"]]},
        )
        try:
            decision = LLMDecision.model_validate(normalize_decision_payload(parsed, rule))
        except Exception as exc:
            call_id = raw.get("_monitor_call_id")
            if call_id and self.call_monitor is not None:
                self.call_monitor.processing_failed(call_id, "validation", exc)
            raise
        call_id = raw.get("_monitor_call_id")
        if call_id and self.call_monitor is not None:
            self.call_monitor.mark_validation(call_id)
        return decision, raw

    def judge_rule_bundle(
        self,
        rules: list[dict],
        evidence: list[dict],
        standard_evidence: dict[str, list[dict]],
    ) -> tuple[dict[str, LLMDecision], dict]:
        clause_sources: dict[tuple[str, str], dict] = {}
        for rule in rules:
            key = (rule["standard_code"], rule["clause"])
            clause_sources.setdefault(
                key,
                {
                    "standard_code": rule["standard_code"],
                    "clause": rule["clause"],
                    "quote": rule["original_text"][:1800],
                },
            )
        rag_sources: dict[str, dict] = {}
        for entries in standard_evidence.values():
            for item in entries:
                rag_sources.setdefault(
                    item["id"],
                    {
                        "chunk_id": item["id"],
                        "standard_code": item["standard_code"],
                        "clause": item["clause"],
                        "page": item["page_start"],
                        "quote": item["text"][:1200],
                        "retrieval_type": item.get("retrieval_type", "unknown"),
                        "independent_scene_match": bool(
                            item.get("independent_scene_match", False)
                        ),
                    },
                )
        required_rag_source_ids = list(
            dict.fromkeys(
                str(item["id"])
                for rule in rules
                for item in standard_evidence.get(str(rule["rule_id"]), [])[:2]
            )
        )
        ordered_rag_sources = [
            rag_sources[source_id]
            for source_id in required_rag_source_ids
            if source_id in rag_sources
        ]
        included_rag_ids = {str(item["chunk_id"]) for item in ordered_rag_sources}
        for source_id, item in rag_sources.items():
            if source_id in included_rag_ids:
                continue
            ordered_rag_sources.append(item)
            included_rag_ids.add(source_id)
            if len(ordered_rag_sources) >= max(8, len(required_rag_source_ids)):
                break
        plan_sources: dict[str, dict] = {}
        for item in evidence:
            plan_sources.setdefault(
                str(item["id"]),
                {
                    "segment_id": item["id"],
                    "location": item["location"],
                    "quote": item["text"][:900],
                },
            )
        for rule in rules:
            for item in rule.get("_plan_evidence", []):
                plan_sources.setdefault(
                    str(item["id"]),
                    {
                        "segment_id": item["id"],
                        "location": item["location"],
                        "quote": item["text"][:900],
                    },
                )
        required_plan_source_ids = list(
            dict.fromkeys(
                str(item["id"])
                for rule in rules
                for item in [
                    *rule.get("_plan_evidence", [])[:6],
                    *rule.get("_global_counter_evidence", [])[:4],
                ]
            )
        )
        ordered_plan_sources = [
            plan_sources[source_id]
            for source_id in required_plan_source_ids
            if source_id in plan_sources
        ]
        included_source_ids = {
            str(item["segment_id"]) for item in ordered_plan_sources
        }
        for source_id, item in plan_sources.items():
            if source_id in included_source_ids:
                continue
            ordered_plan_sources.append(item)
            included_source_ids.add(source_id)
            if len(ordered_plan_sources) >= max(20, len(required_plan_source_ids)):
                break

        parsed, raw = self._json_completion(
            "你是建筑施工高处作业施工方案审查助手。本次输入包含同一业务控制项下、"
            "由相同或相邻方案原文命中的多条原子规则。你必须逐条返回判断，但只进行一次整体分析。"
            "输入的scene_instance已经由方案原文确认存在。先判断 applicability_status：如果规则"
            "触发条件只是重述该场景或该场景的常规安全控制，直接判为applicable，不得要求方案先写出"
            "本应检查的安全措施来证明适用性；只有规则另有特定设备类型、尺寸区间、天气或施工方式"
            "等附加条件且证据不足时才是uncertain；有明确相斥证据才是not_applicable。"
            "再判断 plan_obligation：must_state=本施工方案必须明确；"
            "conditional_must_state=仅在已确认"
            "条件成立时必须明确；reference_only=可以引用但缺少细节不构成方案缺项；external_only="
            "实际产品合格证、检测结果、网体重量或已经形成的现场记录等方案外资料。方案是否规定"
            "检查、验收并形成记录，仍属于方案可以要求说明的管理流程。"
            "只有 applicability_status=applicable 且 plan_obligation 为 must_state 或"
            "conditional_must_state，才能把缺少必要正文判为未说明。条件不确定必须待人工确认，"
            "不得因为规范中存在要求就推断现场一定涉及。方案明确冲突才判不符合。"
            "必须遵守以下等价与对象边界：方案只写‘系安全带’不能证明已说明安全带培训、可靠挂点"
            "或连续保护，缺少相应方案义务应判未说明而不是不符合或待人工确认；方案已经要求设置防护栏杆时，栏杆组成、挡脚板和封闭等核心构造规则即为"
            "applicable，缺少且属于方案义务时判未说明；规则要求2m及以上柱模板拆装设置平台，而"
            "方案写6m以上梁、柱、墙模板才设置平台时，柱模板对象已对应，应做6>=2的结构化数值"
            "比较并判不符合；该示例的numeric_comparison必须是plan_value=6、operator='<='、"
            "standard_value=2，因为方案开始采取措施的高度阈值不得高于规范阈值；‘用钢钎撬动模板’"
            "不等于‘强行撬脚手架杆件’，不得跨对象判不符合；"
            "规范要求混凝土浇筑等加载过程中架体下严禁有人，而方案只禁止‘无关人员’进入支模"
            "底下时，相关人员仍被允许进入，属于明确不符合；"
            "方案已有自上而下、逐层或等效的拆除顺序时，不得仅因没有逐字复述规范而判缺项。"
            "standard_rag_evidence中exact_clause表示规则绑定的权威条款，scene_vector表示仅由"
            "方案场景原文独立召回的补充条款。scene_vector只用于交叉验证和补充上下文；不得把"
            "与当前rule_id对象、阶段或触发条件不一致的向量条款用于制造问题。"
            "‘水平兜网随作业层上升’只能证明该兜网的移动安排，不能证明安全网、防护栏杆等全部"
            "防护设施均与脚手架架体同步安装到位；审查同步安装规则时必须区分。"
            "每条决定必须保留输入 rule_id；不得引用输入之外的 segment_id；直接证据最多选择3个"
            "且不得重复。issue只写最终审查事实，保持简洁，不得输出分析过程、自我修正、"
            "星号注释或多种互相矛盾的备选结论。若结论依赖数值比较，decision_basis必须为numeric，"
            "并返回numeric_comparison={object,plan_value,plan_unit,operator,standard_value,"
            "standard_unit,plan_segment_id,standard_source_id}。operator表达方案值满足规范时应成立的"
            "关系；standard_source_id只能使用该规则的rule_id或RAG的chunk_id。在判定未说明前，"
            "必须通过whole_document_counter_evidence_ids检查plan_evidence中的对应全文证据；这些"
            "证据可能位于其他章节或后续段落，不得"
            "因为场景分批或章节不同而忽略。其他判断分别使用"
            "decision_basis=text、missing或scope，numeric_comparison返回null。规则包含多个"
            "requirement_items时必须逐项核对；只满足限载牌等其中一项，不得把整条规则判为符合，"
            "其余属于本方案应说明的事项缺少时应判未说明。每条规则返回requirement_checks，"
            "逐项给出{item_index,status=satisfied|conflicting|missing|not_applicable|uncertain,"
            "evidence_ids}；item_index从0开始。"
            "返回JSON：{\"decisions\":[{rule_id,result,applicability_status,applicability_reason,"
            "plan_obligation,plan_obligation_reason,issue,risk_consequence,suggestion,"
            "selected_plan_segment_ids,decision_basis,numeric_comparison,requirement_checks,"
            "confidence}]}。",
            {
                "business_control": {
                    "scene": rules[0]["scene"],
                    "title": rules[0].get("_control_title", rules[0]["process"]),
                    "scene_instance": {
                        "instance_id": rules[0]["_scene_instance"]["id"],
                        "title": rules[0]["_scene_instance"]["title"],
                        "location": rules[0]["_scene_instance"]["location"],
                    },
                },
                "rules": [
                    {
                        "rule_id": rule["rule_id"],
                        "trigger_condition": rule["trigger_condition"],
                        "requirement": rule["requirement"],
                        "requirement_items": requirement_items(rule),
                        "threshold": rule["threshold"],
                        "rule_effect": rule["rule_effect"],
                        "standard_code": rule["standard_code"],
                        "clause": rule["clause"],
                        "standard_source_id": rule["rule_id"],
                        "standard_rag_evidence_ids": [
                            item["id"]
                            for item in standard_evidence.get(rule["rule_id"], [])[:2]
                        ],
                        "deterministic_applicability": rule.get("_applicability"),
                        "deterministic_plan_scope": rule.get("_plan_scope"),
                        "rule_specific_plan_evidence_ids": [
                            item["id"] for item in rule.get("_plan_evidence", [])[:6]
                        ],
                        "whole_document_counter_evidence_ids": [
                            item["id"]
                            for item in rule.get("_global_counter_evidence", [])[:4]
                        ],
                    }
                    for rule in rules
                ],
                "plan_evidence": ordered_plan_sources,
                "clause_sources": list(clause_sources.values()),
                "standard_rag_evidence": ordered_rag_sources,
            },
            purpose="rule_bundle_audit",
            metadata={
                "rule_ids": [rule["rule_id"] for rule in rules],
                "scene": rules[0]["scene"],
                "control": rules[0].get("_control_title", rules[0]["process"]),
            },
            recover_array_key="decisions",
            retry_empty_array=True,
        )
        rule_by_id = {rule["rule_id"]: rule for rule in rules}
        decisions: dict[str, LLMDecision] = {}
        invalid_rule_ids: list[str] = []
        for entry in parsed.get("decisions", []):
            if not isinstance(entry, dict):
                continue
            rule_id = str(entry.get("rule_id", ""))
            rule = rule_by_id.get(rule_id)
            if rule is None or rule_id in decisions:
                continue
            try:
                decisions[rule_id] = LLMDecision.model_validate(
                    normalize_decision_payload(entry, rule)
                )
            except Exception:
                invalid_rule_ids.append(rule_id)
        for rule_id, rule in rule_by_id.items():
            if rule_id in decisions:
                continue
            decisions[rule_id] = LLMDecision(
                result=AuditResult.NEEDS_HUMAN_REVIEW,
                applicability_status=ApplicabilityStatus.UNCERTAIN,
                applicability_reason="模型批量响应缺少本条规则结果。",
                plan_obligation=PlanObligation.CONDITIONAL_MUST_STATE,
                plan_obligation_reason="模型批量响应未返回本条方案说明义务。",
                issue="模型批量响应不完整，本条需要人工确认。",
                risk_consequence=rule.get("hazards") or "可能增加高处作业安全风险。",
                suggestion="请人工核对本条规则。",
                confidence=0.0,
            )
            invalid_rule_ids.append(rule_id)
        call_id = raw.get("_monitor_call_id")
        if call_id and self.call_monitor is not None:
            partial = bool(parsed.get("_partial_parse_error")) or bool(invalid_rule_ids)
            self.call_monitor.mark_validation(call_id, partial=partial)
        raw["validation"] = {
            "valid_rule_ids": sorted(set(decisions) - set(invalid_rule_ids)),
            "invalid_or_missing_rule_ids": sorted(set(invalid_rule_ids)),
            "partial_parse": bool(parsed.get("_partial_parse_error")),
        }
        return decisions, raw

    def resolve_pending_bundle(
        self,
        rules: list[dict],
        evidence: list[dict],
        previous_decisions: dict[str, LLMDecision],
    ) -> tuple[dict[str, LLMDecision], dict]:
        """One controlled second pass before an uncertain item is hidden from users."""
        plan_sources: dict[str, dict] = {}
        for item in evidence:
            plan_sources.setdefault(
                str(item["id"]),
                {
                    "segment_id": item["id"],
                    "location": item["location"],
                    "quote": item["text"][:1200],
                },
            )
        for rule in rules:
            for item in rule.get("_plan_evidence", []):
                plan_sources.setdefault(
                    str(item["id"]),
                    {
                        "segment_id": item["id"],
                        "location": item["location"],
                        "quote": item["text"][:1200],
                    },
                )
        ordered_sources = list(plan_sources.values())[:48]
        parsed, raw = self._json_completion(
            "你是施工方案审计结论复判器。以下规则在第一次判断中处于待人工确认，若直接隐藏可能"
            "造成漏审。请利用给出的全文证据进行一次最终复判。第一次输出不完整、缺少issue或模型"
            "格式错误不属于业务歧义。若施工对象和触发条件已经确认，且规则属于施工方案必须说明的"
            "安全措施：全文有等价且完整表述判符合；全文存在明确冲突判不符合；全文检索后仍没有"
            "该必要内容判未说明。只有施工对象、设备类型、作业方式或数值触发条件确实无法从现有"
            "方案判断时，才保留待人工确认。不得输出‘模型未提供完整描述’、‘需人工核对’等占位语。"
            "同义规则必须使用相同的全文证据，不能因规范编号或章节不同得出相互矛盾的结论。"
            "每条返回rule_id、result、applicability_status、applicability_reason、plan_obligation、"
            "plan_obligation_reason、issue、risk_consequence、suggestion、selected_plan_segment_ids、"
            "decision_basis、numeric_comparison、requirement_checks、confidence。"
            "必须返回JSON对象，格式为：{\"decisions\":[{...}]}。",
            {
                "rules": [
                    {
                        "rule_id": rule["rule_id"],
                        "scene": rule["scene"],
                        "trigger_condition": rule["trigger_condition"],
                        "requirement": rule["requirement"],
                        "requirement_items": requirement_items(rule),
                        "deterministic_applicability": rule.get("_applicability"),
                        "deterministic_plan_scope": rule.get("_plan_scope"),
                        "whole_document_evidence_ids": [
                            item["id"] for item in rule.get("_plan_evidence", [])[:10]
                        ],
                        "previous_decision": previous_decisions[rule["rule_id"]].model_dump(
                            mode="json"
                        ),
                    }
                    for rule in rules
                ],
                "whole_document_evidence": ordered_sources,
            },
            purpose="pending_rule_resolution",
            metadata={"rule_ids": [rule["rule_id"] for rule in rules]},
            recover_array_key="decisions",
        )
        rule_by_id = {str(rule["rule_id"]): rule for rule in rules}
        resolved = dict(previous_decisions)
        valid_ids: list[str] = []
        for entry in parsed.get("decisions", []):
            if not isinstance(entry, dict):
                continue
            rule_id = str(entry.get("rule_id") or "")
            rule = rule_by_id.get(rule_id)
            if rule is None:
                continue
            try:
                resolved[rule_id] = LLMDecision.model_validate(
                    normalize_decision_payload(entry, rule)
                )
                valid_ids.append(rule_id)
            except Exception:
                continue
        call_id = raw.get("_monitor_call_id")
        if call_id and self.call_monitor is not None:
            self.call_monitor.mark_validation(
                call_id, partial=len(set(valid_ids)) != len(rule_by_id)
            )
        raw["validation"] = {
            "valid_rule_ids": sorted(set(valid_ids)),
            "unresolved_rule_ids": sorted(set(rule_by_id) - set(valid_ids)),
        }
        return resolved, raw


def build_audit_model(
    settings: Settings,
    use_llm: bool | None = None,
    *,
    call_monitor: ModelCallMonitor | None = None,
    run_id: str | None = None,
) -> AuditModel:
    should_use_external = settings.model_provider == "openai" if use_llm is None else use_llm
    if should_use_external:
        return OpenAICompatibleAuditModel(settings, call_monitor=call_monitor, run_id=run_id)
    return MockAuditModel()
