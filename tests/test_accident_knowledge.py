from backend.app.config import PROJECT_ROOT
from backend.app.services.accident_knowledge import AccidentKnowledgeRepository


def test_accident_search_returns_traceable_cases():
    repository = AccidentKnowledgeRepository(
        PROJECT_ROOT
        / "data"
        / "高处作业事故知识图谱_55例"
        / "高处作业事故知识图谱.sqlite"
    )

    results = repository.search(
        normalized_task="模板拆除",
        work_content="拆除外墙模板",
        location="外墙",
        scenes=["临边", "模板支架"],
    )

    assert results
    assert len(results) == 1
    assert results[0]["case_id"] == "GZSG-012"
    assert "模板" in results[0]["task"]
    assert all(item["case_id"].startswith("GZSG-") for item in results)
    assert all(item["source_url"].startswith("http") for item in results)
    assert all(item["evidence"] for item in results)


def test_accident_search_returns_up_to_three_same_object_cases():
    repository = AccidentKnowledgeRepository(
        PROJECT_ROOT
        / "data"
        / "高处作业事故知识图谱_55例"
        / "高处作业事故知识图谱.sqlite"
    )

    results = repository.search(
        normalized_task="脚手架拆除",
        work_content="拆除落地式脚手架",
        location="建筑外立面",
        scenes=["施工脚手架", "脚手架搭设与拆除"],
    )

    assert 1 <= len(results) <= 3
    assert all("脚手架" in f"{item['task']} {item['scene']}" for item in results)


def test_accident_search_falls_back_to_only_one_weakest_related_case():
    repository = AccidentKnowledgeRepository(
        PROJECT_ROOT
        / "data"
        / "高处作业事故知识图谱_55例"
        / "高处作业事故知识图谱.sqlite"
    )

    results = repository.search(
        normalized_task="高处巡查",
        work_content="进行高处安全巡查",
        location="施工区域",
        scenes=["高处作业综合管理"],
    )

    assert len(results) == 1


def test_elevator_shaft_does_not_return_roof_skylight_opening_case():
    repository = AccidentKnowledgeRepository(
        PROJECT_ROOT
        / "data"
        / "高处作业事故知识图谱_55例"
        / "高处作业事故知识图谱.sqlite"
    )

    results = repository.search(
        normalized_task="防护栏杆更换",
        work_content="更换电梯井口防护栏杆",
        location="东侧电梯井口",
        scenes=["洞口作业"],
    )

    assert 1 <= len(results) <= 3
    assert all("电梯井" in f"{item['task']} {item['scene']} {item['title']}" for item in results)
    assert all(item["case_id"] != "GZSG-005" for item in results)


def test_basket_facade_cleaning_prefers_matching_facade_case():
    repository = AccidentKnowledgeRepository(
        PROJECT_ROOT
        / "data"
        / "高处作业事故知识图谱_55例"
        / "高处作业事故知识图谱.sqlite"
    )

    results = repository.search(
        normalized_task="外墙清洗",
        work_content="使用吊篮清洗外墙",
        location="南立面",
        scenes=["高处作业吊篮"],
    )

    assert results[0]["case_id"] == "GZSG-006"
