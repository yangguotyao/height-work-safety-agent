from __future__ import annotations

import re
from dataclasses import dataclass

from ..enums import PlanObligation


@dataclass(frozen=True)
class RuleScopeDecision:
    obligation: PlanObligation
    reason: str


EXTERNAL_ARTIFACT_RE = re.compile(
    r"产品合格证|产品合格证明|网体重量|产品分类标记|检验报告|检测报告|"
    r"型式检验|抽样检测|进场检验|进场检测|复试报告|隐蔽验收记录|"
    r"安全防护设施变更记录"
)
ACCEPTANCE_FILE_RE = re.compile(r"验收资料应包括|验收资料.*包括")
REFERENCE_DETAIL_RE = re.compile(
    r"材质、规格、物理性能|耐火性|阻燃性|网目密度|断裂张力|"
    r"材料性能|产品标准|有效期内使用"
)
EXPLICIT_PLAN_RE = re.compile(
    r"施工方案|专项方案|专项施工方案|施工组织设计|方案编制|方案审批|"
    r"安全技术措施|安全技术交底|初次作业人员.*培训"
)

PRODUCT_OR_DESIGN_SCENES = {"建筑幕墙材料", "建筑幕墙设计"}
PRODUCT_OR_DESIGN_PROCESS_RE = re.compile(
    r"设计|选材|材料|产品|加工|性能|紧固件|防雷|磨边|倒角|热浸|密封胶"
)
DIRECT_HIGH_WORK_CONTROL_RE = re.compile(
    r"防坠|坠落|安全带|安全绳|安全锁|防护栏杆|安全网|脚手板|挡脚板|"
    r"操作平台|上下通道|梯道|警戒|防护棚|停止.*作业|验收合格后方可使用"
)
SCAFFOLD_PLAN_RE = re.compile(r"脚手架.{0,20}(?:专项)?施工方案|脚手架专项方案")
SCAFFOLD_INTERFACE_RE = re.compile(
    r"安全防护网|防护栏杆|脚手板|挡脚板|上下通道|专用梯道|"
    r"荷载不得超过|停止架上作业|防滑措施|验收合格后方可使用|"
    r"个人防护用品|防滑鞋|动火申请|接火斗|灭火器|专人监护"
)


def _document_uses_external_scaffold_plan(segments: list[dict]) -> bool:
    """Identify a referenced scaffold scheme without mistaking it for this document."""
    document_title = next(
        (str(item.get("text", "")) for item in segments if item.get("text")), ""
    )
    if SCAFFOLD_PLAN_RE.search(document_title):
        return False
    return any(
        SCAFFOLD_PLAN_RE.search(
            f"{item.get('heading_path', '')} {item.get('text', '')}"
        )
        for item in segments[1:]
    )


def filter_rules_for_document_scope(
    rules: list[dict], segments: list[dict]
) -> list[dict]:
    """Keep this product focused on high-place-work plan obligations.

    Product design/material quality checks are outside this audit. When a general
    construction plan explicitly delegates scaffold engineering to a separate
    scheme, retain only the safety interfaces that the current plan must state.
    """
    external_scaffold_plan = _document_uses_external_scaffold_plan(segments)
    selected: list[dict] = []
    for rule in rules:
        scene = str(rule.get("scene", ""))
        process = str(rule.get("process", ""))
        text = " ".join(
            str(rule.get(field, ""))
            for field in ("process", "trigger_condition", "requirement", "original_text")
        )
        if scene in PRODUCT_OR_DESIGN_SCENES:
            continue
        if (
            scene in {"玻璃幕墙", "幕墙安装与验收"}
            and PRODUCT_OR_DESIGN_PROCESS_RE.search(process)
            and not DIRECT_HIGH_WORK_CONTROL_RE.search(text)
        ):
            continue
        if (
            external_scaffold_plan
            and "脚手架" in scene
            and not SCAFFOLD_INTERFACE_RE.search(text)
        ):
            continue
        selected.append(rule)
    return selected


def classify_plan_obligation(rule: dict) -> RuleScopeDecision:
    text = " ".join(
        str(rule.get(field, ""))
        for field in (
            "trigger_condition",
            "requirement",
            "original_text",
            "inspection_method",
        )
    )
    requirement = str(rule.get("requirement", ""))

    if rule.get("scene") == "安全带使用" and re.search(
        r"全身式系带|前胸|后背|缓冲器|安全绳长度|连接点|使用者.*培训", text
    ):
        return RuleScopeDecision(
            PlanObligation.REFERENCE_ONLY,
            "本条属于安全带产品选型或个体使用细则；专项施工方案已明确佩戴要求时，不因未逐项抄录产品使用说明形成缺项。",
        )
    if rule.get("scene") == "安全帽使用" and re.search(
        r"帽箍|下颏带|严重冲击|更换|报废", text
    ):
        return RuleScopeDecision(
            PlanObligation.REFERENCE_ONLY,
            "本条属于安全帽个体调节、冲击后更换或报废细则，不要求专项施工方案逐项抄录。",
        )

    if rule.get("scene") == "防护栏杆" and re.search(
        r"承受.{0,12}(?:kN|千牛).{0,12}(?:外力|荷载)|(?:外力|荷载).{0,12}(?:kN|千牛)",
        text,
        re.I,
    ):
        return RuleScopeDecision(
            PlanObligation.REFERENCE_ONLY,
            "栏杆承载性能属于设计验算或现场验收指标；方案未抄录试验荷载本身不作为正文缺项。",
        )
    if rule.get("scene") == "攀登作业" and re.search(
        r"施工通道|梯子|梯道|爬梯|攀登作业设施|登高设施", text
    ):
        return RuleScopeDecision(
            PlanObligation.MUST_STATE,
            "高处作业人员的上下通行设施属于施工组织措施，应在方案中明确。",
        )

    if ACCEPTANCE_FILE_RE.search(requirement) and EXTERNAL_ARTIFACT_RE.search(requirement):
        return RuleScopeDecision(
            PlanObligation.EXTERNAL_ONLY,
            "本条要求核验已经形成的产品证明或验收记录，属于材料验收和档案资料，不作为方案正文缺项。",
        )
    if ACCEPTANCE_FILE_RE.search(requirement):
        return RuleScopeDecision(
            PlanObligation.CONDITIONAL_MUST_STATE,
            "方案应规定安全设施验收及形成记录的管理要求，但不要求在方案正文中附上实际记录。",
        )
    if EXTERNAL_ARTIFACT_RE.search(requirement):
        return RuleScopeDecision(
            PlanObligation.EXTERNAL_ONLY,
            "本条核验对象是产品证明、检测结果或施工验收记录，不以施工方案正文是否附载为判定依据。",
        )
    if EXPLICIT_PLAN_RE.search(requirement):
        return RuleScopeDecision(
            PlanObligation.MUST_STATE,
            "规则直接要求编制方案、安全技术措施、交底或培训，属于方案应明确的核心控制。",
        )
    if REFERENCE_DETAIL_RE.search(text):
        return RuleScopeDecision(
            PlanObligation.REFERENCE_ONLY,
            "本条主要核验产品或材料性能；方案可以引用，但缺少具体检测数据本身不构成方案缺项。",
        )
    return RuleScopeDecision(
        PlanObligation.CONDITIONAL_MUST_STATE,
        "本条属于施工控制要求；只有确认具体作业条件成立且该措施应在本方案中展开时，才允许判为未说明。",
    )
