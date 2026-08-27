from app.agents.state import ConversationSession
from app.models.schemas import ConversationStateEnum


def test_create_returns_valid_conversation():
    session = ConversationSession()
    conv = session.create(customer_id="C1001", order_id="ORD-001")
    assert conv.conversation_id.startswith("conv-")
    assert conv.customer_id == "C1001"
    assert conv.order_id == "ORD-001"
    assert conv.status == ConversationStateEnum.IDLE
    assert conv.created_at


def test_save_and_load_roundtrip():
    session = ConversationSession()
    conv = session.create(customer_id="C1001")
    session.save(conv)
    loaded = session.load(conv.conversation_id)
    assert loaded is not None
    assert loaded.conversation_id == conv.conversation_id
    assert loaded.customer_id == "C1001"


def test_load_unknown_returns_none():
    session = ConversationSession()
    assert session.load("conv-nonexistent") is None


def test_customer_isolation_blocks_other_users():
    session = ConversationSession()
    conv = session.create(customer_id="C1001")
    session.save(conv)
    assert session.load(conv.conversation_id, "C1001") is not None
    assert session.load(conv.conversation_id, "C1002") is None


def test_customer_isolation_skipped_when_no_customer_id():
    session = ConversationSession()
    conv = session.create()
    session.save(conv)
    assert session.load(conv.conversation_id) is not None


def test_invalidate_removes_conversation():
    session = ConversationSession()
    conv = session.create(customer_id="C1001")
    session.save(conv)
    session.invalidate(conv.conversation_id)
    assert session.load(conv.conversation_id) is None


def test_save_expired_conversation_logs_and_returns():
    session = ConversationSession()
    conv = session.create(customer_id="C1001")
    conv.expires_at = "2020-01-01T00:00:00+00:00"
    session.save(conv)
    assert session.load(conv.conversation_id) is None


def test_load_expired_conversation_returns_none():
    session = ConversationSession()
    conv = session.create(customer_id="C1001")
    conv.expires_at = "2020-01-01T00:00:00+00:00"
    session.save(conv)
    loaded = session.load(conv.conversation_id)
    assert loaded is None
