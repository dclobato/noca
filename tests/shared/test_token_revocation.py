"""Unit and integration tests for ValkeyRevocationStore and JWTService revocation."""

from __future__ import annotations

import logging
from typing import Any

from shared.services.token_revocation import ValkeyRevocationStore

# ---------------------------------------------------------------------------
# Fake sync Valkey client for unit tests (no real Valkey required)
# ---------------------------------------------------------------------------


class _FakeSyncValkey:
    """Minimal sync Valkey stand-in used by unit tests."""

    def __init__(self, *, fail: bool = False) -> None:
        self._store: dict[str, tuple[str, int | None]] = {}  # key -> (value, ttl)
        self._fail = fail
        self.closed = False

    def exists(self, key: str) -> int:
        if self._fail:
            raise OSError("valkey unavailable")
        return 1 if key in self._store else 0

    def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool | None:
        if self._fail:
            raise OSError("valkey unavailable")
        if nx and key in self._store:
            return None  # NX: not set (already exists)
        self._store[key] = (value, ex)
        return True

    def close(self) -> None:
        self.closed = True

    def ttl(self, key: str) -> int:
        _, ex = self._store.get(key, (None, None))
        return ex if ex is not None else -1


def _make_store_with_fake(fake: _FakeSyncValkey) -> ValkeyRevocationStore:
    """Build a ValkeyRevocationStore with the given fake client injected."""
    store = ValkeyRevocationStore.__new__(ValkeyRevocationStore)
    store._client = fake  # type: ignore[attr-defined]
    store._logger = logging.getLogger("test_revocation")
    return store


# ---------------------------------------------------------------------------
# Unit tests — no real Valkey
# ---------------------------------------------------------------------------


def test_is_revoked_returns_false_for_unknown_jti() -> None:
    fake = _FakeSyncValkey()
    store = _make_store_with_fake(fake)
    assert store.is_revoked("unknown-jti") is False


def test_revoke_stores_key_and_returns_true() -> None:
    fake = _FakeSyncValkey()
    store = _make_store_with_fake(fake)
    result = store.revoke("jti-abc", ttl_seconds=300)
    assert result is True
    assert fake.exists("auth:revoked:jti-abc") == 1


def test_is_revoked_returns_true_after_revoke() -> None:
    fake = _FakeSyncValkey()
    store = _make_store_with_fake(fake)
    store.revoke("jti-xyz", ttl_seconds=60)
    assert store.is_revoked("jti-xyz") is True


def test_revoke_returns_false_on_duplicate() -> None:
    fake = _FakeSyncValkey()
    store = _make_store_with_fake(fake)
    first = store.revoke("jti-dup", ttl_seconds=60)
    second = store.revoke("jti-dup", ttl_seconds=60)
    assert first is True
    assert second is False


def test_revoke_stores_correct_ttl() -> None:
    fake = _FakeSyncValkey()
    store = _make_store_with_fake(fake)
    store.revoke("jti-ttl", ttl_seconds=120)
    assert fake.ttl("auth:revoked:jti-ttl") == 120


def test_revoke_clamps_ttl_to_at_least_one_second() -> None:
    fake = _FakeSyncValkey()
    store = _make_store_with_fake(fake)
    store.revoke("jti-zero", ttl_seconds=0)
    assert fake.ttl("auth:revoked:jti-zero") == 1


def test_is_revoked_fails_open_on_valkey_error() -> None:
    fake = _FakeSyncValkey(fail=True)
    store = _make_store_with_fake(fake)
    # Must return False (fail-open) without raising
    assert store.is_revoked("jti-err") is False


def test_revoke_fails_silently_on_valkey_error() -> None:
    fake = _FakeSyncValkey(fail=True)
    store = _make_store_with_fake(fake)
    result = store.revoke("jti-err", ttl_seconds=60)
    assert result is False


def test_close_delegates_to_client() -> None:
    fake = _FakeSyncValkey()
    store = _make_store_with_fake(fake)
    store.close()
    assert fake.closed is True


def test_key_uses_auth_revoked_prefix() -> None:
    store = ValkeyRevocationStore.__new__(ValkeyRevocationStore)
    store._client = _FakeSyncValkey()  # type: ignore[attr-defined]
    store._logger = logging.getLogger("test")  # type: ignore[attr-defined]
    assert store._key("abc123") == "auth:revoked:abc123"


# ---------------------------------------------------------------------------
# JWTService integration — revocation plugged into the library (no real Valkey)
# ---------------------------------------------------------------------------


def test_jwtservice_validate_rejects_revoked_token_via_store() -> None:
    """JWTService.validate() returns an invalid result after service.revoke()."""
    from jwtservice import JWTService, load_token_config_from_dict

    from web.services.authentication_service import AuthAction

    fake = _FakeSyncValkey()
    store = _make_store_with_fake(fake)

    jwt_service = JWTService(
        config=load_token_config_from_dict({"SECRET_KEY": "test-secret-key-for-revocation-unittest"}),
        logger=logging.getLogger("test"),
        action_enum=AuthAction,
        revocation_store=store,
    )

    token = jwt_service.create(sub="user@test.com", expires_in=3600, action=AuthAction.WEB_ACCESS)

    result_before = jwt_service.validate(token)
    assert result_before.valid is True

    jwt_service.revoke(token, reason="logout")

    result_after = jwt_service.validate(token)
    assert result_after.valid is False


def test_jwtservice_revoke_populates_store() -> None:
    """After JWTService.revoke(), the store contains the JTI entry."""
    from jwtservice import JWTService, load_token_config_from_dict

    from web.services.authentication_service import AuthAction

    fake = _FakeSyncValkey()
    store = _make_store_with_fake(fake)

    jwt_service = JWTService(
        config=load_token_config_from_dict({"SECRET_KEY": "test-secret-key-revoke-populate-store"}),
        logger=logging.getLogger("test"),
        action_enum=AuthAction,
        revocation_store=store,
    )

    token = jwt_service.create(sub="someone", expires_in=300, action=AuthAction.WEB_ACCESS)
    jwt_service.revoke(token, reason="logout")

    # The store should have exactly one revoked entry
    revoked_keys = [k for k in fake._store if k.startswith("auth:revoked:")]
    assert len(revoked_keys) == 1


# ---------------------------------------------------------------------------
# Integration tests — require real Valkey (DB 15, auto-skipped when offline)
# ---------------------------------------------------------------------------


def test_integration_revoke_and_is_revoked(sync_valkey_client: Any) -> None:
    """Round-trip: revoke a JTI and verify is_revoked returns True."""
    store = ValkeyRevocationStore(valkey_url="redis://127.0.0.1:6379/15")
    try:
        revoked = store.revoke("integ-jti-1", ttl_seconds=60)
        assert revoked is True
        assert store.is_revoked("integ-jti-1") is True
    finally:
        store.close()


def test_integration_unknown_jti_not_revoked(sync_valkey_client: Any) -> None:
    """is_revoked returns False for a JTI that was never revoked."""
    store = ValkeyRevocationStore(valkey_url="redis://127.0.0.1:6379/15")
    try:
        assert store.is_revoked("integ-jti-never-set") is False
    finally:
        store.close()


def test_integration_duplicate_revoke_returns_false(sync_valkey_client: Any) -> None:
    """Revoking the same JTI twice returns False on the second call."""
    store = ValkeyRevocationStore(valkey_url="redis://127.0.0.1:6379/15")
    try:
        assert store.revoke("integ-jti-dup", ttl_seconds=60) is True
        assert store.revoke("integ-jti-dup", ttl_seconds=60) is False
    finally:
        store.close()


def test_integration_ttl_is_bounded(sync_valkey_client: Any) -> None:
    """The key TTL set by revoke() is between 1 and the requested value."""
    store = ValkeyRevocationStore(valkey_url="redis://127.0.0.1:6379/15")
    try:
        store.revoke("integ-jti-ttl", ttl_seconds=120)
        raw_ttl = sync_valkey_client.ttl("auth:revoked:integ-jti-ttl")
        assert 1 <= raw_ttl <= 120
    finally:
        store.close()


def test_integration_key_format_uses_correct_prefix(sync_valkey_client: Any) -> None:
    """Revoked tokens are stored under the auth:revoked: prefix."""
    store = ValkeyRevocationStore(valkey_url="redis://127.0.0.1:6379/15")
    try:
        store.revoke("integ-jti-prefix", ttl_seconds=60)
        # Verify the key was written with the expected name
        assert sync_valkey_client.exists("auth:revoked:integ-jti-prefix") == 1
        assert sync_valkey_client.exists("integ-jti-prefix") == 0
    finally:
        store.close()


def test_integration_jwtservice_end_to_end(sync_valkey_client: Any) -> None:
    """Full end-to-end: JWTService with real ValkeyRevocationStore."""
    from jwtservice import JWTService, load_token_config_from_dict

    from web.services.authentication_service import AuthAction

    store = ValkeyRevocationStore(valkey_url="redis://127.0.0.1:6379/15")
    try:
        jwt_service = JWTService(
            config=load_token_config_from_dict({"SECRET_KEY": "integ-test-secret-end-to-end-longer"}),
            logger=logging.getLogger("test"),
            action_enum=AuthAction,
            revocation_store=store,
        )

        token = jwt_service.create(sub="integ@test.com", expires_in=3600, action=AuthAction.WEB_ACCESS)

        assert jwt_service.validate(token).valid is True

        jwt_service.revoke(token, reason="logout")

        assert jwt_service.validate(token).valid is False
    finally:
        store.close()
