from backend.app.db import Database
from backend.app.repositories import Repository
from backend.app.services.rule_importer import import_rules


def test_imports_reviewed_rule_workbook(test_settings):
    database = Database(test_settings.resolved_database_path)
    database.initialize()

    imported, skipped = import_rules(database, test_settings.resolved_rule_workbook_path)
    repository = Repository(database)

    assert imported == 301
    assert skipped == 0
    assert repository.count_rules() == 301
    scenes = repository.list_scenes()
    assert "高处作业综合管理" in scenes
    assert "施工脚手架" in scenes
    assert "高处作业吊篮" in scenes
    assert "幕墙安装与验收" in scenes
