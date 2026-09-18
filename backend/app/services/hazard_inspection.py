from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import uuid4

from fastapi import UploadFile
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI, RateLimitError
from pydantic import BaseModel, Field, model_validator

from ..config import Settings
from ..db import Database, json_load
from ..repositories import utc_now

ALLOWED_IMAGE_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}

BASIS_CONCEPTS = {
    "脚手架": ("脚手架", "架体"),
    "安全网": ("安全网", "防护网", "密目网", "防坠网", "网板"),
    "防护栏杆": ("防护栏杆", "栏杆", "护栏"),
    "挡脚板": ("挡脚板", "踢脚板"),
    "脚手板": ("脚手板", "踏板", "走道板"),
    "连墙件": ("连墙件", "拉结点", "拉结件"),
    "立杆": ("立杆",),
    "剪刀撑": ("剪刀撑", "斜撑"),
    "扣件": ("扣件", "紧固件", "连接件"),
    "基础": ("地基", "基础", "底座", "垫板"),
    "临边": ("临边",),
    "洞口": ("洞口", "孔洞"),
    "电梯井": ("电梯井", "井道"),
    "安全带": ("安全带", "安全绳", "生命绳", "挂点"),
    "安全帽": ("安全帽",),
    "吊篮": ("吊篮",),
    "操作平台": ("操作平台", "作业平台", "卸料平台"),
    "排水": ("场地积水", "积水", "排水", "泥泞"),
}
BROAD_BASIS_CONCEPTS = {"脚手架", "临边"}
ONSITE_BASIS_VERSION = "onsite_v6"
BASIS_SUBTYPE_TERMS = (
    "承插型盘扣式",
    "扣件式",
    "门式",
    "碗扣式",
    "悬挑式",
    "附着式升降",
    "满堂脚手架",
)


def _description_clause_overlap(description: str, clause_text: str) -> float:
    """Measure how much of a clause is directly reflected in the described issue."""
    normalize = lambda value: re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", value.lower())
    source = normalize(description)
    evidence = normalize(clause_text)
    if len(source) < 2 or len(evidence) < 2:
        return 0.0
    source_pairs = {source[index : index + 2] for index in range(len(source) - 1)}
    evidence_pairs = {
        evidence[index : index + 2] for index in range(len(evidence) - 1)
    }
    return len(source_pairs & evidence_pairs) / max(len(evidence_pairs), 1)


def _description_focus(description: str) -> list[str]:
    """Extract explicitly named inspection objects without treating text as visual proof."""
    normalized = description.strip()
    matches = [
        concept
        for concept, terms in BASIS_CONCEPTS.items()
        if any(term in normalized for term in terms)
    ]
    specific = [item for item in matches if item not in BROAD_BASIS_CONCEPTS]
    return specific or matches


class VisionCandidate(BaseModel):
    scene: str = Field(default="", max_length=100)
    hazard_type: str = Field(default="", max_length=100)
    suspected_hazard: str = Field(min_length=2, max_length=1000)
    visible_facts: list[str] = Field(min_length=1, max_length=2)
    unable_to_confirm: list[str] = Field(default_factory=list, max_length=2)
    recommended_checks: list[str] = Field(min_length=1, max_length=2)


class VisionAnalysis(BaseModel):
    image_quality: Literal["usable", "partially_usable", "unusable"]
    overall_visible_facts: list[str] = Field(default_factory=list, max_length=20)
    unable_to_confirm: list[str] = Field(default_factory=list, max_length=2)
    candidates: list[VisionCandidate] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def unusable_image_has_no_candidates(self) -> VisionAnalysis:
        if self.image_quality == "unusable" and self.candidates:
            raise ValueError("无法辨识的图片不能生成疑似隐患候选")
        return self


class RectificationComparison(BaseModel):
    rectification_result: Literal["已完成", "基本完成", "整改不足", "无法判断"]
    main_improvements: list[str] = Field(default_factory=list, max_length=2)
    remaining_concern: str = Field(min_length=2, max_length=300)
    review_suggestion: str = Field(min_length=2, max_length=300)


class HazardVisionProvider(Protocol):
    model_name: str

    def analyze(self, *, image_path: Path, mime_type: str, context: dict[str, Any]) -> dict: ...

    def compare(
        self,
        *,
        before_path: Path,
        before_mime_type: str,
        after_path: Path,
        after_mime_type: str,
        context: dict[str, Any],
    ) -> dict: ...


class StandardBasisProvider(Protocol):
    def discover(self, query: str, limit: int = 4) -> list[dict[str, Any]]: ...


def _strip_json_wrapper(value: str) -> str:
    text = value.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _friendly_vision_error(exc: Exception) -> str:
    if isinstance(exc, APITimeoutError):
        return "AI识别服务响应超时，请稍后重新提交。"
    if isinstance(exc, RateLimitError):
        return "AI识别服务当前繁忙，请稍后重试。"
    if isinstance(exc, APIConnectionError):
        return "暂时无法连接AI识别服务，请检查网络后重试。"
    if isinstance(exc, APIStatusError) and exc.status_code >= 500:
        return "AI识别服务暂时异常，请稍后重试。"
    message = str(exc).strip()
    if message and any("\u4e00" <= char <= "\u9fff" for char in message):
        return message[:2000]
    return "AI识别失败，请稍后重试。"


class QwenHazardVisionProvider:
    def __init__(self, settings: Settings):
        self.model_name = settings.hazard_vision_model
        self.api_key = settings.hazard_vision_api_key
        self.base_url = settings.hazard_vision_base_url
        self.timeout = settings.hazard_vision_timeout_seconds

    def analyze(self, *, image_path: Path, mime_type: str, context: dict[str, Any]) -> dict:
        if not self.api_key or not self.base_url:
            raise RuntimeError(
                "现场识别模型尚未配置，请设置 HAZARD_VISION_API_KEY 和 HAZARD_VISION_BASE_URL"
            )
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout,
            max_retries=1,
        )
        system_prompt = (
            "你是建筑施工现场安全隐患辅助识别助手。根据现场图片、现场人员描述和作业上下文，"
            "先自动识别图片中的主要施工对象，再对该对象进行有优先级的安全检查，"
            "只生成少量、重要且有直接视觉依据的疑似隐患。现场描述是本次巡检意图和关注对象的"
            "首要定位线索，但不能替代图片证据。"
            "【现场描述优先规则】如果 context.onsite_description 或 context.description_focus 明确指出"
            "某个构件、部位或异常（例如挡脚板变形松脱、防护栏杆缺失、安全网破损），必须先定位并"
            "核验该对象。图片直接支持时，它必须作为主要疑似隐患，不得被脚手架通用检查项或更容易"
            "识别的斜杆、剪刀撑问题替代。只有完成描述对象核验后，才考虑其他问题。若图片无法定位或"
            "无法支持描述中的异常，应明确写入 unable_to_confirm；不得仅凭文字把异常写成可见事实，"
            "此时最多补充1个图片中非常明显的其他高风险问题。"
            "先在内部判断主要对象属于脚手架、临边及洞口、高处作业、模板支撑、起重设备、"
            "基坑、临时用电、施工机械或其他施工场景；不要要求用户预先指定隐患类型。"
            "现场描述未明确具体检查对象时，通用检查顺序才是：第一，可能影响整体稳定、坍塌或高处坠落的结构性问题；"
            "第二，明显缺失的关键构件或防护措施；第三，明显设置异常、连接异常、变形或严重破损；"
            "第四，安全网轻微下垂、细小拼接缝和整洁度等次要问题。"
            "不得因为次要问题更容易看见就优先输出；若已有高优先级候选，不用低优先级问题干扰结论。"
            "仅当现场描述没有指出具体构件时，识别为脚手架才依次检查：架体整体稳定体系，剪刀撑或斜向稳定构件的"
            "连续性，立杆、水平杆和扫地杆等主要杆件，基础、垫板和底座，作业层脚手板与防护栏杆，"
            "可见连接节点，明显弯曲变形或严重锈蚀，最后才检查安全网或防护网的严重破损、脱落。"
            "挡脚板通常位于作业平台或脚手板外缘，重点核验是否连续、明显变形、移位、松脱或固定失效；"
            "不要把悬挂标牌、布条或普通杆件误认成挡脚板。"
            "对于剪刀撑，普通斜杆不等于剪刀撑。剪刀撑通常表现为外立面较大范围内连续布置的"
            "银白色或金属色斜向杆件，并形成连续斜向支撑体系；局部短斜杆、零散斜杆或临时支撑"
            "不能直接证明已形成完整剪刀撑。若大面积外立面清晰可见，只有零散斜杆而未形成明显"
            "连续斜向稳定体系，可以提出“疑似剪刀撑设置不足或不连续”。不得编造跨数、角度或间距。"
            "对剪刀撑、连墙件、扫地杆、防护栏杆、脚手板等缺失型问题，只有图片较完整展示应设置区域，"
            "且该区域明显未见相应构造时，才能输出“疑似缺失或设置不足”。如果有安全网、墙体或杆件"
            "遮挡，拍摄范围不足，构件可能位于内侧，或图片不清晰，只能写入 unable_to_confirm；"
            "看不见绝不等于没有设置。尤其不得因外部视角看不到连墙件就推断连墙件不足。"
            "严格区分直接可见事实、基于事实的疑似判断和无法确认内容；不得猜测图片外情况、尺寸、"
            "材质、人员身份、证照、验收状态或施工过程。你的输出只供安全员复核，不是正式隐患结论，"
            "不要给出正式风险等级。最多输出3个候选，并按重要性和视觉证据强度降序排列；"
            "第1项是主要疑似隐患，其余最多2项仅在问题确实明显时输出。每个候选的可见事实、"
            "无法确认和建议核查均最多2条。没有充分视觉证据时 candidates 必须返回空数组，"
            "不要为了产生结果强行寻找问题。"
            "仅返回符合约定字段的 JSON 对象，不要输出 Markdown。"
        )
        user_text = {
            "task": "先核验现场描述明确指出的对象，再按风险优先级生成待人工确认的疑似隐患候选",
            "context": context,
            "output_schema": {
                "image_quality": "usable | partially_usable | unusable",
                "overall_visible_facts": ["图片直接可见事实"],
                "unable_to_confirm": ["最重要的图片或上下文不足事项，最多2条"],
                "candidates": [
                    {
                        "scene": "识别出的主要施工对象或作业场景",
                        "hazard_type": "疑似隐患类型，可为空",
                        "suspected_hazard": "必须使用疑似、可能等非正式结论措辞",
                        "visible_facts": ["支持该候选的直接可见事实，最多2条"],
                        "unable_to_confirm": ["该候选仍无法确认的内容，最多2条"],
                        "recommended_checks": ["安全员下一步现场核查动作，最多2条"],
                    }
                ],
            },
        }
        response = client.chat.completions.create(
            model=self.model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": json.dumps(user_text, ensure_ascii=False)},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
                        },
                    ],
                },
            ],
            response_format={"type": "json_object"},
            extra_body={"enable_thinking": False},
        )
        content = response.choices[0].message.content or "{}"
        parsed = VisionAnalysis.model_validate_json(_strip_json_wrapper(content))
        usage = response.usage
        return {
            **parsed.model_dump(),
            "usage": {
                "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
                "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
            },
        }

    def compare(
        self,
        *,
        before_path: Path,
        before_mime_type: str,
        after_path: Path,
        after_mime_type: str,
        context: dict[str, Any],
    ) -> dict:
        if not self.api_key or not self.base_url:
            raise RuntimeError(
                "整改对比模型尚未配置，请设置 HAZARD_VISION_API_KEY 和 HAZARD_VISION_BASE_URL"
            )
        before = base64.b64encode(before_path.read_bytes()).decode("ascii")
        after = base64.b64encode(after_path.read_bytes()).decode("ascii")
        client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout,
            max_retries=1,
        )
        system_prompt = (
            "你是建筑施工现场整改效果核验助手。第一张图片是整改前，第二张图片是整改后。"
            "本任务只核验上下文中给出的原隐患及其整改要求，不是重新开展全面安全检查。"
            "优先判断原问题是否得到明显改善或消除，只描述整改前后能够直接看到的变化。"
            "只有与原隐患直接相关且在整改后图片中非常明显的问题，才能写入仍需关注；"
            "不要因为可能性、推测或缺少信息扩展新隐患，不评价与本次整改无关的脚手架整体状况。"
            "无法从图片判断的内容最多保留最关键一项，并合并进一句复核建议。"
            "输出必须简短：主要改善最多2条，仍需关注最多1条，复核建议只能有一句话。"
            "没有明显遗留问题时，remaining_concern必须原样填写“未发现与原问题直接相关的明显遗留问题”。"
            "rectification_result只能是已完成、基本完成、整改不足、无法判断之一；"
            "若整改后已直接看到针对原问题的改善，且没有直接看到明显未完成项，应判断为基本完成；"
            "不得仅因紧固度等隐藏状态无法从图片确认而判断为无法判断。"
            "该结果只是图像辅助判断，不代表工单销项，最终关闭必须由安全管理人员决定。"
            "不得猜测图片外情况、尺寸、材质、人员身份、验收状态或施工过程。"
            "仅返回符合约定字段的 JSON 对象，不要输出 Markdown。"
        )
        request_body = {
            "task": "核验原隐患是否得到整改，不进行新的全面隐患排查",
            "context": context,
            "image_order": ["整改前", "整改后"],
            "output_schema": {
                "rectification_result": "已完成 | 基本完成 | 整改不足 | 无法判断",
                "main_improvements": ["与原问题直接相关的主要可见改善，最多2条"],
                "remaining_concern": "最多1条明显遗留问题；没有则使用指定默认句",
                "review_suggestion": "包含最关键待确认事项的一句话人工复核建议",
            },
        }
        response = client.chat.completions.create(
            model=self.model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": json.dumps(request_body, ensure_ascii=False)},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{before_mime_type};base64,{before}"
                            },
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{after_mime_type};base64,{after}"},
                        },
                    ],
                },
            ],
            response_format={"type": "json_object"},
            extra_body={"enable_thinking": False},
        )
        content = response.choices[0].message.content or "{}"
        parsed = RectificationComparison.model_validate_json(_strip_json_wrapper(content))
        return parsed.model_dump()


class HazardInspectionService:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        provider: HazardVisionProvider | None = None,
        standard_rag: StandardBasisProvider | None = None,
    ):
        self.db = database
        self.settings = settings
        self.provider = provider or QwenHazardVisionProvider(settings)
        self.standard_rag = standard_rag

    @staticmethod
    def _detected_mime(data: bytes) -> str | None:
        if data.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return "image/webp"
        return None

    async def create(
        self,
        upload: UploadFile,
        *,
        description: str,
        task_id: str | None,
        submitted_by: str,
        browser_session_id: str = "default-session",
    ) -> dict[str, Any]:
        original_name = Path(upload.filename or "").name
        suffix = Path(original_name).suffix.lower()
        if suffix not in ALLOWED_IMAGE_TYPES:
            raise ValueError("现场图片只接受 JPG、PNG 或 WEBP")
        description = description.strip()
        if len(description) < 2 or len(description) > 2000:
            raise ValueError("现场描述长度应为2至2000个字符")
        if (
            task_id
            and self.db.fetch_one("SELECT id FROM work_tasks WHERE id=?", (task_id,)) is None
        ):
            raise KeyError("关联作业任务不存在")

        upload_dir = self.settings.resolved_upload_dir / "hazard_images"
        upload_dir.mkdir(parents=True, exist_ok=True)
        storage_path = upload_dir / f"{uuid4().hex}{suffix}"
        digest = hashlib.sha256()
        total = 0
        maximum = self.settings.hazard_image_max_mb * 1024 * 1024
        first_bytes = b""
        try:
            with storage_path.open("wb") as target:
                while chunk := await upload.read(1024 * 1024):
                    if not first_bytes:
                        first_bytes = chunk[:16]
                    total += len(chunk)
                    if total > maximum:
                        raise ValueError(f"现场图片超过 {self.settings.hazard_image_max_mb}MB 限制")
                    digest.update(chunk)
                    target.write(chunk)
            detected_mime = self._detected_mime(first_bytes)
            if detected_mime is None or detected_mime != ALLOWED_IMAGE_TYPES[suffix]:
                raise ValueError("图片扩展名与文件内容不匹配")
            inspection_id = uuid4().hex
            now = utc_now()
            self.db.execute(
                """INSERT INTO hazard_inspections
                   (id, browser_session_id, task_id, description, original_filename,
                    storage_path, mime_type,
                    sha256, file_size, analysis_status, model_name, submitted_by, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)""",
                (
                    inspection_id,
                    browser_session_id,
                    task_id,
                    description,
                    original_name,
                    str(storage_path),
                    detected_mime,
                    digest.hexdigest(),
                    total,
                    self.provider.model_name,
                    submitted_by,
                    now,
                ),
            )
        except Exception:
            storage_path.unlink(missing_ok=True)
            raise
        finally:
            await upload.close()
        return self.get(inspection_id)

    def _analysis_context(self, inspection: dict[str, Any]) -> dict[str, Any]:
        context: dict[str, Any] = {
            "project_name": self.settings.project_name,
            "onsite_description": inspection["description"],
            "description_focus": _description_focus(inspection["description"]),
        }
        task_id = inspection.get("task_id")
        if not task_id:
            context["work_task"] = None
            return context
        task = self.db.fetch_one("SELECT * FROM work_tasks WHERE id=?", (task_id,))
        if task is None:
            context["work_task"] = None
            return context
        context["work_task"] = {
            "id": task["id"],
            "work_content": task["work_content"],
            "work_location": task["work_location"],
            "work_floor": task["work_floor"],
            "work_time": task["work_time"],
            "task_action": task.get("task_action") or "",
            "equipment_type": task.get("equipment_type") or "",
            "scenes": json_load(task.get("scenes_json"), []),
        }
        stored_card = self.db.fetch_one(
            "SELECT card_json FROM task_risk_cards WHERE task_id=?", (task_id,)
        )
        if stored_card:
            card = json_load(stored_card["card_json"], {})
            context["existing_risk_card"] = {
                "risk_level": card.get("risk_level"),
                "key_risks": (card.get("key_risks") or [])[:8],
                "controls": (card.get("controls") or [])[:8],
            }
        if task.get("audit_run_id"):
            context["related_plan_findings"] = self.db.fetch_all(
                """SELECT i.scene, i.issue, i.suggestion,
                          COALESCE(i.final_result, i.result) result
                   FROM audit_items i
                   WHERE i.run_id=? AND COALESCE(i.final_result, i.result) != '符合'
                   ORDER BY i.created_at LIMIT 8""",
                (task["audit_run_id"],),
            )
        return context

    def analyze(self, inspection_id: str) -> None:
        inspection = self.db.fetch_one(
            "SELECT * FROM hazard_inspections WHERE id=?", (inspection_id,)
        )
        if inspection is None:
            return
        self.db.execute(
            "UPDATE hazard_inspections SET analysis_status='analyzing', error=NULL WHERE id=?",
            (inspection_id,),
        )
        try:
            result = self.provider.analyze(
                image_path=Path(inspection["storage_path"]),
                mime_type=inspection["mime_type"],
                context=self._analysis_context(inspection),
            )
            validated = VisionAnalysis.model_validate(result)
            now = utc_now()
            with self.db.connect() as connection:
                connection.execute(
                    """UPDATE hazard_inspections
                       SET analysis_status='ready', image_quality=?,
                           overall_visible_facts_json=?, unable_to_confirm_json=?,
                           ai_result_json=?, completed_at=?, error=NULL WHERE id=?""",
                    (
                        validated.image_quality,
                        json.dumps(validated.overall_visible_facts, ensure_ascii=False),
                        json.dumps(validated.unable_to_confirm, ensure_ascii=False),
                        json.dumps(result, ensure_ascii=False),
                        now,
                        inspection_id,
                    ),
                )
                connection.execute(
                    "DELETE FROM hazard_candidates WHERE inspection_id=?", (inspection_id,)
                )
                for index, candidate in enumerate(validated.candidates, start=1):
                    suspected_hazard = candidate.suspected_hazard
                    if not any(word in suspected_hazard for word in ("疑似", "可能", "需核查")):
                        suspected_hazard = f"疑似：{suspected_hazard}"
                    connection.execute(
                        """INSERT INTO hazard_candidates
                           (id, inspection_id, sequence_no, scene, hazard_type,
                            suspected_hazard, visible_facts_json, unable_to_confirm_json,
                            recommended_checks_json, review_status, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
                        (
                            uuid4().hex,
                            inspection_id,
                            index,
                            candidate.scene,
                            candidate.hazard_type,
                            suspected_hazard,
                            json.dumps(candidate.visible_facts, ensure_ascii=False),
                            json.dumps(candidate.unable_to_confirm, ensure_ascii=False),
                            json.dumps(candidate.recommended_checks, ensure_ascii=False),
                            now,
                        ),
                    )
        except Exception as exc:
            self.db.execute(
                """UPDATE hazard_inspections SET analysis_status='failed', error=?,
                   completed_at=? WHERE id=?""",
                (_friendly_vision_error(exc), utc_now(), inspection_id),
            )

    @staticmethod
    def _candidate(row: dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        item["visible_facts"] = json_load(item.pop("visible_facts_json"), [])
        item["unable_to_confirm"] = json_load(item.pop("unable_to_confirm_json"), [])
        item["recommended_checks"] = json_load(item.pop("recommended_checks_json"), [])
        return item

    @staticmethod
    def _safety_item(row: dict[str, Any] | None) -> dict[str, Any] | None:
        return dict(row) if row else None

    def get(self, inspection_id: str) -> dict[str, Any]:
        row = self.db.fetch_one("SELECT * FROM hazard_inspections WHERE id=?", (inspection_id,))
        if row is None:
            raise KeyError("现场巡检记录不存在")
        item = dict(row)
        item.pop("storage_path", None)
        item["overall_visible_facts"] = json_load(item.pop("overall_visible_facts_json"), [])
        item["unable_to_confirm"] = json_load(item.pop("unable_to_confirm_json"), [])
        item["ai_original_result"] = json_load(item.pop("ai_result_json", None), {})
        candidates = self.db.fetch_all(
            "SELECT * FROM hazard_candidates WHERE inspection_id=? ORDER BY sequence_no",
            (inspection_id,),
        )
        item["candidates"] = []
        for candidate_row in candidates:
            candidate = self._candidate(candidate_row)
            candidate["safety_item"] = self._safety_item(
                self.db.fetch_one(
                    "SELECT * FROM safety_items WHERE source_candidate_id=?",
                    (candidate["id"],),
                )
            )
            item["candidates"].append(candidate)
        item["image_url"] = f"/api/v1/hazard-inspections/{inspection_id}/image"
        return item

    def list(
        self, limit: int = 30, browser_session_id: str = "default-session"
    ) -> list[dict[str, Any]]:
        rows = self.db.fetch_all(
            """SELECT id FROM hazard_inspections
               WHERE browser_session_id IN ('baseline', 'legacy', ?)
               ORDER BY created_at DESC LIMIT ?""",
            (browser_session_id, limit),
        )
        return [self.get(row["id"]) for row in rows]

    def image_path(self, inspection_id: str) -> tuple[Path, str]:
        row = self.db.fetch_one(
            "SELECT storage_path, mime_type FROM hazard_inspections WHERE id=?",
            (inspection_id,),
        )
        if row is None:
            raise KeyError("现场巡检记录不存在")
        path = Path(row["storage_path"])
        if not path.is_file():
            raise FileNotFoundError("现场图片不存在")
        return path, row["mime_type"]

    @staticmethod
    def _record_event(
        connection: Any,
        *,
        safety_item_id: str,
        event_type: str,
        title: str,
        actor: str,
        at: str,
        detail: str = "",
        payload: dict[str, Any] | None = None,
    ) -> None:
        event_id = uuid4().hex
        body = {"title": title, "detail": detail, "actor": actor, **(payload or {})}
        connection.execute(
            """INSERT INTO platform_data_events
               (id, project_id, event_type, entity_type, entity_id, version_key,
                payload_json, created_at)
               VALUES (?, 'default-project', ?, 'safety_item', ?, ?, ?, ?)""",
            (
                event_id,
                event_type,
                safety_item_id,
                event_id,
                json.dumps(body, ensure_ascii=False),
                at,
            ),
        )

    def _standard_basis(
        self, candidate: dict[str, Any], formal_item: dict[str, Any]
    ) -> list[dict[str, Any]]:
        if self.standard_rag is None:
            return []
        core_fields = [
            ("现场描述", candidate.get("onsite_description")),
            ("作业场景", candidate.get("scene")),
            ("AI识别类型", candidate.get("hazard_type")),
            ("AI疑似问题", candidate.get("suspected_hazard")),
            ("人工确认类别", formal_item.get("hazard_category")),
            ("人工确认事实", formal_item.get("fact_description")),
        ]
        query_parts = [
            f"{label}：{value or ''}" for label, value in core_fields
        ] + [
            f"整改要求：{formal_item.get('rectification_requirement') or ''}",
        ]
        query = "；".join(part for part in query_parts if not part.endswith("："))
        primary_concepts = self._basis_concepts(
            " ".join(str(value or "") for _, value in core_fields)
        )
        remediation_concepts = self._basis_concepts(
            str(formal_item.get("rectification_requirement") or "")
        )
        issue_concepts = primary_concepts | remediation_concepts
        specific_concepts = issue_concepts - BROAD_BASIS_CONCEPTS
        specific_primary_concepts = primary_concepts - BROAD_BASIS_CONCEPTS
        if issue_concepts:
            query = f"核心检索词：{'、'.join(sorted(issue_concepts))}；{query}"
        source_text = " ".join(str(value or "") for _, value in core_fields)
        # The focused retrieval is driven by the observed problem itself.  Do not
        # expand it into a preselected clause or let the proposed remedy dominate
        # what the image and human confirmation actually describe.
        focus_query = " ".join(
            str(value or "")
            for label, value in core_fields
            if label in {"AI识别类型", "AI疑似问题", "人工确认类别", "人工确认事实"}
        )
        concept_queries: list[str] = []
        concept_priority = sorted(
            specific_primary_concepts,
            key=lambda concept: -sum(
                focus_query.count(alias) for alias in BASIS_CONCEPTS[concept]
            ),
        )
        broad_context = " ".join(sorted(primary_concepts & BROAD_BASIS_CONCEPTS))
        for concept in concept_priority[:4]:
            observed_aliases = [
                alias for alias in BASIS_CONCEPTS[concept] if alias in focus_query
            ]
            concept_queries.append(
                " ".join(
                    part
                    for part in [broad_context, concept, *observed_aliases]
                    if part
                )
            )
        try:
            matches = self.standard_rag.discover(query, limit=40)
            focused_matches = self.standard_rag.discover(focus_query, limit=40)
            concept_matches = [
                item
                for concept_query in concept_queries
                for item in self.standard_rag.discover(concept_query, limit=12)
            ]
        except Exception:
            return []
        merged_matches: dict[str, dict[str, Any]] = {}
        for item in [*matches, *focused_matches, *concept_matches]:
            item_id = str(item.get("id") or "")
            current = merged_matches.get(item_id)
            if current is None or float(item.get("score") or 0.0) > float(
                current.get("score") or 0.0
            ):
                merged_matches[item_id] = item
        ranked: list[tuple[int, int, float, int, int, float, dict[str, Any]]] = []
        for item in merged_matches.values():
            if not item.get("standard_code") or not item.get("clause") or not item.get("text"):
                continue
            evidence_text = " ".join(
                str(item.get(key) or "")
                for key in ("standard_name", "title_path", "text")
            )
            evidence_concepts = self._basis_concepts(evidence_text)
            overlap = issue_concepts & evidence_concepts
            if issue_concepts and not overlap:
                continue
            required_concepts = specific_primary_concepts or specific_concepts
            if required_concepts and not (required_concepts & evidence_concepts):
                continue
            specificity = (
                len(specific_primary_concepts & evidence_concepts) * 4
                + len(remediation_concepts & evidence_concepts) * 2
                + len(overlap)
            )
            actionability = int(
                any(
                    term in evidence_text
                    for term in (
                        "应设置",
                        "应采取",
                        "应符合",
                        "应保持",
                        "不应",
                        "不得",
                        "严禁",
                        "必须",
                    )
                )
            )
            matched_aliases = {
                alias
                for concept in primary_concepts
                for alias in BASIS_CONCEPTS[concept]
                if alias in source_text and alias in evidence_text
            }
            # A short clause that directly states the observed issue is preferable
            # to a long checklist that happens to mention several related objects.
            clause_body = str(item.get("text") or "").split("\n", 1)[-1]
            directness = _description_clause_overlap(source_text, clause_body) + (
                sum(len(alias) for alias in matched_aliases)
                / max(len(evidence_text), 80)
            )
            has_unmatched_subtype = any(
                term in evidence_text and term not in source_text
                for term in BASIS_SUBTYPE_TERMS
            )
            applicability = int(not has_unmatched_subtype)
            unrelated_specific = (
                evidence_concepts - primary_concepts - BROAD_BASIS_CONCEPTS
            )
            scope_fit = -len(unrelated_specific)
            ranked.append(
                (
                    applicability,
                    scope_fit,
                    directness,
                    specificity,
                    actionability,
                    float(item.get("score") or 0.0),
                    item,
                )
            )
        ranked.sort(
            key=lambda value: (
                -value[0],
                -value[1],
                -value[2],
                -value[3],
                -value[4],
                -value[5],
            )
        )
        results = []
        for _, _, _, _, _, _, item in ranked[:1]:
            clause = str(item.get("clause") or "").strip()
            quote = str(item.get("text") or "").split("\n", 1)[-1].strip()
            if clause and quote.startswith(clause):
                quote = quote[len(clause) :].strip()
            results.append(
                {
                    "source_id": item.get("id"),
                    "standard_code": item.get("standard_code"),
                    "standard_name": item.get("standard_name"),
                    "clause": clause,
                    "quote": quote,
                    "page": item.get("page_start"),
                    "score": item.get("score"),
                    "retrieval_version": ONSITE_BASIS_VERSION,
                }
            )
        return results

    @staticmethod
    def _basis_concepts(text: str) -> set[str]:
        normalized = "".join(str(text).lower().split())
        return {
            concept
            for concept, aliases in BASIS_CONCEPTS.items()
            if any(alias in normalized for alias in aliases)
        }

    @staticmethod
    def _public_comparison(value: dict[str, Any]) -> dict[str, Any]:
        if "rectification_result" in value:
            return value
        improvements = list(value.get("visible_changes") or [])[:2]
        remaining = list(value.get("remaining_suspected_hazards") or [])
        checks = list(value.get("recommended_checks") or [])
        unknown = list(value.get("unable_to_confirm") or [])
        remaining_concern = remaining[0] if remaining else ""
        uncertainty_terms = ("无法", "难以", "不能", "待确认", "需确认", "需核查")
        if remaining_concern and any(
            term in remaining_concern for term in uncertainty_terms
        ):
            unknown.insert(0, remaining_concern)
            remaining_concern = ""
        return {
            "rectification_result": (
                "基本完成" if improvements and not remaining_concern else "无法判断"
            ),
            "main_improvements": improvements,
            "remaining_concern": (
                remaining_concern
                if remaining_concern
                else "未发现与原问题直接相关的明显遗留问题"
            ),
            "review_suggestion": (
                checks[0]
                if checks
                else unknown[0]
                if unknown
                else "请结合现场情况完成人工复核。"
            ),
        }

    def review(
        self,
        candidate_id: str,
        *,
        action: str,
        reviewer_ref: str,
        comment: str | None = None,
        formal_item: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        candidate = self.db.fetch_one(
            """SELECT c.*, i.task_id, i.id inspection_id, i.description onsite_description,
                      i.created_at inspection_created_at,
                      i.completed_at inspection_completed_at
               FROM hazard_candidates c JOIN hazard_inspections i ON i.id=c.inspection_id
               WHERE c.id=?""",
            (candidate_id,),
        )
        if candidate is None:
            raise KeyError("疑似隐患候选不存在")
        if candidate["review_status"] != "pending":
            raise ValueError("该候选已经完成过人工复核")
        if action == "confirm" and formal_item is None:
            raise ValueError("确认候选时必须填写正式安全事项")
        basis = self._standard_basis(candidate, formal_item or {}) if formal_item else []
        now = utc_now()
        with self.db.connect() as connection:
            connection.execute(
                """UPDATE hazard_candidates SET review_status=?, reviewer_ref=?,
                   review_comment=?, reviewed_at=? WHERE id=? AND review_status='pending'""",
                (
                    "confirmed" if action == "confirm" else "rejected",
                    reviewer_ref,
                    comment,
                    now,
                    candidate_id,
                ),
            )
            if action == "confirm" and formal_item is not None:
                safety_item_id = uuid4().hex
                item_no = f"AQ-{now[:10].replace('-', '')}-{safety_item_id[:6].upper()}"
                order_id = uuid4().hex
                order_no = f"ZG-{now[:10].replace('-', '')}-{order_id[:6].upper()}"
                connection.execute(
                    """INSERT INTO safety_items
                       (id, item_no, source_candidate_id, task_id, source, title,
                        fact_description, hazard_category, risk_level, location, basis_json,
                        basis_retrieval_version, rectification_requirement, responsible_ref,
                        deadline, status,
                        confirmed_by, confirmed_at, created_by, created_at, updated_at)
                       VALUES (?, ?, ?, ?, 'onsite_multimodal', ?, ?, ?, ?, ?, ?,
                               ?, ?, ?, ?,
                               'processing', ?, ?, ?, ?, ?)""",
                    (
                        safety_item_id,
                        item_no,
                        candidate_id,
                        candidate["task_id"],
                        formal_item["title"],
                        formal_item["fact_description"],
                        formal_item["hazard_category"],
                        formal_item["risk_level"],
                        formal_item["location"],
                        json.dumps(basis, ensure_ascii=False),
                        ONSITE_BASIS_VERSION,
                        formal_item["rectification_requirement"],
                        formal_item["responsible_ref"],
                        formal_item.get("deadline"),
                        reviewer_ref,
                        now,
                        reviewer_ref,
                        now,
                        now,
                    ),
                )
                connection.execute(
                    """INSERT INTO rectification_orders
                       (id, order_no, safety_item_id, responsible_ref, team_or_area,
                        requirement, due_at, risk_level, immediate, status, created_by,
                        created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_rectification', ?, ?, ?)""",
                    (
                        order_id,
                        order_no,
                        safety_item_id,
                        formal_item["responsible_ref"],
                        formal_item["responsible_ref"],
                        formal_item["rectification_requirement"],
                        formal_item.get("deadline"),
                        formal_item["risk_level"],
                        int(formal_item["risk_level"] == "red"),
                        reviewer_ref,
                        now,
                        now,
                    ),
                )
                self._record_event(
                    connection,
                    safety_item_id=safety_item_id,
                    event_type="onsite_reported",
                    title="现场问题已上报",
                    actor="现场上报人",
                    at=candidate["inspection_created_at"],
                    detail="现场图片和文字描述已提交",
                )
                self._record_event(
                    connection,
                    safety_item_id=safety_item_id,
                    event_type="ai_candidate_generated",
                    title="AI生成疑似隐患候选",
                    actor=self.provider.model_name,
                    at=candidate["inspection_completed_at"] or candidate["created_at"],
                    detail=candidate["suspected_hazard"],
                )
                self._record_event(
                    connection,
                    safety_item_id=safety_item_id,
                    event_type="human_confirmed",
                    title="人工确认正式安全事项",
                    actor=reviewer_ref,
                    at=now,
                    detail=formal_item["fact_description"],
                )
                self._record_event(
                    connection,
                    safety_item_id=safety_item_id,
                    event_type="rectification_order_created",
                    title="整改任务已下达",
                    actor=reviewer_ref,
                    at=now,
                    detail=f"责任对象：{formal_item['responsible_ref']}",
                    payload={"order_id": order_id, "order_no": order_no},
                )
        return self.get(candidate["inspection_id"])

    @staticmethod
    def _public_safety_item(row: dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        item["basis"] = json_load(item.pop("basis_json", None), [])
        item.pop("basis_retrieval_version", None)
        if "immediate" in item:
            item["immediate"] = bool(item["immediate"])
        return item

    def safety_items(
        self, limit: int = 50, browser_session_id: str = "default-session"
    ) -> list[dict[str, Any]]:
        rows = self.db.fetch_all(
            """SELECT s.*, o.id order_id, o.order_no, o.status order_status,
                      o.due_at order_due_at
               FROM safety_items s
               JOIN hazard_candidates c ON c.id=s.source_candidate_id
               JOIN hazard_inspections i ON i.id=c.inspection_id
               LEFT JOIN rectification_orders o ON o.safety_item_id=s.id
               WHERE i.browser_session_id IN ('baseline', 'legacy', ?)
               ORDER BY s.created_at DESC LIMIT ?""",
            (browser_session_id, limit),
        )
        return [self._public_safety_item(row) for row in rows]

    def safety_item_detail(self, safety_item_id: str) -> dict[str, Any]:
        row = self.db.fetch_one(
            """SELECT s.*, c.scene, c.hazard_type, c.suspected_hazard, c.visible_facts_json,
                      c.unable_to_confirm_json candidate_unable_json,
                      c.recommended_checks_json, c.reviewer_ref candidate_reviewer,
                      c.reviewed_at candidate_reviewed_at,
                      i.id inspection_id, i.description onsite_description,
                      i.model_name, i.ai_result_json, i.original_filename before_filename,
                      i.submitted_by, i.created_at reported_at,
                      o.id order_id, o.order_no, o.status order_status,
                      o.responsible_ref order_responsible_ref,
                      o.team_or_area, o.requirement order_requirement,
                      o.due_at order_due_at, o.immediate
               FROM safety_items s
               JOIN hazard_candidates c ON c.id=s.source_candidate_id
               JOIN hazard_inspections i ON i.id=c.inspection_id
               LEFT JOIN rectification_orders o ON o.safety_item_id=s.id
               WHERE s.id=?""",
            (safety_item_id,),
        )
        if row is None:
            raise KeyError("安全事项不存在")
        if row.get("basis_retrieval_version") != ONSITE_BASIS_VERSION:
            basis = self._standard_basis(row, row)
            self.db.execute(
                """UPDATE safety_items
                   SET basis_json=?, basis_retrieval_version=?, updated_at=?
                   WHERE id=?""",
                (
                    json.dumps(basis, ensure_ascii=False),
                    ONSITE_BASIS_VERSION,
                    utc_now(),
                    safety_item_id,
                ),
            )
            row["basis_json"] = json.dumps(basis, ensure_ascii=False)
            row["basis_retrieval_version"] = ONSITE_BASIS_VERSION
        item = self._public_safety_item(row)
        item["visible_facts"] = json_load(item.pop("visible_facts_json", None), [])
        item["candidate_unable_to_confirm"] = json_load(
            item.pop("candidate_unable_json", None), []
        )
        item["recommended_checks"] = json_load(
            item.pop("recommended_checks_json", None), []
        )
        item["ai_original_result"] = json_load(item.pop("ai_result_json", None), {})
        item["before_image_url"] = (
            f"/api/v1/hazard-inspections/{item['inspection_id']}/image"
        )
        submissions = self.db.fetch_all(
            """SELECT * FROM rectification_submissions WHERE order_id=?
               ORDER BY attempt_no""",
            (item.get("order_id"),),
        ) if item.get("order_id") else []
        for submission in submissions:
            submission.pop("storage_path", None)
            submission["comparison"] = self._public_comparison(
                json_load(submission.pop("comparison_json", None), {})
            )
            submission["image_url"] = (
                f"/api/v1/rectification-submissions/{submission['id']}/image"
            )
        item["submissions"] = submissions
        item["reviews"] = self.db.fetch_all(
            """SELECT * FROM rectification_reviews WHERE order_id=?
               ORDER BY sequence_no""",
            (item.get("order_id"),),
        ) if item.get("order_id") else []
        events = self.db.fetch_all(
            """SELECT * FROM platform_data_events
               WHERE entity_type='safety_item' AND entity_id=?
               ORDER BY created_at, rowid""",
            (safety_item_id,),
        )
        item["timeline"] = [
            {
                "id": event["id"],
                "type": event["event_type"],
                "at": event["created_at"],
                **json_load(event["payload_json"], {}),
            }
            for event in events
        ]
        return item

    async def submit_rectification(
        self,
        order_id: str,
        upload: UploadFile,
        *,
        description: str,
        submitted_by: str,
    ) -> dict[str, Any]:
        order = self.db.fetch_one(
            """SELECT o.*, s.id safety_item_id FROM rectification_orders o
               JOIN safety_items s ON s.id=o.safety_item_id WHERE o.id=?""",
            (order_id,),
        )
        if order is None:
            raise KeyError("整改任务不存在")
        if order["status"] not in {"pending_rectification", "rectifying"}:
            raise ValueError("当前整改任务不能提交整改证据")
        description = description.strip()
        if len(description) < 2 or len(description) > 3000:
            raise ValueError("整改说明长度应为2至3000个字符")
        original_name = Path(upload.filename or "").name
        suffix = Path(original_name).suffix.lower()
        if suffix not in ALLOWED_IMAGE_TYPES:
            raise ValueError("整改后图片只接受 JPG、PNG 或 WEBP")
        upload_dir = self.settings.resolved_upload_dir / "rectification_images"
        upload_dir.mkdir(parents=True, exist_ok=True)
        storage_path = upload_dir / f"{uuid4().hex}{suffix}"
        digest = hashlib.sha256()
        total = 0
        maximum = self.settings.hazard_image_max_mb * 1024 * 1024
        first_bytes = b""
        try:
            with storage_path.open("wb") as target:
                while chunk := await upload.read(1024 * 1024):
                    if not first_bytes:
                        first_bytes = chunk[:16]
                    total += len(chunk)
                    if total > maximum:
                        raise ValueError(
                            f"整改后图片超过 {self.settings.hazard_image_max_mb}MB 限制"
                        )
                    digest.update(chunk)
                    target.write(chunk)
            detected_mime = self._detected_mime(first_bytes)
            if detected_mime is None or detected_mime != ALLOWED_IMAGE_TYPES[suffix]:
                raise ValueError("图片扩展名与文件内容不匹配")
            now = utc_now()
            submission_id = uuid4().hex
            with self.db.connect() as connection:
                attempt = connection.execute(
                    "SELECT COUNT(*) FROM rectification_submissions WHERE order_id=?",
                    (order_id,),
                ).fetchone()[0] + 1
                connection.execute(
                    """INSERT INTO rectification_submissions
                       (id, order_id, attempt_no, description, original_filename,
                        storage_path, mime_type, sha256, file_size, submitted_by,
                        submitted_at, comparison_status, comparison_model)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?)""",
                    (
                        submission_id,
                        order_id,
                        attempt,
                        description,
                        original_name,
                        str(storage_path),
                        detected_mime,
                        digest.hexdigest(),
                        total,
                        submitted_by,
                        now,
                        self.provider.model_name,
                    ),
                )
                connection.execute(
                    """UPDATE rectification_orders SET status='pending_review',
                       updated_at=? WHERE id=?""",
                    (now, order_id),
                )
                self._record_event(
                    connection,
                    safety_item_id=order["safety_item_id"],
                    event_type="rectification_submitted",
                    title=f"第{attempt}次提交整改证据",
                    actor=submitted_by,
                    at=now,
                    detail=description,
                    payload={"submission_id": submission_id, "attempt_no": attempt},
                )
        except Exception:
            storage_path.unlink(missing_ok=True)
            raise
        finally:
            await upload.close()
        return self.safety_item_detail(order["safety_item_id"])

    def compare_submission(self, submission_id: str) -> None:
        row = self.db.fetch_one(
            """SELECT sub.*, o.requirement, o.safety_item_id,
                      s.fact_description, i.storage_path before_path,
                      i.mime_type before_mime_type
               FROM rectification_submissions sub
               JOIN rectification_orders o ON o.id=sub.order_id
               JOIN safety_items s ON s.id=o.safety_item_id
               JOIN hazard_candidates c ON c.id=s.source_candidate_id
               JOIN hazard_inspections i ON i.id=c.inspection_id
               WHERE sub.id=?""",
            (submission_id,),
        )
        if row is None:
            return
        self.db.execute(
            """UPDATE rectification_submissions SET comparison_status='analyzing',
               comparison_error=NULL WHERE id=?""",
            (submission_id,),
        )
        try:
            result = self.provider.compare(
                before_path=Path(row["before_path"]),
                before_mime_type=row["before_mime_type"],
                after_path=Path(row["storage_path"]),
                after_mime_type=row["mime_type"],
                context={
                    "confirmed_hazard": row["fact_description"],
                    "rectification_requirement": row["requirement"],
                    "rectification_description": row["description"],
                },
            )
            validated = RectificationComparison.model_validate(result)
            self.db.execute(
                """UPDATE rectification_submissions SET comparison_status='ready',
                   comparison_json=?, comparison_error=NULL WHERE id=?""",
                (json.dumps(validated.model_dump(), ensure_ascii=False), submission_id),
            )
        except Exception as exc:
            self.db.execute(
                """UPDATE rectification_submissions SET comparison_status='failed',
                   comparison_error=? WHERE id=?""",
                (_friendly_vision_error(exc), submission_id),
            )

    def submission_image_path(self, submission_id: str) -> tuple[Path, str]:
        row = self.db.fetch_one(
            """SELECT storage_path, mime_type FROM rectification_submissions
               WHERE id=?""",
            (submission_id,),
        )
        if row is None:
            raise KeyError("整改证据不存在")
        path = Path(row["storage_path"])
        if not path.is_file():
            raise FileNotFoundError("整改后图片不存在")
        return path, row["mime_type"]

    def review_rectification(
        self,
        order_id: str,
        *,
        result: str,
        reason: str,
        reviewer_ref: str,
    ) -> dict[str, Any]:
        order = self.db.fetch_one(
            "SELECT * FROM rectification_orders WHERE id=?", (order_id,)
        )
        if order is None:
            raise KeyError("整改任务不存在")
        if order["status"] != "pending_review":
            raise ValueError("当前整改任务不在待复核状态")
        submission = self.db.fetch_one(
            """SELECT * FROM rectification_submissions WHERE order_id=?
               ORDER BY attempt_no DESC LIMIT 1""",
            (order_id,),
        )
        if submission is None:
            raise ValueError("整改任务尚未提交证据")
        reason = reason.strip()
        if result == "return" and len(reason) < 2:
            raise ValueError("退回整改时必须填写原因")
        now = utc_now()
        with self.db.connect() as connection:
            sequence = connection.execute(
                "SELECT COUNT(*) FROM rectification_reviews WHERE order_id=?",
                (order_id,),
            ).fetchone()[0] + 1
            connection.execute(
                """INSERT INTO rectification_reviews
                   (id, order_id, submission_id, sequence_no, result, reason,
                    reviewer_ref, reviewed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    uuid4().hex,
                    order_id,
                    submission["id"],
                    sequence,
                    result,
                    reason,
                    reviewer_ref,
                    now,
                ),
            )
            new_status = "closed" if result == "pass" else "rectifying"
            connection.execute(
                "UPDATE rectification_orders SET status=?, updated_at=? WHERE id=?",
                (new_status, now, order_id),
            )
            if result == "pass":
                connection.execute(
                    "UPDATE safety_items SET status='closed', updated_at=? WHERE id=?",
                    (now, order["safety_item_id"]),
                )
            self._record_event(
                connection,
                safety_item_id=order["safety_item_id"],
                event_type="review_passed" if result == "pass" else "review_returned",
                title="人工复核通过" if result == "pass" else f"第{sequence}次复核退回",
                actor=reviewer_ref,
                at=now,
                detail=reason or "整改证据经人工复核通过",
                payload={"submission_id": submission["id"], "review_sequence": sequence},
            )
            if result == "pass":
                self._record_event(
                    connection,
                    safety_item_id=order["safety_item_id"],
                    event_type="safety_item_closed",
                    title="安全事项已关闭",
                    actor=reviewer_ref,
                    at=now,
                    detail="对应整改任务已完成并通过人工复核",
                )
        return self.safety_item_detail(order["safety_item_id"])
