from fastapi.testclient import TestClient

from backend.app.main import create_app


def test_complete_backend_audit_flow_with_rag(test_settings, sample_docx):
    app = create_app(test_settings)

    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["rule_count"] == 301
        assert health.json()["standard_count"] == 11
        assert health.json()["vector_chunk_count"] > 500

        with sample_docx.open("rb") as source:
            upload = client.post(
                "/documents",
                files={
                    "file": (
                        sample_docx.name,
                        source,
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
                },
            )
        assert upload.status_code == 201
        document_id = upload.json()["id"]

        parsed = client.post(f"/documents/{document_id}/parse")
        assert parsed.status_code == 200
        assert parsed.json()["segment_count"] > 100
        markdown = client.get(f"/documents/{document_id}/markdown")
        assert markdown.status_code == 200
        assert markdown.json()["char_count"] > 1000
        assert "#" in markdown.json()["markdown"]

        created = client.post("/audits", json={"document_id": document_id, "use_llm": False})
        assert created.status_code == 202
        run_id = created.json()["id"]

        audit = client.get(f"/audits/{run_id}")
        assert audit.status_code == 200
        body = audit.json()
        assert body["status"] == "completed"
        assert body["scenes"]
        assert body["scene_instances"]
        assert body["candidate_rule_count"] > 0
        assert body["completed_rules"] == body["candidate_rule_count"]
        assert 0 < body["bundle_count"] < body["candidate_rule_count"]
        assert body["completed_bundles"] == body["bundle_count"]
        assert body["model_call_count"] <= body["bundle_count"]
        assert len(body["items"]) == body["candidate_rule_count"]
        assert len(body["findings"]) < len(body["items"])
        assert all(
            finding["result"] in {"不符合", "未说明"}
            for finding in body["findings"]
        )
        assert all(item["review_status"] == "not_required" for item in body["items"])
        assert all(item["basis"] and item["evidences"] for item in body["items"])
        assert body["total_elapsed_seconds"] >= body["elapsed_seconds"] >= 0
        assert all(
            any(entry.get("chunk_id") for entry in item["basis"]) for item in body["items"]
        )
        model_calls = client.get(f"/audits/{run_id}/model-calls")
        assert model_calls.status_code == 200
        assert model_calls.json()["summary"]["call_count"] == 0

        gold_test = client.post("/gold-tests/run", json={"audit_run_id": run_id})
        assert gold_test.status_code == 200
        gold_body = gold_test.json()
        assert gold_body["total_gold"] > 0
        assert "《高支模专项方案》" in gold_body["gold_source"]
        assert gold_body["generated_items"] == len(body["items"])
        assert len(gold_body["details"]) == gold_body["total_gold"]

        stored_test = client.get(f"/gold-tests/{gold_body['id']}")
        assert stored_test.status_code == 200
        assert stored_test.json()["details"] == gold_body["details"]


def test_rejects_invalid_legacy_doc_upload(test_settings):
    app = create_app(test_settings)
    with TestClient(app) as client:
        response = client.post(
            "/documents",
            files={"file": ("旧方案.doc", b"not-a-docx", "application/msword")},
        )
    assert response.status_code == 400
    assert "文件内容不匹配" in response.json()["detail"]


def test_one_click_legacy_doc_audit(test_settings, sample_doc):
    app = create_app(test_settings)
    with TestClient(app) as client:
        with sample_doc.open("rb") as source:
            response = client.post(
                "/audit-documents",
                files={"file": (sample_doc.name, source, "application/msword")},
                data={"use_llm": "false"},
            )
        assert response.status_code == 202, response.text
        run_id = response.json()["id"]
        audit = client.get(f"/audits/{run_id}").json()
        assert audit["status"] == "completed"
        assert audit["candidate_rule_count"] > 0
        assert len(audit["items"]) == audit["candidate_rule_count"]

        document = client.get(f"/documents/{audit['document_id']}").json()
        assert document["status"] == "parsed"
        assert any("Microsoft Word" in warning for warning in document["parse_warnings"])
