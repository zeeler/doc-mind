import pytest
from sqlalchemy import inspect
from server.database import get_engine
from server.models.base import Base
from server.models.document import Document
from server.models.job import Job


class TestJobModel:
    def test_job_table_exists(self, tmp_data_dir, monkeypatch):
        monkeypatch.setattr("server.database.DATA_DIR", tmp_data_dir)
        from server.database import reset_engine
        reset_engine()
        Base.metadata.create_all(bind=get_engine())
        insp = inspect(get_engine())
        columns = {c["name"] for c in insp.get_columns("jobs")}
        assert "id" in columns
        assert "document_id" in columns
        assert "job_type" in columns
        assert "priority" in columns
        assert "status" in columns
        assert "progress" in columns

    def test_job_defaults(self, tmp_data_dir, monkeypatch):
        monkeypatch.setattr("server.database.DATA_DIR", tmp_data_dir)
        from server.database import reset_engine
        reset_engine()
        Base.metadata.create_all(bind=get_engine())
        j = Job(document_id="d1", job_type="quick_scan")
        assert j.status == "pending"
        assert j.priority == 5
        assert j.progress == 0
