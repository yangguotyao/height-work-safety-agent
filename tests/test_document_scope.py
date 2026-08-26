from backend.app.services.document_scope import assess_construction_scheme_document


def _segment(index: int, heading: str, text: str) -> dict:
    return {
        "id": f"segment-{index}",
        "sequence_no": index,
        "heading_path": heading,
        "location": f"P{index:03d}",
        "text": text,
    }


def test_product_agent_plan_is_not_auditable_construction_scheme():
    segments = [
        _segment(1, "文档正文", "产品方案"),
        _segment(2, "文档正文", "高处作业安全智能体"),
        _segment(3, "产品概述", "Agent连接规范RAG、规则库和事故知识图谱。"),
        _segment(4, "核心模块", "系统识别临边、洞口和脚手架等高处场景。"),
    ]

    decision = assess_construction_scheme_document(
        "高处作业安全智能体产品方案.docx", segments
    )

    assert decision.auditable is False
    assert "产品或系统方案" in decision.reason


def test_explicit_construction_scheme_filename_is_auditable():
    decision = assess_construction_scheme_document(
        "钢管脚手架专项施工方案.docx",
        [_segment(1, "编制依据", "本方案依据现行规范编制。")],
    )

    assert decision.auditable is True


def test_unnamed_document_requires_scheme_structure_and_construction_actions():
    segments = [
        _segment(1, "工程概况", "本工程地上十层。"),
        _segment(2, "施工工艺", "脚手架应按顺序搭设并设置扫地杆。"),
        _segment(3, "安全技术措施", "洞口应设置盖板并固定牢固。"),
        _segment(4, "施工方法", "混凝土浇筑前应检查模板支架。"),
    ]

    assert assess_construction_scheme_document("项目文件.docx", segments).auditable


def test_safety_knowledge_article_without_scheme_structure_is_not_auditable():
    segments = [
        _segment(1, "安全知识", "脚手架搭设、模板拆除和洞口防护是常见风险。"),
        _segment(2, "事故案例", "作业人员应佩戴安全带。"),
    ]

    assert not assess_construction_scheme_document("安全知识.docx", segments).auditable
