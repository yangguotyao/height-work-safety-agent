from backend.app.enums import AuditResult
from backend.app.services.plan_consistency import detect_plan_consistency_issues
from backend.app.services.scaffold_objects import extract_scaffold_object_cards


def _segment(identifier: str, text: str, heading: str = "脚手架方案") -> dict:
    return {
        "id": identifier,
        "text": text,
        "heading_path": heading,
        "location": f"{heading}｜{identifier}",
        "sequence_no": int(identifier.removeprefix("s")),
    }


def test_extracts_lightweight_scaffold_object_card_and_parameters():
    cards = extract_scaffold_object_cards(
        [
            _segment(
                "s1",
                "4～20层采用悬挑式钢管脚手架，立杆纵距1.5m，排距1m，步距1.8m。",
            )
        ],
        ["施工脚手架"],
    )

    assert len(cards) == 1
    assert cards[0]["scaffold_subtype"] == "cantilever"
    assert cards[0]["parameters"]["work_scaffold_width"]["value"] == 1.0
    assert cards[0]["parameters"]["work_layer_height"]["value"] == 1.8


def test_object_card_reads_parameters_from_following_text_and_calculation_table():
    cards = extract_scaffold_object_cards(
        [
            _segment("s1", "建筑物四周搭设落地式双排钢管脚手架。", "3.2设计总体思路"),
            _segment("s2", "排距（横距）为1.1m，大横杆步距为1.80m。", "3.2设计总体思路"),
            _segment(
                "s3",
                "立杆横距Lb(m)｜1.1",
                "11.1落地式脚手架承载计算 > 脚手架特性参数",
            ),
            _segment(
                "s4",
                "大横杆步距h（m）｜1.8",
                "11.1落地式脚手架承载计算 > 脚手架特性参数",
            ),
        ],
        ["施工脚手架"],
    )

    assert len(cards) == 1
    assert cards[0]["title"] == "落地式脚手架"
    assert cards[0]["parameters"]["work_scaffold_width"]["value"] == 1.1
    assert cards[0]["parameters"]["work_layer_height"]["value"] == 1.8


def test_detects_each_plan_calculation_inconsistency_once():
    items = detect_plan_consistency_issues(
        "run-1",
        [
            _segment("s1", "型钢采用14B槽钢。", "材料要求"),
            _segment("s2", "悬挑梁采用16a槽钢。", "悬挑式钢管脚手架计算书"),
            _segment("s3", "立杆底部设置20cm×20cm×5cm木垫块。", "搭设要求"),
            _segment("s4", "基础底面面积 A = 0.25m2。", "落地式钢管脚手架计算书"),
            _segment("s5", "悬挑架设置钢丝绳拉设。", "搭设要求"),
            _segment("s6", "本计算没有钢丝绳或支杆与建筑物拉结。", "悬挑式钢管脚手架计算书"),
        ],
    )

    assert [item["rule_id"] for item in items] == [
        "PLAN-CONSISTENCY-CHANNEL-STEEL",
        "PLAN-CONSISTENCY-BASE-AREA",
        "PLAN-CONSISTENCY-WIRE-ROPE-ASSUMPTION",
    ]
    assert items[-1]["result"] == AuditResult.NOT_SPECIFIED.value
