from backend.app.services.scene_router import (
    SCENE_ALIASES,
    build_scene_keyword_hints,
    build_scene_context_batches,
    filter_model_scene_instances,
    merge_scene_instances,
    route_scene_instances,
    route_scenes,
)
from backend.app.graph.audit_graph import AuditGraph


def test_every_controlled_scene_alias_can_be_routed():
    available = sorted(SCENE_ALIASES)
    segments = [
        {
            "text": "对应施工措施。",
            "id": f"segment-{index}",
            "heading_path": aliases[0],
            "location": f"P{index:03d}",
        }
        for index, aliases in enumerate(SCENE_ALIASES.values(), start=1)
    ]

    matched = route_scenes(segments, available)

    assert set(matched) == set(available)


def test_heading_creates_specific_scaffold_dismantling_instance():
    segments = [
        {
            "id": "heading-content-1",
            "text": "拆除前应进行检查并设置警戒区。",
            "heading_path": "第五章 施工措施 > 5.3 脚手架的使用与拆除",
            "location": "第五章 施工措施｜P021",
        },
        {
            "id": "heading-content-2",
            "text": "连墙件应随架体逐层拆除。",
            "heading_path": "第五章 施工措施 > 5.3 脚手架的使用与拆除",
            "location": "第五章 施工措施｜P022",
        },
    ]

    instances = route_scene_instances(
        segments, ["施工脚手架", "脚手架搭设与拆除", "高处作业综合管理"]
    )

    dismantling = [item for item in instances if item["scene"] == "脚手架搭设与拆除"]
    assert len(dismantling) == 1
    assert dismantling[0]["title"].endswith("脚手架的使用与拆除")
    assert dismantling[0]["segment_ids"] == ["heading-content-1", "heading-content-2"]
    assert dismantling[0]["sources"] == ["deterministic_heading"]


def test_single_body_mention_can_create_scene_instance_without_matching_heading():
    segments = [
        {
            "id": "segment-1",
            "sequence_no": 1,
            "text": "塔吊基础坑周边设置1.0m高防护栏杆。",
            "heading_path": "安全措施",
            "location": "安全措施｜P001",
        }
    ]

    instances = route_scene_instances(segments, ["临边作业", "高处作业综合管理"])

    assert any(item["scene"] == "临边作业" for item in instances)


def test_protective_shed_routes_cross_work_controls_without_literal_cross_work_word():
    segments = [
        {
            "id": "shed-1",
            "sequence_no": 1,
            "heading_path": "安全通道防护",
            "location": "P061",
            "text": "建筑物出入口搭设安全防护棚，顶部满铺脚手板。",
        }
    ]

    instances = route_scene_instances(segments, ["交叉作业"])

    assert len(instances) == 1
    assert instances[0]["scene"] == "交叉作业"


def test_climbing_a_scaffold_does_not_invent_powered_climbing_scaffold_scene():
    segments = [
        {
            "id": "access-1",
            "sequence_no": 1,
            "heading_path": "文明施工",
            "location": "P142",
            "text": "作业人员走人行梯道，不准攀爬架子。",
        }
    ]

    instances = route_scene_instances(segments, ["附着式升降脚手架"])

    assert instances == []


def test_basket_used_only_for_lowering_parts_is_not_working_platform_scene():
    segments = [
        {
            "id": "basket-1",
            "sequence_no": 1,
            "heading_path": "拆除措施",
            "location": "P135",
            "text": "零配件装入容器内，用吊篮吊下。",
        }
    ]

    instances = route_scene_instances(segments, ["高处作业吊篮"])

    assert instances == []


def test_high_work_does_not_invent_climbing_scene_without_access_evidence():
    segments = [
        {
            "id": "segment-1",
            "sequence_no": 1,
            "text": "高支模作业高度为14.83m。",
            "heading_path": "工程概况",
            "location": "P001",
        }
    ]

    instances = route_scene_instances(
        segments, ["攀登作业", "高处作业综合管理"]
    )

    access = [item for item in instances if item["scene"] == "攀登作业"]
    assert access == []


def test_scene_context_batches_cover_every_segment_once():
    segments = [
        {
            "id": f"segment-{index}",
            "sequence_no": index,
            "text": "高处施工内容" * 10,
            "heading_path": "施工措施",
            "location": f"P{index:03d}",
        }
        for index in range(1, 10)
    ]

    batches = build_scene_context_batches(segments, max_chars=300, max_segments=3)
    covered = [segment_id for batch in batches for segment_id in batch["segment_ids"]]

    assert len(batches) > 1
    assert covered == [segment["id"] for segment in segments]


def test_keyword_matches_are_only_hints_when_real_model_confirms_no_scene():
    segments = [
        {
            "id": "basis-1",
            "sequence_no": 1,
            "text": "编制依据：建筑施工高处作业安全技术规范。",
            "heading_path": "编制依据",
            "location": "P001",
        }
    ]

    class RepositoryStub:
        def __init__(self):
            self.updated = {}

        def list_scenes(self):
            return ["高处作业综合管理"]

        def update_audit_run(self, _run_id, **values):
            self.updated.update(values)

    class ModelStub:
        provider_name = "openai"

        def __init__(self):
            self.hints = []

        def identify_scene_instances(self, _context, _available, hints):
            self.hints.extend(hints)
            return []

    graph = object.__new__(AuditGraph)
    graph.repository = RepositoryStub()
    graph.model = ModelStub()
    graph.max_workers = 1

    result = graph._route_scene_instances(
        {
            "run_id": "run-1",
            "document_filename": "高处作业专项施工方案.docx",
            "segments": segments,
        }
    )

    assert graph.model.hints
    assert result["scene_instances"] == []
    assert result["scenes"] == []


def test_keyword_hint_source_is_not_a_confirmed_scene_source():
    hints = build_scene_keyword_hints(
        [
            {
                "id": "segment-1",
                "sequence_no": 1,
                "text": "设置防护栏杆。",
                "heading_path": "临边防护",
                "location": "P010",
            }
        ],
        ["临边作业"],
    )

    assert hints
    assert all("deterministic" not in source for source in hints[0]["sources"])
    assert all("keyword" in source for source in hints[0]["sources"])


def test_scene_batch_count_adapts_to_document_size():
    small = [
        {
            "id": f"small-{index}",
            "sequence_no": index,
            "text": "高处作业措施" * 20,
            "heading_path": "安全措施",
            "location": f"P{index:03d}",
        }
        for index in range(1, 6)
    ]
    large = [
        {
            "id": f"large-{index}",
            "sequence_no": index,
            "text": "高处作业措施" * 150,
            "heading_path": "安全措施",
            "location": f"P{index:03d}",
        }
        for index in range(1, 160)
    ]

    assert len(build_scene_context_batches(small)) == 1
    assert len(build_scene_context_batches(large)) > 1


def test_model_cannot_combine_template_dismantling_with_scaffold_edge_into_scene():
    segments = [
        {
            "id": "mixed-object",
            "sequence_no": 1,
            "heading_path": "交叉作业",
            "location": "P078",
            "text": "钢模板拆除后不得在楼层边口、通道口、脚手架边缘堆放物体。",
        }
    ]
    proposed = [
        {
            "scene": "脚手架搭设与拆除",
            "title": "模板拆除",
            "segment_ids": ["mixed-object"],
        },
        {
            "scene": "施工脚手架",
            "title": "脚手架边缘",
            "segment_ids": ["mixed-object"],
        },
    ]

    assert filter_model_scene_instances(proposed, segments) == []


def test_model_scaffold_lifecycle_scene_is_kept_with_same_object_action_evidence():
    segments = [
        {
            "id": "scaffold-work",
            "sequence_no": 1,
            "heading_path": "脚手架工程",
            "location": "P078",
            "text": "脚手架拆除前设置警戒区，并安排专人监护。",
        }
    ]
    proposed = [
        {
            "scene": "脚手架搭设与拆除",
            "title": "脚手架拆除",
            "segment_ids": ["scaffold-work"],
        }
    ]

    accepted = filter_model_scene_instances(proposed, segments)

    assert accepted[0]["scene"] == "脚手架搭设与拆除"
    assert accepted[0]["anchor_segment_ids"] == ["scaffold-work"]


def test_template_support_scaffold_is_not_classified_as_work_scaffold():
    segments = [
        {
            "id": "support-system",
            "sequence_no": 20,
            "heading_path": "模板支撑体系",
            "location": "P144",
            "text": (
                "楼板模板采用碗扣式钢管脚手架，上带可调头，支模时先搭设脚手架，"
                "再安装主龙骨和次龙骨。"
            ),
        }
    ]
    proposed = [
        {
            "scene": "施工脚手架",
            "title": "楼板模板施工",
            "segment_ids": ["support-system"],
        }
    ]

    accepted = filter_model_scene_instances(proposed, segments)

    assert accepted[0]["object_type"] == "template_support_scaffold"
    assert accepted[0]["anchor_segment_ids"] == ["support-system"]


def test_template_document_context_classifies_generic_frame_removal_as_support_scaffold():
    segments = [
        {
            "id": "title",
            "sequence_no": 1,
            "heading_path": "文档标题",
            "location": "P001",
            "text": "配套工程模板施工方案",
        },
        {
            "id": "removal",
            "sequence_no": 100,
            "heading_path": "安全措施",
            "location": "P318",
            "text": "拆除架体应一步一清，拆下的钢管、扣件分类堆放。",
        },
    ]
    proposed = [
        {
            "scene": "脚手架搭设与拆除",
            "title": "架体拆除",
            "segment_ids": ["removal"],
        }
    ]

    accepted = filter_model_scene_instances(proposed, segments)

    assert accepted[0]["object_type"] == "template_support_scaffold"


def test_emergency_fall_text_does_not_create_general_high_work_scene():
    segments = [
        {
            "id": "emergency",
            "sequence_no": 1,
            "heading_path": "应急预案 > 高处坠落事故救援",
            "location": "P350",
            "text": "发生高处坠落事故后，应立即组织抢救伤者。",
        }
    ]
    proposed = [
        {
            "scene": "高处作业综合管理",
            "title": "高处坠落事故救援",
            "segment_ids": ["emergency"],
        }
    ]

    assert filter_model_scene_instances(proposed, segments) == []


def test_explicit_operational_high_work_keeps_general_management_scene():
    segments = [
        {
            "id": "high-work",
            "sequence_no": 1,
            "heading_path": "高处作业安全措施",
            "location": "P010",
            "text": "高处作业前进行安全技术交底并检查防护设施。",
        }
    ]
    proposed = [
        {
            "scene": "高处作业综合管理",
            "title": "高处作业安全措施",
            "segment_ids": ["high-work"],
        }
    ]

    assert filter_model_scene_instances(proposed, segments)[0]["scene"] == "高处作业综合管理"


def test_product_feature_description_cannot_be_scene_evidence():
    segments = [
        {
            "id": "product-flow",
            "sequence_no": 1,
            "heading_path": "模块一 > 审计流程",
            "location": "P040",
            "text": "Agent识别临边、洞口、脚手架、操作平台、攀登、悬空作业等高处场景。",
        }
    ]
    proposed = [
        {
            "scene": "洞口作业",
            "title": "洞口防护",
            "segment_ids": ["product-flow"],
        }
    ]

    assert filter_model_scene_instances(proposed, segments) == []


def test_model_section_hits_consolidate_to_business_scene_instance():
    segments = [
        {
            "id": "segment-1",
            "text": "脚手架材料计划。",
            "heading_path": "第四章 材料计划",
            "location": "P010",
        },
        {
            "id": "segment-2",
            "text": "脚手架使用要求。",
            "heading_path": "第六章 脚手架施工",
            "location": "P030",
        },
    ]
    model_instances = [
        {
            "scene": "施工脚手架",
            "title": "脚手架材料计划",
            "segment_ids": ["segment-1"],
        },
        {
            "scene": "施工脚手架",
            "title": "脚手架使用要求",
            "segment_ids": ["segment-2"],
        },
    ]

    instances = merge_scene_instances(
        [], model_instances, segments, ["施工脚手架"]
    )

    assert len(instances) == 1
    assert instances[0]["segment_ids"] == ["segment-1", "segment-2"]


def test_scaffold_erection_and_dismantling_remain_separate_instances():
    segments = [
        {
            "id": "segment-1",
            "text": "脚手架按顺序搭设。",
            "heading_path": "6.2 脚手架搭设",
            "location": "P030",
        },
        {
            "id": "segment-2",
            "text": "脚手架按顺序拆除。",
            "heading_path": "7.2 脚手架拆除",
            "location": "P080",
        },
    ]
    proposed = [
        {
            "scene": "脚手架搭设与拆除",
            "title": "6.2 脚手架搭设",
            "segment_ids": ["segment-1"],
        },
        {
            "scene": "脚手架搭设与拆除",
            "title": "7.2 脚手架拆除",
            "segment_ids": ["segment-2"],
        },
    ]

    instances = merge_scene_instances(
        [], proposed, segments, ["脚手架搭设与拆除"]
    )

    assert len(instances) == 2
    assert {item["segment_ids"][0] for item in instances} == {
        "segment-1",
        "segment-2",
    }


def test_model_anchor_expands_to_the_rest_of_its_section():
    segments = [
        {
            "id": "segment-1",
            "text": "严禁站在悬臂结构上。",
            "heading_path": "7.2 模板拆除",
            "location": "P127",
        },
        {
            "id": "segment-2",
            "text": "6m以上柱模板拆除时设置操作平台。",
            "heading_path": "7.2 模板拆除",
            "location": "P125",
        },
    ]

    instances = merge_scene_instances(
        [],
        [
            {
                "scene": "悬空作业",
                "title": "模板拆除",
                "segment_ids": ["segment-1"],
            }
        ],
        segments,
        ["悬空作业"],
    )

    assert instances[0]["segment_ids"] == ["segment-1", "segment-2"]
