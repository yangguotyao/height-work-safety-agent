from types import SimpleNamespace

from backend.app.graph.audit_graph import AuditGraph


class _RepositoryStub:
    def update_audit_run(self, *_args, **_kwargs):
        return None


class _DiscoveryRAGStub:
    settings = SimpleNamespace(scene_rag_limit=4)

    def __init__(self):
        self.calls = []

    def discover(self, query, **kwargs):
        self.calls.append((query, kwargs))
        return [
            {
                "id": "chunk-1",
                "standard_code": "JGJ 80-2016",
                "standard_name": "建筑施工高处作业安全技术规范",
                "clause": "4.2.1",
                "page_start": 12,
                "page_end": 12,
                "text": "洞口作业时应采取防坠落措施。",
                "score": 0.8,
                "retrieval_type": "scene_vector",
            }
        ]


def _graph(rag):
    graph = object.__new__(AuditGraph)
    graph.repository = _RepositoryStub()
    graph.standard_rag = rag
    graph.max_workers = 1
    return graph


def test_scene_standard_discovery_uses_plan_anchor_without_rule_answer():
    rag = _DiscoveryRAGStub()
    graph = _graph(rag)
    state = {
        "run_id": "run-1",
        "segments": [
            {
                "id": "plan-1",
                "text": "楼板开洞，小于300mm时钢筋不断开。",
            }
        ],
        "scene_instances": [
            {
                "id": "scene-1",
                "scene": "洞口作业",
                "title": "楼板开洞",
                "object_type": "general",
                "anchor_segment_ids": ["plan-1"],
            }
        ],
    }

    result = graph._discover_scene_standard_evidence(state)

    query, kwargs = rag.calls[0]
    assert "楼板开洞，小于300mm时钢筋不断开" in query
    assert "洞口作业时应采取防坠落措施" not in query
    assert "standard_code" not in kwargs and "clause" not in kwargs
    assert kwargs["limit"] == 4
    assert result["scene_standard_evidence"]["scene-1"][0]["retrieval_type"] == (
        "scene_vector"
    )


class _HybridRAGStub:
    def __init__(self):
        self.query = ""

    def retrieve(self, query, **_kwargs):
        self.query = query
        return [
            {
                "id": "exact-1",
                "standard_code": "GB 55023-2022",
                "standard_name": "施工脚手架通用规范",
                "clause": "5.3.7",
                "page_start": 20,
                "page_end": 20,
                "text": "支撑脚手架加载过程中架体下严禁有人。",
                "score": 1.0,
                "retrieval_type": "exact_clause",
            }
        ]


def test_exact_basis_query_excludes_rule_answer_and_marks_independent_match():
    rag = _HybridRAGStub()
    graph = _graph(rag)
    rule = {
        "scene": "施工脚手架",
        "requirement": "这段规则答案不得进入检索查询",
        "standard_code": "GB 55023-2022",
        "clause": "5.3.7",
        "pdf_page": 20,
        "_scene_instance": {
            "title": "模板支撑体系",
            "object_type": "template_support_scaffold",
        },
        "_scene_standard_evidence": [
            {
                "id": "exact-1",
                "standard_code": "GB 55023-2022",
                "standard_name": "施工脚手架通用规范",
                "clause": "5.3.7",
                "page_start": 20,
                "page_end": 20,
                "text": "支撑脚手架加载过程中架体下严禁有人。",
                "score": 0.75,
                "retrieval_type": "scene_vector",
            },
            {
                "id": "unrelated-1",
                "standard_code": "JGJ 80-2016",
                "standard_name": "建筑施工高处作业安全技术规范",
                "clause": "4.3.1",
                "page_start": 12,
                "page_end": 12,
                "text": "临边防护栏杆应符合构造规定。",
                "score": 0.8,
                "retrieval_type": "scene_vector",
            },
        ],
    }

    evidence = graph._retrieve_standard_evidence(
        rule, [{"text": "混凝土浇筑期间无关人员不得进入支模底下。"}]
    )

    assert "这段规则答案不得进入检索查询" not in rag.query
    assert evidence[0]["retrieval_type"] == "exact_clause"
    assert evidence[0]["independent_scene_match"] is True
    assert len(evidence) == 1
