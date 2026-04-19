"""End-to-end tests for the subscription web surface (Phase F.2).

Uses FastAPI's TestClient + a per-test SQLite file so nothing touches
a real database or mail relay. The confirmation-email sender is a
captured callable, so we assert on what WOULD have been sent.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)

from polymarket_insider_tracker.storage.models import Base, SubscriberModel
from polymarket_insider_tracker.web.app import WebConfig, create_app


async def _bootstrap_db(url: str) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


def _fetch_unsubscribe_token(url: str, email: str) -> str:
    """Ad-hoc DB read for the unsubscribe_token the app never exposes."""
    async def _inner() -> str:
        engine = create_async_engine(url)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with factory() as session:
            result = await session.execute(
                select(SubscriberModel).where(SubscriberModel.email == email.lower())
            )
            row = result.scalar_one()
            token = row.unsubscribe_token
        await engine.dispose()
        return token
    return asyncio.run(_inner())


@pytest.fixture
def db_url(tmp_path) -> str:
    url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    asyncio.run(_bootstrap_db(url))
    return url


@pytest.fixture
def sent_confirmations() -> list[dict]:
    return []


@pytest.fixture
def client(db_url: str, sent_confirmations: list[dict]):
    def _fake_sender(payload, opt_in_url, token) -> None:
        sent_confirmations.append(
            {"email": str(payload.email), "opt_in_url": opt_in_url, "token": token}
        )

    app = create_app(
        WebConfig(
            database_url=db_url,
            public_host="newsletter.test",
            confirmation_sender=_fake_sender,
        )
    )
    with TestClient(app) as tc:
        yield tc


class TestSubscribe:
    def test_new_signup_creates_pending_and_sends_confirmation(
        self, client, sent_confirmations
    ):
        response = client.post(
            "/subscribe",
            json={"email": "Alice@Example.COM", "cadences": ["daily"]},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "pending_opt_in"
        assert "inbox" in body["message"].lower()
        assert len(sent_confirmations) == 1
        assert "newsletter.test/opt-in?token=" in sent_confirmations[0]["opt_in_url"]

    def test_duplicate_pending_refreshes_token(self, client, sent_confirmations):
        client.post("/subscribe", json={"email": "b@x.com", "cadences": ["daily"]})
        client.post("/subscribe", json={"email": "b@x.com", "cadences": ["weekly"]})
        # Two confirmation emails with different tokens (old link invalidated).
        assert len({c["token"] for c in sent_confirmations}) == 2

    def test_already_active_signup_is_idempotent(
        self, client, sent_confirmations
    ):
        client.post("/subscribe", json={"email": "c@x.com", "cadences": ["daily"]})
        token = sent_confirmations[-1]["token"]
        client.get(f"/opt-in?token={token}")
        r = client.post("/subscribe", json={"email": "c@x.com", "cadences": ["daily"]})
        assert r.json()["status"] == "active"

    def test_invalid_cadence_rejected(self, client):
        response = client.post(
            "/subscribe", json={"email": "d@x.com", "cadences": ["hourly"]}
        )
        assert response.status_code == 400
        assert "invalid cadence" in response.json()["detail"].lower()

    def test_invalid_email_rejected(self, client):
        response = client.post(
            "/subscribe", json={"email": "not-an-email", "cadences": ["daily"]}
        )
        assert response.status_code == 422


class TestOptIn:
    def test_valid_token_flips_to_active(self, client, sent_confirmations):
        client.post("/subscribe", json={"email": "a@x.com", "cadences": ["daily"]})
        token = sent_confirmations[-1]["token"]
        response = client.get(f"/opt-in?token={token}")
        assert response.status_code == 200
        body = response.json()
        assert body == {"status": "active", "email": "a@x.com"}

    def test_invalid_token_returns_404(self, client):
        response = client.get(
            "/opt-in?token=00000000-0000-0000-0000-000000000000"
        )
        assert response.status_code == 404

    def test_double_click_is_idempotent(self, client, sent_confirmations):
        client.post("/subscribe", json={"email": "a@x.com", "cadences": ["daily"]})
        token = sent_confirmations[-1]["token"]
        r1 = client.get(f"/opt-in?token={token}")
        r2 = client.get(f"/opt-in?token={token}")
        assert r1.status_code == 200 and r2.status_code == 200
        assert r1.json() == r2.json()


class TestUnsubscribe:
    def test_get_flips_status(self, client, db_url, sent_confirmations):
        client.post("/subscribe", json={"email": "a@x.com", "cadences": ["daily"]})
        token = sent_confirmations[-1]["token"]
        client.get(f"/opt-in?token={token}")
        unsub = _fetch_unsubscribe_token(db_url, "a@x.com")
        response = client.get(f"/unsubscribe?token={unsub}")
        assert response.status_code == 200
        assert response.json() == {"status": "unsubscribed", "email": "a@x.com"}

    def test_post_one_click_also_works(self, client, db_url, sent_confirmations):
        client.post("/subscribe", json={"email": "a@x.com", "cadences": ["daily"]})
        token = sent_confirmations[-1]["token"]
        client.get(f"/opt-in?token={token}")
        unsub = _fetch_unsubscribe_token(db_url, "a@x.com")
        response = client.post(f"/unsubscribe?token={unsub}")
        assert response.status_code == 200

    def test_invalid_token_returns_404(self, client):
        response = client.get(
            "/unsubscribe?token=00000000-0000-0000-0000-000000000000"
        )
        assert response.status_code == 404


class TestHealth:
    def test_healthz(self, client):
        assert client.get("/healthz").json() == {"status": "ok"}
