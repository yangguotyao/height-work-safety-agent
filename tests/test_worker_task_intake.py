from backend.app.services.worker_task_intake import (
    TaskExtraction,
    deterministic_extract,
    finalize_task_draft,
    fuse_task_extractions,
    merge_task_draft,
    missing_task_fields,
)

SCENES = [
    "高处作业综合管理",
    "个体防护",
    "安全帽使用",
    "安全带使用",
    "临边作业",
    "防护栏杆",
    "悬空作业",
    "交叉作业",
    "施工脚手架",
    "脚手架搭设与拆除",
    "高处作业吊篮",
]


def test_normalizes_model_null_scenes_to_empty_list():
    extraction = TaskExtraction.model_validate({"scenes": None})

    assert extraction.scenes == []


def test_normalizes_model_string_scenes_to_list():
    extraction = TaskExtraction.model_validate({"scenes": "施工脚手架、脚手架搭设与拆除"})

    assert extraction.scenes == ["施工脚手架", "脚手架搭设与拆除"]


def test_fuses_explicit_height_missed_by_model():
    model = {
        "work_content": "清洗外墙",
        "location": "东立面",
        "floor": None,
        "work_time": "后天下午",
        "scenes": ["高处作业吊篮"],
    }
    deterministic = {
        "floor": "30米高处",
        "work_time": "后天下午",
        "scenes": ["临边作业", "高处作业吊篮"],
    }

    fused = fuse_task_extractions(model, deterministic)

    assert fused["floor"] == "30米高处"
    assert fused["scenes"] == ["高处作业吊篮", "临边作业"]


def test_extracts_complete_task_from_one_message():
    extracted = deterministic_extract({}, "今天下午在12层拆除外墙模板", SCENES)
    draft = merge_task_draft({}, extracted, SCENES)

    assert missing_task_fields(draft) == []
    assert draft["work_content"] == "拆除外墙模板"
    assert draft["location"] == "外墙"
    assert draft["floor"] == "12层"
    assert draft["work_time"] == "今天下午"
    assert draft["normalized_task"] == "模板拆除"
    assert "临边作业" in draft["scenes"]
    assert "悬空作业" in draft["scenes"]


def test_keeps_partial_task_and_accepts_follow_up():
    first = merge_task_draft(
        {}, deterministic_extract({}, "今天下午我要去拆模板了", SCENES), SCENES
    )
    assert missing_task_fields(first) == ["location", "floor"]

    second = merge_task_draft(
        first, deterministic_extract(first, "12层外墙", SCENES), SCENES
    )
    assert missing_task_fields(second) == []
    assert second["work_content"] == "拆模板"
    assert second["location"] == "外墙"
    assert second["floor"] == "12层"


def test_scaffold_dismantling_asks_type_not_location_or_floor():
    first = merge_task_draft(
        {}, deterministic_extract({}, "明天上午拆除脚手架", SCENES), SCENES
    )

    assert missing_task_fields(first) == ["equipment_type"]

    second = merge_task_draft(
        first, deterministic_extract(first, "悬挑式脚手架", SCENES), SCENES
    )
    completed = finalize_task_draft(second)

    assert missing_task_fields(second) == []
    assert completed["equipment_type"] == "悬挑式脚手架"
    assert completed["task_action"] == "拆除"
    assert completed["location"] == "脚手架作业区（现场确认）"
    assert completed["floor"] == "不按楼层定位"
    assert completed["normalized_task"] == "悬挑式脚手架拆除"


def test_basket_task_still_requires_working_level():
    draft = merge_task_draft(
        {}, deterministic_extract({}, "后天下午用吊篮清洗外墙", SCENES), SCENES
    )

    assert missing_task_fields(draft) == ["floor"]


def test_basket_task_accepts_facade_and_height_instead_of_floor_number():
    draft = merge_task_draft(
        {},
        deterministic_extract({}, "后天下午在东立面用吊篮清洗外墙30米高处", SCENES),
        SCENES,
    )

    assert missing_task_fields(draft) == []
    assert draft["location"] == "东立面"
    assert draft["floor"] == "30米高处"


def test_elevator_shaft_task_requires_floor_but_keeps_night_time():
    draft = merge_task_draft(
        {}, deterministic_extract({}, "明晚清理电梯井杂物", SCENES), SCENES
    )

    assert missing_task_fields(draft) == ["floor"]
    assert draft["location"] == "电梯井"
    assert draft["work_time"] == "明天晚上"


def test_roof_task_does_not_ask_for_floor_again():
    draft = merge_task_draft(
        {}, deterministic_extract({}, "明早在屋面维修防水", SCENES), SCENES
    )

    assert missing_task_fields(draft) == []
    assert draft["work_time"] == "明天早上"
    assert draft["location"] == "屋面"


def test_scaffold_erection_infers_erection_scene_and_evening_alias():
    draft = merge_task_draft(
        {}, deterministic_extract({}, "今晚搭设落地式脚手架", SCENES), SCENES
    )

    assert missing_task_fields(draft) == []
    assert draft["work_time"] == "今天晚上"
    assert draft["task_action"] == "搭设"
    assert draft["equipment_type"] == "落地式脚手架"
    assert "脚手架搭设与拆除" in draft["scenes"]
