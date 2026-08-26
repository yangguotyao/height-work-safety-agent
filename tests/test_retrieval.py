from backend.app.services.retrieval import (
    explicit_semantic_compliance_evidence,
    retrieve_global_counter_evidence,
    retrieve_plan_evidence,
    share_semantic_control_evidence,
)


def test_domain_synonyms_retrieve_under_frame_access_control():
    rule = {
        "scene": "施工脚手架",
        "process": "混凝土浇筑",
        "trigger_condition": "支撑脚手架施加荷载时",
        "requirement": "浇筑混凝土过程中架体下严禁有人",
        "threshold": "",
    }
    segments = [
        {
            "id": "generic",
            "sequence_no": 1,
            "heading_path": "工程概况",
            "text": "高支模脚手架搭设高度及立杆间距见计算书。",
        },
        {
            "id": "control",
            "sequence_no": 2,
            "heading_path": "浇筑安全措施",
            "text": "混凝土浇筑期间，禁止无关人员进入支模底下。",
        },
    ]

    evidence = retrieve_plan_evidence(rule, segments)

    assert evidence[0]["id"] == "control"


def test_global_counter_evidence_can_find_later_chapter_compliance():
    rule = {
        "scene": "施工脚手架",
        "process": "人员上下通行",
        "trigger_condition": "人员上下脚手架时",
        "requirement": "应设置供人员上下的专用梯道，严禁攀爬架体",
        "threshold": "",
        "original_text": "作业人员应走专用梯道，不得攀爬脚手架。",
    }
    segments = [
        {
            "id": "local",
            "sequence_no": 10,
            "heading_path": "脚手架搭设",
            "text": "本工程搭设落地式脚手架。",
        },
        {
            "id": "later",
            "sequence_no": 144,
            "heading_path": "安全管理措施 > 人员通行",
            "text": "作业人员上下必须走人行梯道，严禁攀爬脚手架。",
        },
    ]

    evidence = retrieve_global_counter_evidence(rule, segments)

    assert evidence[0]["id"] == "later"


def test_equivalent_weather_rules_share_full_document_evidence():
    evidence = {
        "id": "weather-stop",
        "sequence_no": 99,
        "heading_path": "雨季施工措施",
        "location": "P099",
        "text": "遇六级风以上恶劣天气，应停止露天或高空作业。",
        "score": 0.9,
    }
    rules = [
        {
            "scene": "施工脚手架",
            "process": "使用管理",
            "trigger_condition": "6级及以上大风",
            "requirement": "6级及以上大风应停止架上作业",
            "original_text": "雷雨天气、6级及以上大风天气应停止架上作业",
            "_plan_evidence": [evidence],
            "_global_counter_evidence": [],
        },
        {
            "scene": "高处作业综合管理",
            "process": "恶劣天气",
            "trigger_condition": "6级及以上强风",
            "requirement": "不得进行露天攀登与悬空高处作业",
            "original_text": "6级及以上强风不得进行露天攀登与悬空高处作业",
            "_plan_evidence": [],
            "_global_counter_evidence": [],
        },
    ]

    share_semantic_control_evidence(rules)

    assert rules[1]["_plan_evidence"][0]["id"] == "weather-stop"
    assert explicit_semantic_compliance_evidence(rules[1], rules[1]["_plan_evidence"])
