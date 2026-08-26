from datetime import date
from io import BytesIO

from docx import Document
from fastapi.testclient import TestClient

from backend.app.main import create_app


def _create_task(client: TestClient) -> dict:
    response = client.post(
        "/worker-assistant/messages",
        json={
            "message": "今天下午在12层拆除外墙模板",
            "worker_ref": "项目作业人员",
            "team_ref": "模板班组",
            "use_llm": False,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_safety_log_is_versioned_downloadable_and_uses_current_project_name(test_settings):
    app = create_app(test_settings)
    today = date.today().isoformat()
    with TestClient(app) as client:
        _create_task(client)

        created = client.post("/api/v1/safety-logs", json={"assessment_date": today})
        assert created.status_code == 201, created.text
        first = created.json()
        assert first["project_name"] == "XX综合医院扩建项目"
        assert first["assessment_date"] == today
        assert first["version"] == 1
        assert first["version_created"] is True
        assert first["content"]["summary"]["task_count"] == 1
        assert first["content"]["source_modules"] == [
            "方案审查",
            "工人助手",
            "安全知识图谱",
            "动态风险",
        ]
        assert "web_verification" not in first["content"]

        duplicate = client.post("/api/v1/safety-logs", json={"assessment_date": today})
        assert duplicate.status_code == 200, duplicate.text
        assert duplicate.json()["id"] == first["id"]
        assert duplicate.json()["version_created"] is False

        download = client.get(f"/api/v1/safety-logs/{first['id']}/download")
        assert download.status_code == 200, download.text
        assert download.headers["content-type"].startswith(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        word = Document(BytesIO(download.content))
        text = "\n".join(paragraph.text for paragraph in word.paragraphs)
        assert "XX综合医院扩建项目" in text
        assert "当日高处作业安全日志" in text
        assert "天气摘要：" not in text
        assert "安全学习与培训建议" not in text
        assert "四、规范与事故依据" in text
        assert "这是该日期的首个安全日志版本" not in text

        evidence_table = next(
            table for table in word.tables
            if [cell.text for cell in table.rows[0].cells] == ["类型", "证据摘要", "来源与定位"]
        )
        evidence_types = {row.cells[0].text for row in evidence_table.rows[1:]}
        assert evidence_types <= {
            "标准规范",
            "方案审查",
            "事故案例",
            "施工方案",
            "项目数据",
            "规则依据",
        }
        assert evidence_table.columns[1].width > evidence_table.columns[2].width
        assert evidence_table.columns[2].width > evidence_table.columns[0].width

        catalog = client.get("/api/v1/projects").json()
        project_id = catalog["active_project_id"]
        renamed = client.patch(
            f"/api/v1/projects/{project_id}", json={"name": "武汉市XX区住宅楼施工项目"}
        )
        assert renamed.status_code == 200, renamed.text

        renamed_log = client.post("/api/v1/safety-logs", json={"assessment_date": today})
        assert renamed_log.status_code == 201, renamed_log.text
        second = renamed_log.json()
        assert second["project_name"] == "武汉市XX区住宅楼施工项目"
        assert second["version"] == 2
        assert second["change"]["changed_sources"] == ["项目名称"]


def test_safety_log_lists_history_and_agent_can_generate_it(test_settings):
    app = create_app(test_settings)
    today = date.today().isoformat()
    with TestClient(app) as client:
        _create_task(client)
        response = client.post(
            "/api/v1/agent/messages", json={"message": "生成今天的高处作业安全日志"}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["agent_name"] == "safety_log_agent"
        assert body["metadata"]["tools"] == ["safety_log.generate"]

        history = client.get("/api/v1/safety-logs")
        assert history.status_code == 200
        assert history.json()[0]["assessment_date"] == today
        assert client.get("/api/v1/safety-logs/latest").json()["id"] == history.json()[0]["id"]
