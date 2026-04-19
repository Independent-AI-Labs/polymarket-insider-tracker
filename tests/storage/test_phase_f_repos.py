"""Tests for Phase F repositories (subscribers, suppression, ledger, bounces)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from polymarket_insider_tracker.storage.models import Base
from polymarket_insider_tracker.storage.repos import (
    DEFAULT_BOUNCE_THRESHOLD,
    STATUS_ACTIVE,
    STATUS_BOUNCED,
    STATUS_PENDING,
    STATUS_SUPPRESSED,
    STATUS_UNSUBSCRIBED,
    EmailBounceDTO,
    EmailBounceRepository,
    EmailDeliveryDTO,
    EmailDeliveryRepository,
    SubscriberDTO,
    SubscribersRepository,
    SuppressionEntryDTO,
    SuppressionListRepository,
)


@pytest.fixture
async def async_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(async_engine) -> AsyncSession:
    factory = async_sessionmaker(bind=async_engine, expire_on_commit=False)
    async with factory() as s:
        yield s


@pytest.mark.asyncio
class TestSubscribersRepository:
    async def test_insert_pending_new_row(self, session):
        repo = SubscribersRepository(session)
        dto = await repo.insert_pending(email="Alice@Example.COM", cadences=["daily"])
        assert dto.email == "alice@example.com"
        assert dto.status == STATUS_PENDING
        assert dto.cadences == ["daily"]
        assert dto.opt_in_token and dto.unsubscribe_token
        assert dto.opt_in_token != dto.unsubscribe_token

    async def test_insert_pending_duplicate_refreshes_token(self, session):
        repo = SubscribersRepository(session)
        first = await repo.insert_pending(email="alice@example.com", cadences=["daily"])
        second = await repo.insert_pending(email="alice@example.com", cadences=["weekly"])
        # Same row, new token + cadence update.
        assert first.id == second.id
        assert first.opt_in_token != second.opt_in_token
        assert second.cadences == ["weekly"]

    async def test_insert_pending_active_row_untouched(self, session):
        repo = SubscribersRepository(session)
        dto = await repo.insert_pending(email="a@x.com", cadences=["daily"])
        await repo.confirm_opt_in(dto.opt_in_token)
        # A second /subscribe on an already-active email: the API
        # caller decides how to respond, but the stored token mustn't
        # flip. We capture this by checking the row stays active.
        redo = await repo.insert_pending(email="a@x.com", cadences=["daily"])
        assert redo.status == STATUS_ACTIVE

    async def test_insert_pending_rejects_invalid_cadence(self, session):
        repo = SubscribersRepository(session)
        with pytest.raises(ValueError, match="invalid cadence"):
            await repo.insert_pending(email="a@x.com", cadences=["hourly"])

    async def test_insert_pending_blocks_suppressed(self, session):
        repo = SubscribersRepository(session)
        dto = await repo.insert_pending(email="a@x.com", cadences=["daily"])
        # Forcibly suppress via direct model update to simulate ops action.
        from polymarket_insider_tracker.storage.models import SubscriberModel
        from sqlalchemy import update
        await session.execute(
            update(SubscriberModel)
            .where(SubscriberModel.id == dto.id)
            .values(status=STATUS_SUPPRESSED)
        )
        with pytest.raises(PermissionError, match="suppression"):
            await repo.insert_pending(email="a@x.com", cadences=["daily"])

    async def test_confirm_opt_in_flips_pending_to_active(self, session):
        repo = SubscribersRepository(session)
        dto = await repo.insert_pending(email="a@x.com", cadences=["daily"])
        confirmed = await repo.confirm_opt_in(dto.opt_in_token)
        assert confirmed is not None
        assert confirmed.status == STATUS_ACTIVE
        assert confirmed.opt_in_confirmed_at is not None

    async def test_confirm_opt_in_unknown_token_returns_none(self, session):
        repo = SubscribersRepository(session)
        # Non-existent UUID — must return None without raising and
        # without revealing that the token was invalid (REQ-MAIL-112).
        result = await repo.confirm_opt_in("00000000-0000-0000-0000-000000000000")
        assert result is None

    async def test_confirm_opt_in_is_idempotent(self, session):
        repo = SubscribersRepository(session)
        dto = await repo.insert_pending(email="a@x.com", cadences=["daily"])
        first = await repo.confirm_opt_in(dto.opt_in_token)
        second = await repo.confirm_opt_in(dto.opt_in_token)
        assert first.status == STATUS_ACTIVE
        assert second.status == STATUS_ACTIVE
        # Wall-clock equality — SQLite strips tz info on readback, so
        # compare the naive representations for determinism.
        assert first.opt_in_confirmed_at is not None
        assert second.opt_in_confirmed_at is not None
        assert first.opt_in_confirmed_at.replace(tzinfo=None) == (
            second.opt_in_confirmed_at.replace(tzinfo=None)
        )

    async def test_unsubscribe_flips_any_status(self, session):
        repo = SubscribersRepository(session)
        dto = await repo.insert_pending(email="a@x.com", cadences=["daily"])
        await repo.confirm_opt_in(dto.opt_in_token)
        result = await repo.unsubscribe(dto.unsubscribe_token)
        assert result is not None
        assert result.status == STATUS_UNSUBSCRIBED

    async def test_unsubscribe_unknown_token_returns_none(self, session):
        repo = SubscribersRepository(session)
        result = await repo.unsubscribe("00000000-0000-0000-0000-000000000000")
        assert result is None

    async def test_active_for_cadence_filters_correctly(self, session):
        repo = SubscribersRepository(session)
        d = await repo.insert_pending(email="daily@x.com", cadences=["daily"])
        await repo.confirm_opt_in(d.opt_in_token)
        wm = await repo.insert_pending(email="weekly-monthly@x.com", cadences=["weekly", "monthly"])
        await repo.confirm_opt_in(wm.opt_in_token)
        pending = await repo.insert_pending(email="pending@x.com", cadences=["daily"])
        # pending stays pending_opt_in and is excluded.

        daily_list = await repo.active_for_cadence("daily")
        assert {s.email for s in daily_list} == {"daily@x.com"}
        weekly_list = await repo.active_for_cadence("weekly")
        assert {s.email for s in weekly_list} == {"weekly-monthly@x.com"}
        monthly_list = await repo.active_for_cadence("monthly")
        assert {s.email for s in monthly_list} == {"weekly-monthly@x.com"}

    async def test_record_bounce_hard_increments_and_flips(self, session):
        repo = SubscribersRepository(session)
        dto = await repo.insert_pending(email="a@x.com", cadences=["daily"])
        await repo.confirm_opt_in(dto.opt_in_token)
        for _ in range(DEFAULT_BOUNCE_THRESHOLD - 1):
            updated = await repo.record_bounce(email="a@x.com", bounce_type="hard")
            assert updated.status == STATUS_ACTIVE
        final = await repo.record_bounce(email="a@x.com", bounce_type="hard")
        assert final.status == STATUS_BOUNCED
        assert final.bounce_count == DEFAULT_BOUNCE_THRESHOLD

    async def test_record_bounce_soft_does_not_flip(self, session):
        repo = SubscribersRepository(session)
        dto = await repo.insert_pending(email="a@x.com", cadences=["daily"])
        await repo.confirm_opt_in(dto.opt_in_token)
        for _ in range(10):
            await repo.record_bounce(email="a@x.com", bounce_type="soft")
        row = (await repo.active_for_cadence("daily"))[0]
        assert row.status == STATUS_ACTIVE
        assert row.bounce_count == 0   # soft doesn't count against threshold

    async def test_delete_for_gdpr_removes_row(self, session):
        repo = SubscribersRepository(session)
        await repo.insert_pending(email="a@x.com", cadences=["daily"])
        assert await repo.delete_for_gdpr("a@x.com") is True
        assert await repo.delete_for_gdpr("a@x.com") is False


@pytest.mark.asyncio
class TestSuppressionListRepository:
    async def test_exact_match(self, session):
        repo = SuppressionListRepository(session)
        await repo.add(SuppressionEntryDTO(pattern="bad@x.com", pattern_type="exact"))
        assert await repo.matches("bad@x.com") is not None
        assert await repo.matches("other@x.com") is None

    async def test_domain_match(self, session):
        repo = SuppressionListRepository(session)
        await repo.add(SuppressionEntryDTO(pattern="spam.example", pattern_type="domain"))
        assert await repo.matches("anybody@spam.example") is not None
        assert await repo.matches("anybody@other.example") is None

    async def test_regex_match_skips_malformed(self, session):
        repo = SuppressionListRepository(session)
        await repo.add(SuppressionEntryDTO(pattern=r".*@evil\.test", pattern_type="regex"))
        # Malformed regex must not poison the whole match loop.
        await repo.add(SuppressionEntryDTO(pattern="[unclosed", pattern_type="regex"))
        assert await repo.matches("bot@evil.test") is not None
        assert await repo.matches("user@x.com") is None

    async def test_filter_subscribers_splits_correctly(self, session):
        subs_repo = SubscribersRepository(session)
        supp_repo = SuppressionListRepository(session)
        good = await subs_repo.insert_pending(email="good@x.com", cadences=["daily"])
        bad = await subs_repo.insert_pending(email="bad@spam.example", cadences=["daily"])
        await supp_repo.add(
            SuppressionEntryDTO(
                pattern="spam.example",
                pattern_type="domain",
                reason="bulk abuse",
            )
        )
        allowed, suppressed = await supp_repo.filter_subscribers([good, bad])
        assert [s.email for s in allowed] == ["good@x.com"]
        assert len(suppressed) == 1
        assert suppressed[0][1].reason == "bulk abuse"


@pytest.mark.asyncio
class TestEmailDeliveryAndBounce:
    async def test_delivery_record_roundtrip(self, session):
        repo = EmailDeliveryRepository(session)
        now = datetime.now(UTC)
        dto = await repo.record(
            EmailDeliveryDTO(
                edition_id="daily-2026-04-19",
                cadence="daily",
                email="a@x.com",
                outcome="sent",
                queued_at=now,
                message_id="<abc@relay>",
            )
        )
        assert dto.id is not None
        found = await repo.find_by_message_id("<abc@relay>")
        assert found is not None
        assert found.email == "a@x.com"

    async def test_bounce_record_validates_type(self, session):
        repo = EmailBounceRepository(session)
        with pytest.raises(ValueError, match="invalid bounce_type"):
            await repo.record(
                EmailBounceDTO(
                    email="a@x.com",
                    bounce_type="weird",
                    reported_at=datetime.now(UTC),
                )
            )

    async def test_bounce_record_happy_path(self, session):
        repo = EmailBounceRepository(session)
        dto = await repo.record(
            EmailBounceDTO(
                email="a@x.com",
                bounce_type="hard",
                reported_at=datetime.now(UTC),
                diagnostic="550 5.1.1 user unknown",
            )
        )
        assert dto.id is not None
        assert dto.bounce_type == "hard"
