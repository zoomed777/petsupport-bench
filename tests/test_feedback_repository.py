import pytest
from pathlib import Path
import tempfile


@pytest.fixture
def repo():
    from app.services.feedback_repository import FeedbackRepository
    from app.core.config import get_settings

    original = get_settings().feedback_db_path
    tmp = Path(tempfile.mktemp(suffix=".db"))
    get_settings().feedback_db_path = tmp
    r = FeedbackRepository()
    yield r
    get_settings().feedback_db_path = original
    if tmp.exists():
        tmp.unlink()


class TestFeedbackSaveAnalysis:
    def test_save_and_list(self, repo):
        from app.models.schemas import AnalysisRecord
        record = AnalysisRecord(
            ticket_id="T-001",
            customer_id="C1",
            order_id="O1",
            message="我的订单没收到",
            category="logistics",
            priority="high",
            confidence=0.85,
            reply_draft="已为您查询物流",
            should_escalate=False,
            source="react_agent",
            prompt_version="v1",
            created_at="2026-05-24T00:00:00",
        )
        repo.save_analysis(record)
        records, total = repo.list_analyses()
        assert total == 1
        assert records[0]["ticket_id"] == "T-001"
        assert records[0]["category"] == "logistics"

    def test_save_multiple(self, repo):
        from app.models.schemas import AnalysisRecord
        for i in range(3):
            repo.save_analysis(AnalysisRecord(
                ticket_id=f"T-{i:03d}", customer_id="C1", message=f"msg{i}",
                category="other", priority="normal", confidence=0.5,
                reply_draft="ok", should_escalate=False, source="react_agent",
                created_at="2026-05-24T00:00:00",
            ))
        records, total = repo.list_analyses(limit=10)
        assert total == 3
        assert len(records) == 3


class TestFeedbackSatisfaction:
    def test_save_satisfaction(self, repo):
        from app.models.schemas import AnalysisRecord
        repo.save_analysis(AnalysisRecord(
            ticket_id="T-001", customer_id="C1", message="test",
            category="other", priority="normal", confidence=0.5,
            reply_draft="ok", should_escalate=False, source="react_agent",
            created_at="2026-05-24T00:00:00",
        ))
        repo.save_satisfaction("T-001", True)
        metrics = repo.satisfaction_metrics()
        assert metrics.total == 1
        assert metrics.satisfied_count == 1
        assert metrics.satisfaction_rate == 1.0

class TestFeedbackGetByCategory:
    def test_get_recent_by_category(self, repo):
        from app.models.schemas import AnalysisRecord
        repo.save_analysis(AnalysisRecord(
            ticket_id="T-001", customer_id="C1", message="test",
            category="refund", priority="normal", confidence=0.5,
            reply_draft="ok", should_escalate=False, source="react_agent",
            created_at="2026-05-24T00:00:00",
        ))
        results = repo.get_recent_by_category("refund")
        assert len(results) >= 1
        assert results[0]["category"] == "refund"

    def test_get_recent_by_category_no_match(self, repo):
        results = repo.get_recent_by_category("nonexistent")
        assert results == []
