from backend.app.db import Database
from backend.app.repositories import Repository
from backend.app.services.model_call_monitor import ModelCallMonitor


def test_records_full_model_input_output_and_usage(tmp_path):
    database = Database(tmp_path / "monitor.sqlite")
    database.initialize()
    repository = Repository(database)
    document = repository.create_document("方案.docx", "hash", "方案.docx")
    run = repository.create_audit_run(document["id"], "openai", "test-model", 0)
    monitor = ModelCallMonitor(database)

    call_id, started = monitor.start(
        run_id=run["id"],
        purpose="scene_identification",
        attempt=1,
        provider="openai",
        model_name="test-model",
        request={"messages": [{"role": "user", "content": "完整输入"}]},
        metadata={"batch": 1},
    )
    monitor.succeed(
        call_id,
        started,
        {
            "content": '{"instances":[]}',
            "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
        },
    )

    listed = monitor.list_for_run(run["id"])
    detail = monitor.get_call(call_id)
    assert listed["summary"]["call_count"] == 1
    assert listed["summary"]["total_tokens"] == 13
    assert detail["request"]["messages"][0]["content"] == "完整输入"
    assert detail["response"]["content"] == '{"instances":[]}'


def test_distinguishes_http_parse_validation_and_persistence(tmp_path):
    database = Database(tmp_path / "monitor-stages.sqlite")
    database.initialize()
    repository = Repository(database)
    document = repository.create_document("方案.docx", "hash", "方案.docx")
    run = repository.create_audit_run(document["id"], "openai", "test-model", 0)
    monitor = ModelCallMonitor(database)
    call_id, started = monitor.start(
        run_id=run["id"],
        purpose="rule_bundle_audit",
        attempt=1,
        provider="openai",
        model_name="test-model",
        request={"messages": []},
    )

    monitor.http_completed(call_id, started, {"content": "{}", "usage": {}})
    monitor.mark_parse(call_id)
    monitor.mark_validation(call_id, partial=True)
    monitor.mark_persisted(call_id)

    detail = monitor.get_call(call_id)
    assert detail["status"] == "persisted"
    assert detail["http_status"] == "succeeded"
    assert detail["parse_status"] == "succeeded"
    assert detail["validation_status"] == "partial"
    assert detail["persistence_status"] == "succeeded"
