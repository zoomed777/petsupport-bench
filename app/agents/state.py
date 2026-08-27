from __future__ import annotations

import copy
import logging
import pickle
import time
from datetime import datetime
from uuid import uuid4

from app.core.config import get_settings
from app.models.schemas import ConversationData, ConversationStateEnum

logger = logging.getLogger(__name__)

WAITING_TIMEOUT_SECONDS = 600


class ConversationSession:
    def __init__(self) -> None:
        self._store: dict[str, ConversationData] = {}
        self._redis = None
        self._redis_available = False
        self._init_redis()

    def _init_redis(self) -> None:
        settings = get_settings()
        if not settings.redis_url:
            return
        try:
            import redis
            self._redis = redis.from_url(settings.redis_url, decode_responses=False, socket_connect_timeout=2, socket_timeout=5)
            self._redis.ping()
            self._redis_available = True
            logger.info("Redis session store connected: %s", settings.redis_url)
        except Exception as exc:
            self._redis = None
            self._redis_available = False
            logger.warning("Redis unavailable, falling back to in-memory store: %s", exc)

    def _redis_key(self, conversation_id: str) -> str:
        return f"conv:{conversation_id}"

    def create(self, customer_id: str | None = None, order_id: str | None = None) -> ConversationData:
        now = datetime.now().isoformat()
        conv_id = f"conv-{uuid4().hex[:12]}"
        data = ConversationData(
            conversation_id=conv_id,
            status=ConversationStateEnum.IDLE,
            customer_id=customer_id,
            order_id=order_id,
            created_at=now,
            expires_at=self._future_time(3600),
        )
        self._store[conv_id] = data
        self._redis_set(conv_id, data, ttl=3600)
        logger.info("Created conversation %s", conv_id)
        return data

    def load(self, conversation_id: str, customer_id: str | None = None) -> ConversationData | None:
        data = self._store.get(conversation_id)
        if data is None and self._redis_available:
            data = self._redis_get(conversation_id)
            if data is not None:
                self._store[conversation_id] = data
        if data is None:
            return None
        if self._is_expired(data):
            logger.info("Conversation %s expired, invalidating", conversation_id)
            self.invalidate(conversation_id)
            return None
        if customer_id and data.customer_id and data.customer_id != customer_id:
            logger.warning("Isolation violation: conv=%s owner=%s caller=%s",
                           conversation_id, data.customer_id, customer_id)
            return None
        return copy.deepcopy(data)

    def save(self, data: ConversationData) -> None:
        if self._is_expired(data):
            logger.info("Conversation %s expired, cannot save", data.conversation_id)
            return
        expires_in = WAITING_TIMEOUT_SECONDS if data.status == ConversationStateEnum.WAITING_FOR_USER else 3600
        data.expires_at = self._future_time(expires_in)
        deep = copy.deepcopy(data)
        self._store[data.conversation_id] = deep
        self._redis_set(data.conversation_id, deep, ttl=expires_in)

    def peek(self, conversation_id: str) -> ConversationData | None:
        data = self._store.get(conversation_id)
        if data is None and self._redis_available:
            data = self._redis_get(conversation_id)
            if data is not None:
                self._store[conversation_id] = data
        return data

    def invalidate(self, conversation_id: str) -> None:
        self._store.pop(conversation_id, None)
        self._redis_delete(conversation_id)

    def _is_expired(self, data: ConversationData) -> bool:
        if not data.expires_at:
            return False
        try:
            expires = datetime.fromisoformat(data.expires_at).timestamp()
            return time.time() > expires
        except (ValueError, TypeError):
            return False

    def _future_time(self, seconds: int) -> str:
        return datetime.fromtimestamp(time.time() + seconds).isoformat()

    def _redis_set(self, conv_id: str, data: ConversationData, ttl: int) -> None:
        if not self._redis_available:
            return
        try:
            payload = pickle.dumps(data)
            self._redis.setex(self._redis_key(conv_id), ttl, payload)
        except Exception as exc:
            logger.warning("Redis set failed: %s", exc)

    def _redis_get(self, conv_id: str) -> ConversationData | None:
        if not self._redis_available:
            return None
        try:
            payload = self._redis.get(self._redis_key(conv_id))
            if payload is None:
                return None
            ttl = self._redis.ttl(self._redis_key(conv_id))
            data = pickle.loads(payload)
            if ttl > 0:
                data.expires_at = self._future_time(int(ttl))
            return data
        except Exception as exc:
            logger.warning("Redis get failed: %s", exc)
            return None

    def _redis_delete(self, conv_id: str) -> None:
        if not self._redis_available:
            return
        try:
            self._redis.delete(self._redis_key(conv_id))
        except Exception as exc:
            logger.warning("Redis delete failed: %s", exc)


session_manager = ConversationSession()
