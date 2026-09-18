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
        if not body["findings"]:
            app.state.database.execute(
                "UPDATE audit_items SET result='未说明' WHERE id=?",
                (body["items"][0]["id"],),
            )
            body = client.get(f"/audits/{run_id}").json()
        repository = app.state.repository
        revised = repository.create_audit_run(
            document_id=document_id,
            model_provider="mock",
            model_name=None,
            rule_limit=body["candidate_rule_count"],
        )
        app.state.database.execute(
            "UPDATE audit_runs SET status='completed', completed_at=created_at WHERE id=?",
            (revised["id"],),
        )
        app.state.database.execute(
            """INSERT INTO audit_items
               (id, run_id, rule_id, scene, plan_quote, source_location, issue,
                basis_json, risk_consequence, result, applicability_status,
                applicability_reason, plan_obligation, plan_obligation_reason,
                bundle_id, business_group_key, control_title, suggestion, confidence,
                review_status, model_raw_json, created_at, updated_at)
               SELECT lower(hex(randomblob(16))), ?, rule_id, scene, plan_quote,
                      source_location, issue, basis_json, risk_consequence, '符合',
                      applicability_status, applicability_reason, plan_obligation,
                      plan_obligation_reason, bundle_id, business_group_key,
                      control_title, suggestion, confidence, review_status,
                      model_raw_json, created_at, updated_at
               FROM audit_items WHERE run_id=?""",
            (revised["id"], run_id),
        )
        revision = repository.create_plan_revision(run_id, revised["id"], "测试管理员")
        compared = repository.complete_plan_revision(revision["id"])
        assert compared["status"] == "closed"
        assert compared["comparison"]["resolved_count"] == len(body["findings"])
        assert compared["comparison"]["unresolved_count"] == 0
        partial_source = body["findings"][0]
        app.state.database.execute(
            "UPDATE audit_items SET result='未说明' WHERE run_id=? AND rule_id=?",
            (revised["id"], partial_source["rule_ids"][0]),
        )
        partial = repository.complete_plan_revision(revision["id"])
        partial_detail = next(
            item
            for item in partial["comparison"]["details"]
            if item["finding_id"] == partial_source["id"]
        )
        assert partial["status"] == "needs_revision"
        expected_outcome = (
            "partial" if len(partial_source["rule_ids"]) > 1 else "unresolved"
        )
        assert partial_detail["outcome"] == expected_outcome
        assert partial_detail["remaining_issues"]
        assert partial_detail["remaining_issues"][0]["issue"]
        recent = client.get("/api/v1/audits/recent").json()
        recent_ids = {item["id"] for item in recent}
        assert run_id in recent_ids
        assert revised["id"] not in recent_ids
        source_row = next(item for item in recent if item["id"] == run_id)
        assert source_row["revision_count"] == 1
        assert source_row["revision_status"] == "needs_revision"
        model_calls = client.get(f"/audits/{run_id}/model-calls")
        assert model_calls.status_code == 200
        assert model_calls.json()["summary"]["call_count"] == 0

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
