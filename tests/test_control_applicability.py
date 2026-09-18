from backend.app.services.control_applicability import (
    batch_controls,
    build_control_candidates,
    merge_control_evidence,
)


def _rule(rule_id: str, trigger: str) -> dict:
    return {
        "rule_id": rule_id,
        "scene": "攀登作业",
        "process": "梯具与上下通道",
        "trigger_condition": trigger,
        "requirement": "设置安全通道",
        "threshold": "",
    }


def test_controls_are_built_before_atomic_units_with_whole_document_evidence():
    segments = [
        {
            "id": "segment-1",
            "sequence_no": 1,
            "heading_path": "施工措施",
            "location": "P001",
            "text": "人员通过斜道上下脚手架。",
        },
        {
            "id": "segment-2",
            "sequence_no": 20,
            "heading_path": "安全措施",
            "location": "P020",
            "text": "斜道两侧设置防护栏杆。",
        },
    ]
    instances = [
        {
            "id": "instance-1",
            "scene": "攀登作业",
            "title": "斜道上下通行",
            "location": "P001",
            "segment_ids": ["segment-1"],
        }
    ]
    controls = build_control_candidates(
        [_rule("PD-001", "人员通过通道上下时"), _rule("PD-002", "采用斜道时")],
        instances,
        segments,
    )

    assert len(controls) == 1
    assert [rule["rule_id"] for rule in controls[0]["rules"]] == ["PD-001", "PD-002"]
    assert {item["id"] for item in controls[0]["evidence"]} == {
        "segment-1",
        "segment-2",
    }
    assert batch_controls(controls) == [controls]


def test_ground_work_scaffold_excludes_support_and_cantilever_only_rules():
    segments = [
        {
            "id": "ground",
            "sequence_no": 1,
            "heading_path": "架体形式",
            "location": "P001",
            "text": "本工程采用落地式作业脚手架。",
        }
    ]
    rules = [
        {
            "rule_id": "JSJ-WORK",
            "scene": "施工脚手架",
            "process": "作业层防护",
            "trigger_condition": "使用作业脚手架时",
            "requirement": "作业层应满铺脚手板",
            "threshold": "",
        },
        {
            "rule_id": "JSJ-SUPPORT",
            "scene": "施工脚手架",
            "process": "支撑架加载",
            "trigger_condition": "采用支撑脚手架时",
            "requirement": "支撑脚手架加载时架体下严禁有人",
            "threshold": "",
        },
        {
            "rule_id": "JSJ-CANTILEVER",
            "scene": "施工脚手架",
            "process": "悬挑构造",
            "trigger_condition": "采用悬挑脚手架时",
            "requirement": "悬挑脚手架立杆底部应可靠连接",
            "threshold": "",
        },
    ]
    instances = [
        {
            "id": "ground-object",
            "scene": "施工脚手架",
            "title": "落地式脚手架",
            "location": "P001",
            "segment_ids": ["ground"],
            "object_type": "work_scaffold",
            "scaffold_subtype": "ground",
        }
    ]

    controls = build_control_candidates(rules, instances, segments)

    assert [rule["rule_id"] for rule in controls[0]["rules"]] == ["JSJ-WORK"]


def test_control_gate_keeps_at_least_one_trigger_evidence_for_each_rule():
    segments = [
        {
            "id": "weather",
            "sequence_no": 100,
            "heading_path": "季节性措施",
            "location": "P100",
            "text": "雨雪天气后应对脚手架安全设施进行检查。",
        },
        {
            "id": "ppe",
            "sequence_no": 101,
            "heading_path": "个人防护",
            "location": "P101",
            "text": "高处作业人员佩戴安全帽和安全带。",
        },
    ]
    rules = [
        _rule("GZ-018", "雨雪天气后"),
        _rule("GZ-008", "高处作业人员个人防护"),
    ]
    instances = [
        {
            "id": "instance-1",
            "scene": "攀登作业",
            "title": "高处作业",
            "location": "P001",
            "segment_ids": [],
        }
    ]

    controls = build_control_candidates(rules, instances, segments)
    merged = merge_control_evidence(controls, limit=1)

    required = set(controls[0]["required_evidence_ids"])
    assert required == {"weather", "ppe"}
    assert required.issubset({item["id"] for item in merged})


def test_synthetic_general_scene_only_expands_topics_present_in_scheme():
    segments = [
        {
            "id": "weather",
            "sequence_no": 1,
            "heading_path": "季节性措施",
            "location": "P100",
            "text": "大风、大雪和大雨天气停止作业，并采取防滑措施。",
        }
    ]
    rules = [
        {
            **_rule("GZ-005", "准备高处作业时"),
            "scene": "高处作业综合管理",
            "requirement": "应对初次作业人员进行培训",
        },
        {
            **_rule("GZ-018", "雨雪天气后"),
            "scene": "高处作业综合管理",
            "requirement": "雨雪天气后应检查安全设施并维修合格后使用",
        },
    ]
    instances = [
        {
            "id": "general",
            "scene": "高处作业综合管理",
            "title": "全文通用管理",
            "location": "P001",
            "segment_ids": [],
            "sources": ["deterministic_document_signal"],
        }
    ]

    controls = build_control_candidates(rules, instances, segments)

    assert [rule["rule_id"] for rule in controls[0]["rules"]] == ["GZ-018"]


def test_model_confirmed_general_scene_still_only_expands_document_topics():
    segments = [
        {
            "id": "briefing",
            "sequence_no": 1,
            "heading_path": "安全管理",
            "location": "P010",
            "text": "作业前进行安全技术交底。",
        }
    ]
    rules = [
        {
            **_rule("GZ-004", "准备高处作业时"),
            "scene": "高处作业综合管理",
            "requirement": "高处作业前应进行安全技术交底",
        },
        {
            **_rule("GZ-018", "雨雪天气后"),
            "scene": "高处作业综合管理",
            "requirement": "雨雪天气后应检查安全设施并维修合格后使用",
        },
    ]
    instances = [
        {
            "id": "general",
            "scene": "高处作业综合管理",
            "title": "安全管理",
            "location": "P010",
            "segment_ids": ["briefing"],
            "sources": ["model"],
        }
    ]

    controls = build_control_candidates(rules, instances, segments)

    assert [rule["rule_id"] for rule in controls[0]["rules"]] == ["GZ-004"]


def test_product_protection_guard_does_not_expand_fall_guardrail_rules():
    segments = [
        {
            "id": "product-guard",
            "sequence_no": 1,
            "heading_path": "成品保护措施",
            "location": "P001",
            "text": "型材固定上墙后设置护栏，防止人员穿行，避免踩踏划伤型材。",
        }
    ]
    rules = [
        {
            "rule_id": "HL-001",
            "scene": "防护栏杆",
            "process": "栏杆设置与验收",
            "trigger_condition": "设置临边防护栏杆时",
            "requirement": "栏杆由横杆、立杆及挡脚板组成",
            "threshold": "",
        }
    ]
    instances = [
        {
            "id": "guardrail-instance",
            "scene": "防护栏杆",
            "title": "成品保护护栏",
            "location": "P001",
            "segment_ids": ["product-guard"],
            "sources": ["model"],
        }
    ]

    assert build_control_candidates(rules, instances, segments) == []


def test_template_support_scaffold_does_not_expand_work_scaffold_controls():
    segments = [
        {
            "id": "support-system",
            "sequence_no": 1,
            "heading_path": "模板支撑体系",
            "location": "P144",
            "text": "楼板模板采用碗扣式钢管脚手架，支模时先搭设脚手架。",
        }
    ]
    rules = [
        {
            "rule_id": "JSJ-WORK",
            "scene": "施工脚手架",
            "process": "作业层防护",
            "trigger_condition": "采用作业脚手架时",
            "requirement": "作业层应满铺脚手板并设置挡脚板",
            "original_text": "作业脚手架作业层应满铺脚手板并设置挡脚板",
            "threshold": "",
        },
        {
            "rule_id": "JSJ-MIXED",
            "scene": "施工脚手架",
            "process": "架体连接",
            "trigger_condition": "采用施工脚手架时",
            "requirement": "严禁将支撑脚手架固定在作业脚手架上",
            "original_text": "严禁将支撑脚手架固定在作业脚手架上",
            "threshold": "",
        },
        {
            "rule_id": "JSJ-SUPPORT",
            "scene": "施工脚手架",
            "process": "支撑架使用",
            "trigger_condition": "采用支撑脚手架时",
            "requirement": "支撑脚手架加载过程中架体下严禁有人",
            "original_text": "支撑脚手架加载过程中架体下严禁有人",
            "threshold": "",
        },
    ]
    instances = [
        {
            "id": "support-instance",
            "scene": "施工脚手架",
            "title": "模板支撑体系",
            "location": "P144",
            "segment_ids": ["support-system"],
            "object_type": "template_support_scaffold",
            "sources": ["model"],
        }
    ]

    controls = build_control_candidates(rules, instances, segments)

    assert len(controls) == 1
    assert [rule["rule_id"] for rule in controls[0]["rules"]] == ["JSJ-SUPPORT"]


def test_floor_opening_does_not_expand_vertical_or_unselected_cover_rules():
    segments = [
        {
            "id": "slab-opening",
            "sequence_no": 1,
            "heading_path": "楼面模板施工",
            "location": "P082",
            "text": "楼板开洞：孔洞小于等于300mm时钢筋不断开，大于300mm时洞边加筋。",
        }
    ]
    rules = [
        {
            "rule_id": "DK-GENERAL",
            "scene": "洞口作业",
            "process": "洞口防护",
            "trigger_condition": "存在洞口时",
            "requirement": "洞口作业时应采取防坠落措施",
            "original_text": "洞口作业时应采取防坠落措施",
            "threshold": "",
        },
        {
            "rule_id": "DK-COVER",
            "scene": "洞口作业",
            "process": "洞口防护",
            "trigger_condition": "存在洞口时",
            "requirement": "洞口盖板应满足规定荷载",
            "original_text": "洞口盖板应满足规定荷载",
            "threshold": "",
        },
        {
            "rule_id": "DK-VERTICAL",
            "scene": "洞口作业",
            "process": "洞口防护",
            "trigger_condition": "存在竖向洞口时",
            "requirement": "墙面竖向洞口应按临边要求设置栏杆",
            "original_text": "墙面竖向洞口应按临边要求设置栏杆",
            "threshold": "",
        },
        {
            "rule_id": "DK-ELEVATOR",
            "scene": "洞口作业",
            "process": "洞口防护",
            "trigger_condition": "存在电梯井时",
            "requirement": "电梯井口应设置防护门",
            "original_text": "电梯井口应设置防护门",
            "threshold": "",
        },
    ]
    instances = [
        {
            "id": "floor-opening-instance",
            "scene": "洞口作业",
            "title": "楼板开洞",
            "location": "P082",
            "segment_ids": ["slab-opening"],
            "anchor_segment_ids": ["slab-opening"],
            "sources": ["model"],
        }
    ]

    controls = build_control_candidates(rules, instances, segments)

    assert len(controls) == 1
    assert [rule["rule_id"] for rule in controls[0]["rules"]] == ["DK-GENERAL"]


def test_support_scaffold_dismantling_excludes_work_scaffold_connection_rules():
    segments = [
        {
            "id": "support-removal",
            "sequence_no": 1,
            "heading_path": "模板支撑拆除",
            "location": "P318",
            "text": "拆除支模架应一步一清，拆下钢管和扣件分类堆放。",
        }
    ]
    rules = [
        {
            "rule_id": "JSJ-SETUP-SITE",
            "scene": "脚手架搭设与拆除",
            "process": "搭设场地准备",
            "trigger_condition": "搭设、使用或拆除施工脚手架时",
            "requirement": "搭设场地应平整坚实",
            "original_text": "脚手架搭设场地应平整坚实",
            "threshold": "",
        },
        {
            "rule_id": "JSJ-WALL",
            "scene": "脚手架搭设与拆除",
            "process": "连墙件安装",
            "trigger_condition": "搭设作业脚手架时",
            "requirement": "连墙件应随作业脚手架同步安装",
            "original_text": "作业脚手架连墙件应同步安装",
            "threshold": "",
        },
        {
            "rule_id": "JSJ-REMOVE",
            "scene": "脚手架搭设与拆除",
            "process": "构配件拆运",
            "trigger_condition": "拆除架体时",
            "requirement": "拆除的杆件和构配件严禁抛掷",
            "original_text": "拆除的杆件和构配件严禁抛掷",
            "threshold": "",
        },
    ]
    instances = [
        {
            "id": "support-removal-instance",
            "scene": "脚手架搭设与拆除",
            "title": "支模架拆除（拆除阶段）",
            "location": "P318",
            "segment_ids": ["support-removal"],
            "object_type": "template_support_scaffold",
            "sources": ["model"],
        }
    ]

    controls = build_control_candidates(rules, instances, segments)

    assert len(controls) == 1
    assert [rule["rule_id"] for rule in controls[0]["rules"]] == ["JSJ-REMOVE"]
