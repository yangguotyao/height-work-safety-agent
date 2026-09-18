from fastapi.testclient import TestClient

from backend.app.main import create_app


def test_account_registration_first_project_and_project_switching(test_settings):
    app = create_app(test_settings.model_copy(update={"enforce_auth": True}))
    with TestClient(app) as client:
        assert client.get("/api/v1/projects").status_code == 401
        registered = client.post(
            "/api/v1/auth/register",
            json={
                "username": "new_user",
                "password": "12345",
                "confirm_password": "12345",
            },
        )
        assert registered.status_code == 201, registered.text
        assert registered.json()["user"]["display_name"] == "new_user"
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "new_user", "password": "12345"},
        )
        assert login.status_code == 200, login.text
        assert login.json()["needs_project"] is True

        created = client.post(
            "/api/v1/projects",
            json={"name": "武汉中心项目", "city": "武汉市", "address": "江岸区"},
        )
        assert created.status_code == 201, created.text
        assert created.json()["city"] == "武汉市"

        catalog = client.get("/api/v1/projects").json()
        assert len(catalog["projects"]) == 1
        assert catalog["active_project"]["name"] == "武汉中心项目"
        assert client.get("/api/v1/dashboard").status_code == 200

        second = client.post(
            "/api/v1/projects",
            json={"name": "汉阳施工项目", "city": "武汉市", "address": "汉阳区"},
        )
        assert second.status_code == 201, second.text
        catalog = client.get("/api/v1/projects").json()
        assert {item["name"] for item in catalog["projects"]} == {
            "武汉中心项目",
            "汉阳施工项目",
        }
        assert catalog["active_project_id"] == second.json()["id"]


def test_default_admin_login_keeps_existing_workspace(test_settings):
    app = create_app(test_settings.model_copy(update={"enforce_auth": True}))
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "Admin", "password": "admin"},
        )
        assert login.status_code == 200, login.text
        assert login.json()["needs_project"] is False
        me = client.get("/api/v1/auth/me").json()
        assert me["user"]["username"] == "admin"
        assert me["user"]["project_id"] == "default-project"
