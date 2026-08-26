from pathlib import Path

import chromadb
from fastapi.testclient import TestClient

from backend.app.main import create_app


def _collection_names(path: Path) -> set[str]:
    client = chromadb.PersistentClient(path=str(path))
    return {str(getattr(item, "name", item)) for item in client.list_collections()}


def test_delete_inactive_project_removes_catalog_files_and_vector_collection(
    test_settings,
):
    app = create_app(test_settings)
    with TestClient(app) as client:
        default_id = client.get("/api/v1/projects").json()["active_project_id"]
        created = client.post(
            "/api/v1/projects", json={"name": "武汉市江岸住宅楼施工项目"}
        ).json()
        client.post(f"/api/v1/projects/{created['id']}/activate").raise_for_status()

        project = app.state.workspace_manager.get(created["id"])
        workspace_dir = Path(project["database_path"]).parent
        upload_dir = Path(project["upload_dir"])
        upload_dir.mkdir(parents=True, exist_ok=True)
        (upload_dir / "待删除资料.txt").write_text("project data", encoding="utf-8")
        collection_name = project["standard_collection"]
        assert workspace_dir.exists()
        assert collection_name in _collection_names(test_settings.resolved_chroma_dir)

        client.post(f"/api/v1/projects/{default_id}/activate").raise_for_status()
        response = client.delete(f"/api/v1/projects/{created['id']}")

        assert response.status_code == 200, response.text
        assert response.json()["deleted_project"]["id"] == created["id"]
        assert not workspace_dir.exists()
        assert created["id"] not in {
            item["id"] for item in client.get("/api/v1/projects").json()["projects"]
        }
        assert collection_name not in _collection_names(test_settings.resolved_chroma_dir)


def test_delete_rejects_current_project(test_settings):
    app = create_app(test_settings)
    with TestClient(app) as client:
        catalog = client.get("/api/v1/projects").json()
        response = client.delete(f"/api/v1/projects/{catalog['active_project_id']}")

    assert response.status_code == 400
    assert response.json()["detail"] == "当前项目不能直接删除，请先切换到其他项目"


def test_rename_active_project_preserves_workspace_data(test_settings):
    app = create_app(test_settings)
    with TestClient(app) as client:
        catalog = client.get("/api/v1/projects").json()
        project_id = catalog["active_project_id"]
        database_path = app.state.settings.resolved_database_path
        response = client.patch(
            f"/api/v1/projects/{project_id}",
            json={"name": "武汉市XX区智慧写字楼施工项目"},
        )

        assert response.status_code == 200, response.text
        assert response.json()["name"] == "武汉市XX区智慧写字楼施工项目"
        assert response.json()["active"] is True
        assert app.state.settings.resolved_database_path == database_path
        assert app.state.active_workspace["name"] == "武汉市XX区智慧写字楼施工项目"
        assert client.get("/api/v1/dashboard").json()["project"] == response.json()["name"]


def test_rename_project_rejects_duplicate_name(test_settings):
    app = create_app(test_settings)
    with TestClient(app) as client:
        current = client.get("/api/v1/projects").json()["active_project"]
        created = client.post(
            "/api/v1/projects", json={"name": "武汉市XX区住宅楼施工项目"}
        ).json()
        response = client.patch(
            f"/api/v1/projects/{created['id']}", json={"name": current["name"]}
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "项目名称已存在"
