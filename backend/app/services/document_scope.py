from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DocumentScopeDecision:
    auditable: bool
    reason: str


CONSTRUCTION_FILENAME_RE = re.compile(
    r"施工组织设计|专项施工方案|专项方案|专项安全方案|安全施工方案|"
    r"施工方案|作业方案|高支模方案"
)
NON_CONSTRUCTION_TITLE_RE = re.compile(
    r"产品方案|产品设计|产品概述|系统设计|系统架构|技术架构|需求规格|"
    r"需求说明|用户手册|项目建议书|比赛原型"
)
PRODUCT_META_RE = re.compile(
    r"智能体|\bAgent\b|规则库|规范RAG|知识图谱|大模型|向量数据库|"
    r"用户需求|功能目标|核心模块|输出格式|API|前端|后端",
    re.IGNORECASE,
)
CONSTRUCTION_SECTION_RE = re.compile(
    r"工程概况|编制依据|施工部署|施工准备|施工工艺|施工方法|施工流程|"
    r"进度计划|安全技术措施|质量保证|应急预案|计算书"
)
CONSTRUCTION_OBJECT_RE = re.compile(
    r"脚手架|模板|支架|钢筋|混凝土|幕墙|吊篮|操作平台|防护栏杆|"
    r"洞口|临边|构件|钢管|扣件|安全网"
)
CONSTRUCTION_ACTION_RE = re.compile(
    r"搭设|拆除|安装|支设|浇筑|绑扎|吊装|铺设|固定|焊接|切割|"
    r"砌筑|开挖|验收|检查|设置|佩戴|系挂|严禁|不得|必须|应当|应"
)
META_SEGMENT_RE = re.compile(
    r"产品方案|产品概述|产品设计|系统架构|技术架构|功能模块|核心模块|"
    r"智能体|\bAgent\b|规则库|规范RAG|知识图谱|大模型|API|用户需求|"
    r"输入格式|输出格式|审计流程|识别.*高处场景|对应规则|模型调用",
    re.IGNORECASE,
)


def is_meta_discussion_segment(segment: dict[str, Any]) -> bool:
    text = f"{segment.get('heading_path', '')} {segment.get('text', '')}"
    return bool(META_SEGMENT_RE.search(text))


def assess_construction_scheme_document(
    filename: str, segments: list[dict[str, Any]]
) -> DocumentScopeDecision:
    """Conservatively decide whether the upload is a construction scheme.

    Safety rules audit construction execution documents, not product descriptions,
    training material, knowledge-base examples or software requirements.
    """
    first = segments[:40]
    opening_text = " ".join(
        f"{item.get('heading_path', '')} {item.get('text', '')}" for item in first
    )
    opening_title = " ".join(str(item.get("text", "")) for item in segments[:12])

    explicit_product_title = bool(NON_CONSTRUCTION_TITLE_RE.search(opening_title))
    product_meta_hits = len(PRODUCT_META_RE.findall(opening_text))
    if explicit_product_title and product_meta_hits >= 2:
        return DocumentScopeDecision(
            False, "文档标题及前部结构表明其为产品或系统方案，不是施工执行方案。"
        )

    if CONSTRUCTION_FILENAME_RE.search(filename):
        return DocumentScopeDecision(True, "文件名明确属于施工方案或施工组织设计。")

    section_hits = {
        match.group(0) for match in CONSTRUCTION_SECTION_RE.finditer(opening_text)
    }
    operational_segments = 0
    for item in segments:
        text = f"{item.get('heading_path', '')} {item.get('text', '')}"
        if META_SEGMENT_RE.search(text):
            continue
        if CONSTRUCTION_OBJECT_RE.search(text) and CONSTRUCTION_ACTION_RE.search(text):
            operational_segments += 1
            if operational_segments >= 3:
                break
    if len(section_hits) >= 2 and operational_segments >= 3:
        return DocumentScopeDecision(
            True, "文档具有施工方案章节结构，并包含多处具体施工动作。"
        )
    return DocumentScopeDecision(
        False, "未确认施工方案章节结构和具体施工执行内容，未进入高处作业规则审核。"
    )
