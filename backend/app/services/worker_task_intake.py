from __future__ import annotations

import json
import re
from typing import Any, Protocol

from openai import BadRequestError, OpenAI
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..config import Settings

BASE_TASK_FIELDS = ("work_content", "work_time")
CONTEXT_TASK_FIELDS = ("location", "floor", "equipment_type")
FIELD_LABELS = {
    "work_content": "作业内容",
    "location": "具体作业位置",
    "floor": "作业楼层或高度",
    "work_time": "作业时间",
    "equipment_type": "脚手架类型",
}

LOCATION_TERMS = (
    "电梯井",
    "采光井",
    "东立面",
    "西立面",
    "南立面",
    "北立面",
    "外墙",
    "屋面",
    "楼顶",
    "临边",
    "洞口",
    "吊篮",
    "操作平台",
    "楼梯间",
    "管廊",
)

SCAFFOLD_TYPE_TERMS = (
    ("附着式升降脚手架", "附着式升降脚手架"),
    ("附着式升降", "附着式升降脚手架"),
    ("爬架", "附着式升降脚手架"),
    ("悬挑式脚手架", "悬挑式脚手架"),
    ("悬挑脚手架", "悬挑式脚手架"),
    ("悬挑式", "悬挑式脚手架"),
    ("落地式脚手架", "落地式脚手架"),
    ("落地脚手架", "落地式脚手架"),
    ("落地式", "落地式脚手架"),
    ("支撑脚手架", "支撑脚手架"),
    ("满堂脚手架", "支撑脚手架"),
    ("满堂架", "支撑脚手架"),
    ("盘扣式脚手架", "盘扣式脚手架"),
    ("盘扣架", "盘扣式脚手架"),
    ("扣件式脚手架", "扣件式脚手架"),
)


class TaskExtraction(BaseModel):
    model_config = ConfigDict(extra="ignore")

    work_content: str | None = None
    location: str | None = None
    floor: str | None = None
    work_time: str | None = None
    normalized_task: str | None = None
    task_action: str | None = None
    equipment_type: str | None = None
    scenes: list[str] = Field(default_factory=list)

    @field_validator("scenes", mode="before")
    @classmethod
    def normalize_scenes(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, str):
            return [part.strip() for part in re.split(r"[，,、;；]", value) if part.strip()]
        return value


class WorkerTaskModel(Protocol):
    provider_name: str

    def extract(
        self,
        *,
        current_draft: dict[str, Any],
        message: str,
        available_scenes: list[str],
    ) -> dict[str, Any]: ...


def _clean(value: Any) -> str | None:
    text = str(value or "").strip(" ，。；;：:\n\t")
    return text or None


def infer_scenes(text: str, available_scenes: list[str]) -> list[str]:
    compact = re.sub(r"\s+", "", text)
    selected = ["高处作业综合管理", "个体防护", "安全帽使用", "安全带使用"]
    mappings = (
        (("脚手架", "排栅", "架子"), ("施工脚手架",)),
        (("脚手架拆", "拆脚手架", "拆架", "脚手架搭"), ("脚手架搭设与拆除",)),
        (("临边", "外墙", "楼层边", "屋面边"), ("临边作业", "防护栏杆")),
        (("洞口", "电梯井", "采光井", "预留孔"), ("洞口作业",)),
        (("模板", "吊装", "钢结构", "外墙作业"), ("悬空作业",)),
        (("模板拆", "拆模板", "上下同时"), ("交叉作业",)),
        (("吊篮", "悬吊平台"), ("高处作业吊篮",)),
        (("攀登", "爬梯", "梯子"), ("攀登作业",)),
        (("移动平台", "移动脚手架"), ("移动式操作平台", "操作平台通用")),
        (("悬挑平台", "卸料平台"), ("悬挑式操作平台", "操作平台通用")),
        (("落地平台",), ("落地式操作平台", "操作平台通用")),
        (("幕墙",), ("幕墙安装与验收", "玻璃幕墙")),
        (("安全网",), ("建筑施工安全网",)),
    )
    for terms, scenes in mappings:
        if any(term in compact for term in terms):
            selected.extend(scenes)
    if "脚手架" in compact and any(
        term in compact for term in ("搭设", "架设", "拆除", "拆卸", "拆架")
    ):
        selected.append("脚手架搭设与拆除")
    available = set(available_scenes)
    return list(dict.fromkeys(scene for scene in selected if scene in available))


def infer_task_action(text: str) -> str | None:
    compact = re.sub(r"\s+", "", text)
    actions = (
        (("拆除", "拆卸", "拆脚手架", "拆架", "拆模板"), "拆除"),
        (("搭设", "搭脚手架", "搭架", "架设"), "搭设"),
        (("升降", "提升", "下降"), "升降"),
        (("检查", "巡检", "验收"), "检查"),
        (("安装",), "安装"),
        (("维修", "检修", "更换"), "维修"),
        (("清理", "清洗"), "清理"),
        (("吊装",), "吊装"),
        (("施工", "作业", "使用"), "使用"),
    )
    return next(
        (action for terms, action in actions if any(term in compact for term in terms)),
        None,
    )


def infer_equipment_type(text: str) -> str | None:
    compact = re.sub(r"\s+", "", text)
    if "不清楚" in compact or "不知道" in compact:
        return "类型待现场确认"
    return next(
        (normalized for term, normalized in SCAFFOLD_TYPE_TERMS if term in compact),
        None,
    )


def normalize_work_time(value: str | None) -> str | None:
    normalized = _clean(value)
    aliases = {
        "今晚": "今天晚上",
        "明早": "明天早上",
        "明晚": "明天晚上",
    }
    return aliases.get(normalized or "", normalized)


def task_category(draft: dict[str, Any]) -> str:
    text = " ".join(
        str(draft.get(field) or "")
        for field in ("work_content", "normalized_task", "location", "equipment_type")
    )
    if any(term in text for term in ("脚手架", "爬架", "满堂架", "外架", "排栅", "架子")):
        return "scaffold"
    if "吊篮" in text or "悬吊平台" in text:
        return "basket"
    if any(term in text for term in ("模板", "外墙", "幕墙")):
        return "facade_or_formwork"
    if any(term in text for term in ("洞口", "电梯井", "采光井", "预留孔")):
        return "opening"
    if any(term in text for term in ("屋面", "楼顶")):
        return "roof"
    if any(term in text for term in ("梯子", "攀登", "操作平台")):
        return "access_or_platform"
    return "general"


def required_task_fields(draft: dict[str, Any]) -> tuple[str, ...]:
    category = task_category(draft)
    if category == "scaffold":
        return (*BASE_TASK_FIELDS, "equipment_type")
    if category == "roof":
        return (*BASE_TASK_FIELDS, "location")
    return (*BASE_TASK_FIELDS, "location", "floor")


def normalize_task_name(
    work_content: str | None, equipment_type: str | None = None
) -> str | None:
    if not work_content:
        return None
    compact = re.sub(r"[我今天明天下午上午中午晚上早上要去进行作业施工了的\s]", "", work_content)
    if any(term in compact for term in ("脚手架", "外架", "排栅", "架子")):
        action = infer_task_action(compact)
        if action:
            return f"{equipment_type or '脚手架'}{action}"
    mappings = (
        (("脚手架", "拆"), "脚手架拆除"),
        (("脚手架", "搭"), "脚手架搭设"),
        (("模板", "拆"), "模板拆除"),
        (("模板", "安装"), "模板安装"),
        (("吊篮", "幕墙"), "吊篮幕墙施工"),
        (("电梯井", "清理"), "电梯井清理"),
        (("屋面", "维修"), "屋面维修"),
        (("采光板", "更换"), "采光板更换"),
    )
    for terms, normalized in mappings:
        if all(term in compact for term in terms):
            if equipment_type and "脚手架" in normalized:
                return f"{equipment_type}{normalized.replace('脚手架', '')}"
            return normalized
    normalized = _clean(work_content)
    if equipment_type and normalized and "脚手架" in normalized:
        return f"{equipment_type}{infer_task_action(normalized) or ''}"
    return normalized


def deterministic_extract(
    current_draft: dict[str, Any], message: str, available_scenes: list[str]
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    time_match = re.search(
        r"(?:20\d{2}年)?\d{1,2}月\d{1,2}日?(?:早上|上午|中午|下午|晚上|夜间)?|"
        r"20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}(?:早上|上午|中午|下午|晚上|夜间)?|"
        r"今晚|明早|明晚|"
        r"(?:今天|明天|后天)?(?:早上|上午|中午|下午|晚上|夜间)|"
        r"(?:今天|明天|后天)(?:\d{1,2}[点时](?:\d{1,2}分)?)?",
        message,
    )
    if time_match:
        result["work_time"] = normalize_work_time(time_match.group(0))

    action = infer_task_action(message)
    if action:
        result["task_action"] = action
    equipment_type = infer_equipment_type(message)
    if equipment_type:
        result["equipment_type"] = equipment_type

    floor_match = re.search(
        r"(?:地下|负)?\d+层|[一二三四五六七八九十]+层|"
        r"\d+(?:\.\d+)?米(?:高处|高度)?|屋面|楼顶",
        message,
    )
    if floor_match:
        result["floor"] = floor_match.group(0)

    for term in LOCATION_TERMS:
        if term in message:
            result["location"] = term
            break
    area_match = re.search(
        r"(?:\d+层|屋面|楼顶)\s*([^，。；]{1,12}?)(?=进行|作业|施工|拆|安装|维修|清理)",
        message,
    )
    if area_match and area_match.group(1).strip():
        result["location"] = area_match.group(1).strip()
    if not result.get("location"):
        named_area = re.search(
            r"(?:在|去)([A-Za-z0-9一二三四五六七八九十]+(?:区|栋|号楼|作业面))",
            message,
        )
        if named_area:
            result["location"] = named_area.group(1)

    work_match = re.search(
        r"(拆除|拆|安装|搭设|维修|检修|更换|清理|清洗|吊装|浇筑|铺设|焊接|切割|打磨|运输)"
        r"[^，。！？]{1,24}",
        message,
    )
    if work_match:
        result["work_content"] = re.sub(r"[了呢啊吧]+$", "", work_match.group(0)).strip()
    elif not current_draft.get("work_content") and len(message.strip()) <= 30:
        result["work_content"] = message.strip(" ，。！？")

    missing_before = missing_task_fields(current_draft)
    short_answer = message.strip(" ，。！？")
    if len(missing_before) == 1 and len(short_answer) <= 30:
        only_field = missing_before[0]
        if only_field not in result:
            result[only_field] = short_answer

    work_content = result.get("work_content") or current_draft.get("work_content")
    current_equipment = result.get("equipment_type") or current_draft.get("equipment_type")
    result["normalized_task"] = normalize_task_name(work_content, current_equipment)
    context = " ".join(
        str(value or "")
        for value in (
            work_content,
            result.get("location") or current_draft.get("location"),
            current_equipment,
            message,
        )
    )
    result["scenes"] = infer_scenes(context, available_scenes)
    return result


def fuse_task_extractions(
    model_extracted: dict[str, Any], deterministic: dict[str, Any]
) -> dict[str, Any]:
    fused = dict(deterministic)
    for field, value in model_extracted.items():
        if field == "scenes":
            continue
        if _clean(value):
            fused[field] = value
    fused["scenes"] = list(
        dict.fromkeys(
            [
                *(model_extracted.get("scenes") or []),
                *(deterministic.get("scenes") or []),
            ]
        )
    )
    return fused


class DeterministicWorkerTaskModel:
    provider_name = "deterministic"

    def extract(
        self,
        *,
        current_draft: dict[str, Any],
        message: str,
        available_scenes: list[str],
    ) -> dict[str, Any]:
        return deterministic_extract(current_draft, message, available_scenes)


class OpenAIWorkerTaskModel:
    provider_name = "openai"

    def __init__(self, settings: Settings):
        if not settings.model_api_key or not settings.model_name:
            raise ValueError("工人助手使用外部模型时必须配置 MODEL_API_KEY 和 MODEL_NAME")
        kwargs: dict[str, Any] = {
            "api_key": settings.model_api_key,
            "timeout": settings.model_timeout_seconds,
        }
        if settings.model_base_url:
            kwargs["base_url"] = settings.model_base_url
        self.client = OpenAI(**kwargs)
        self.model_name = settings.model_name
        self.settings = settings

    def extract(
        self,
        *,
        current_draft: dict[str, Any],
        message: str,
        available_scenes: list[str],
    ) -> dict[str, Any]:
        system = (
            "你是高处作业每日任务信息采集助手。只提取用户已经明确提供的信息，"
            "不要猜测楼层、位置、时间或现场状态。返回一个 JSON 对象，字段为 "
            "work_content、location、floor、work_time、normalized_task、task_action、"
            "equipment_type、scenes。"
            "字段缺失时返回 null。scenes 只能从给定枚举中选择。"
            "task_action 优先归一为拆除、搭设、升降、检查、安装、维修、清理、吊装或使用。"
            "equipment_type 仅填写用户明确说出的脚手架类型，例如落地式脚手架、"
            "悬挑式脚手架、附着式升降脚手架、支撑脚手架、盘扣式脚手架。"
            "floor 既可以是楼层，也可以是用户明确说出的作业高度，例如30米高处。"
            "脚手架搭拆不应猜测具体楼层或位置；模板、洞口、吊篮等任务仍应提取"
            "用户明确说出的楼层和位置。"
            "例如‘今天下午在12层拆除外墙模板’应提取时间=今天下午、楼层=12层、"
            "位置=外墙、作业内容=拆除外墙模板、task_action=拆除。"
            "例如‘明天上午拆除悬挑脚手架’应提取时间=明天上午、"
            "作业内容=拆除悬挑脚手架、task_action=拆除、"
            "equipment_type=悬挑式脚手架，位置和楼层均为 null。"
        )
        prompt = json.dumps(
            {
                "current_draft": current_draft,
                "latest_message": message,
                "available_scenes": available_scenes,
            },
            ensure_ascii=False,
        )
        kwargs: dict[str, Any] = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        if self.settings.model_base_url and "dashscope" in self.settings.model_base_url:
            kwargs["extra_body"] = {"enable_thinking": False}
        try:
            response = self.client.chat.completions.create(**kwargs)
        except BadRequestError:
            kwargs.pop("response_format", None)
            response = self.client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content or "{}"
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.I)
        parsed = TaskExtraction.model_validate(json.loads(content)).model_dump()
        parsed["scenes"] = [
            scene for scene in parsed.get("scenes", []) if scene in set(available_scenes)
        ]
        return parsed


def merge_task_draft(
    current: dict[str, Any], extracted: dict[str, Any], available_scenes: list[str]
) -> dict[str, Any]:
    merged = dict(current)
    for field in (*BASE_TASK_FIELDS, *CONTEXT_TASK_FIELDS, "task_action"):
        value = _clean(extracted.get(field))
        if value:
            merged[field] = normalize_work_time(value) if field == "work_time" else value
    merged["task_action"] = merged.get("task_action") or infer_task_action(
        str(merged.get("work_content") or "")
    )
    merged["normalized_task"] = _clean(extracted.get("normalized_task")) or normalize_task_name(
        merged.get("work_content"), merged.get("equipment_type")
    )
    scene_values = [
        scene for scene in extracted.get("scenes", []) if scene in set(available_scenes)
    ]
    deterministic_scenes = infer_scenes(
        " ".join(
            str(merged.get(field) or "")
            for field in (*BASE_TASK_FIELDS, *CONTEXT_TASK_FIELDS)
        ),
        available_scenes,
    )
    merged["scenes"] = list(
        dict.fromkeys([*merged.get("scenes", []), *scene_values, *deterministic_scenes])
    )
    return merged


def missing_task_fields(draft: dict[str, Any]) -> list[str]:
    return [field for field in required_task_fields(draft) if not _clean(draft.get(field))]


def finalize_task_draft(draft: dict[str, Any]) -> dict[str, Any]:
    completed = dict(draft)
    category = task_category(completed)
    if category == "scaffold":
        completed.setdefault("location", "脚手架作业区（现场确认）")
        completed.setdefault("floor", "不按楼层定位")
    elif category == "roof":
        completed.setdefault("location", "屋面")
        completed.setdefault("floor", "屋面")
    completed["task_action"] = completed.get("task_action") or infer_task_action(
        str(completed.get("work_content") or "")
    ) or "使用"
    completed["normalized_task"] = normalize_task_name(
        completed.get("work_content"), completed.get("equipment_type")
    )
    return completed


def follow_up_question(missing_fields: list[str]) -> str:
    if missing_fields == ["equipment_type"]:
        return (
            "好的，请问是什么类型的脚手架？例如落地式、悬挑式、"
            "附着式升降或支撑脚手架；不清楚也可以直接告诉我。"
        )
    labels = [FIELD_LABELS[field] for field in missing_fields]
    if len(labels) == 1:
        return f"好的，还需要确认{labels[0]}。请告诉我这一项信息。"
    return f"好的，还需要确认{'、'.join(labels)}。请继续告诉我这些信息。"


def build_worker_task_model(
    settings: Settings, use_llm: bool | None = None
) -> WorkerTaskModel:
    should_use_external = settings.model_provider == "openai" if use_llm is None else use_llm
    return (
        OpenAIWorkerTaskModel(settings)
        if should_use_external
        else DeterministicWorkerTaskModel()
    )
