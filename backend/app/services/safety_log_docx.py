from __future__ import annotations

from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

EVIDENCE_TYPE_LABELS = {
    "standard": "标准规范",
    "audit": "方案审查",
    "accident": "事故案例",
    "weather": "气象数据",
    "plan": "施工方案",
    "project": "项目数据",
    "rule": "规则依据",
}


def _font(run, *, name: str = "宋体", size: float = 10.5, bold: bool = False) -> None:
    run.font.name = name
    run._element.rPr.rFonts.set(qn("w:eastAsia"), name)
    run.font.size = Pt(size)
    run.font.bold = bold


def _text(paragraph, text: str, *, bold: bool = False, size: float = 10.5) -> None:
    _font(paragraph.add_run(str(text)), bold=bold, size=size)


def _heading(document: Document, text: str, level: int = 1) -> None:
    paragraph = document.add_paragraph()
    paragraph.paragraph_format.space_before = Pt(10 if level == 1 else 7)
    paragraph.paragraph_format.space_after = Pt(5)
    size = 15 if level == 1 else 12
    _font(paragraph.add_run(text), name="黑体", size=size, bold=True)


def _bullet_list(document: Document, items: list[str], empty_text: str = "无") -> None:
    values = [str(item).strip() for item in items if str(item).strip()]
    if not values:
        paragraph = document.add_paragraph()
        _text(paragraph, empty_text)
        return
    for item in values:
        paragraph = document.add_paragraph(style="List Bullet")
        paragraph.paragraph_format.space_after = Pt(2)
        _text(paragraph, item)


def _table(
    document: Document,
    headers: list[str],
    rows: list[list[Any]],
    widths: list[float] | None = None,
) -> None:
    table = document.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    table.autofit = widths is None
    for index, header in enumerate(headers):
        cell = table.rows[0].cells[index]
        cell.text = ""
        _text(cell.paragraphs[0], header, bold=True, size=9.5)
    for values in rows:
        cells = table.add_row().cells
        for index, value in enumerate(values):
            cells[index].text = ""
            _text(cells[index].paragraphs[0], value if value not in (None, "") else "—", size=9)
    if widths:
        for index, width in enumerate(widths):
            table.columns[index].width = Cm(width)
            for row in table.rows:
                row.cells[index].width = Cm(width)


def _evidence_type_label(value: Any) -> str:
    normalized = str(value or "项目数据").strip()
    return EVIDENCE_TYPE_LABELS.get(normalized.lower(), normalized)


def _source_and_location(item: dict[str, Any]) -> str:
    values = [
        item.get("title") or item.get("source_id"),
        item.get("location"),
        item.get("source_url"),
    ]
    unique: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in unique:
            unique.append(text)
    return "\n".join(unique) or "—"


def render_safety_log_docx(log: dict[str, Any], target: Path) -> Path:
    """Render a restrained, black-and-white A4 report without LibreOffice."""
    target.parent.mkdir(parents=True, exist_ok=True)
    content = log["content"]
    summary = content["summary"]
    document = Document()
    section = document.sections[0]
    section.page_width = Cm(21)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(2.1)
    section.bottom_margin = Cm(2.0)
    section.left_margin = Cm(2.2)
    section.right_margin = Cm(2.2)

    normal = document.styles["Normal"]
    normal.font.name = "宋体"
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
    normal.font.size = Pt(10.5)

    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.paragraph_format.space_before = Pt(28)
    title.paragraph_format.space_after = Pt(18)
    _font(title.add_run("当日高处作业安全日志"), name="黑体", size=22, bold=True)

    project = document.add_paragraph()
    project.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _font(project.add_run(log["project_name"]), name="黑体", size=14, bold=True)
    meta = document.add_paragraph()
    meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _text(
        meta,
        f"日期：{log['assessment_date']}    版本：V{log['version']}    "
        f"生成时间：{log['created_at'][:19]}",
    )

    document.add_paragraph()
    _heading(document, "一、当日概况")
    _table(
        document,
        ["作业任务", "红色风险", "黄色风险", "绿色风险", "方案关联问题"],
        [[
            summary["task_count"],
            summary["red_count"],
            summary["yellow_count"],
            summary["green_count"],
            summary["audit_finding_count"],
        ]],
    )
    _heading(document, "二、当日作业与风险")
    tasks = content.get("tasks") or []
    if not tasks:
        paragraph = document.add_paragraph()
        _text(paragraph, "当日暂无已登记的高处作业任务。")
    for index, task in enumerate(tasks, 1):
        _heading(document, f"2.{index} {task['normalized_task']}", level=2)
        _table(
            document,
            ["作业内容", "位置", "楼层/高度", "时间", "风险等级", "优先分"],
            [[
                task["work_content"],
                task["work_location"],
                task["work_floor"],
                task["work_time"],
                task["risk_level"],
                task["priority_score"],
            ]],
        )
        paragraph = document.add_paragraph()
        _text(paragraph, "风险说明：", bold=True)
        _text(paragraph, task.get("risk_summary") or "无")
        _heading(document, "主要风险", level=2)
        _bullet_list(document, task.get("main_risks") or [])
        _heading(document, "作业前检查要点", level=2)
        _bullet_list(document, task.get("pre_job_checks") or [])
        _heading(document, "禁止行为", level=2)
        _bullet_list(document, task.get("prohibited_behaviors") or [])
        _heading(document, "风险控制建议", level=2)
        _bullet_list(document, task.get("interventions") or [])

    _heading(document, "三、关联方案审查问题")
    findings = content.get("audit_findings") or []
    _table(
        document,
        ["场景", "发现的问题", "整改建议", "依据"],
        [
            [
                item.get("scene"),
                item.get("issue"),
                item.get("suggestion"),
                " ".join(filter(None, [item.get("standard_code"), item.get("clause")])),
            ]
            for item in findings
        ],
    ) if findings else _bullet_list(document, [], "当日任务未匹配到相关方案审查问题。")

    _heading(document, "四、规范与事故依据")
    sources = [
        item
        for item in content.get("evidence_sources") or []
        if str(item.get("evidence_type") or "").lower() != "weather"
    ]
    if sources:
        _table(
            document,
            ["类型", "证据摘要", "来源与定位"],
            [
                [
                    _evidence_type_label(item.get("evidence_type")),
                    item.get("quote"),
                    _source_and_location(item),
                ]
                for item in sources
            ],
            widths=[2.2, 9.0, 5.4],
        )
    else:
        _bullet_list(document, [], "当前没有可展示的规范或事故证据。")

    _heading(document, "五、版本与数据来源")
    change = log.get("change") or {}
    if change.get("previous_log_id") and change.get("explanation"):
        paragraph = document.add_paragraph()
        _text(paragraph, change["explanation"])
    _bullet_list(
        document,
        [f"综合调用：{'、'.join(content.get('source_modules') or [])}",
         "安全日志不使用自动联网核验，风险等级和分数取自项目动态风险确定性计算结果。",
         "本日志用于项目安全辅助与过程留痕，不替代法定审查、作业许可和现场管理职责。"],
    )

    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _font(footer.add_run(f"{log['project_name']} · 当日高处作业安全日志 V{log['version']}"), size=8)
    document.save(target)
    return target
