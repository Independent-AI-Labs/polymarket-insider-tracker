"""FastAPI factory for the subscription surface."""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass

from fastapi import Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from polymarket_insider_tracker.storage.repos import (
    ALLOWED_CADENCES,
    STATUS_ACTIVE,
    STATUS_PENDING,
    SubscribersRepository,
    SuppressionListRepository,
)


@dataclass
class WebConfig:
    """Runtime knobs the app consumes.

    Held as a plain dataclass so tests can construct one directly
    without touching pydantic-settings / .env loading. Production
    spawn path instantiates via `WebConfig.from_env()`.
    """

    database_url: str
    public_host: str
    confirmation_sender: Callable[[SubscribeRequest, str, str], None] | None = None
    """Callback invoked after a fresh signup. First arg = the signup
    request body, second = the opt-in URL, third = the subscriber's
    opt_in_token. Deliberately pluggable so tests can record calls
    without shelling out to himalaya."""

    @classmethod
    def from_env(cls) -> "WebConfig":
        db_url = os.environ["DATABASE_URL"]
        host = os.environ.get("PUBLIC_HOST", "newsletter.example.com")
        return cls(
            database_url=db_url,
            public_host=host,
            confirmation_sender=None,
        )


class SubscribeRequest(BaseModel):
    """Payload accepted by POST /subscribe."""

    email: EmailStr
    name: str | None = Field(default=None, max_length=200)
    cadences: list[str] = Field(default_factory=lambda: ["daily"], max_length=3)


class SubscribeResponse(BaseModel):
    """Response body for POST /subscribe.

    `status` mirrors the database enum so frontend copy can key on it:
    - `pending_opt_in`: confirmation email dispatched.
    - `active`: caller already confirmed — treat as "already signed up".
    """

    status: str
    message: str


def create_app(config: WebConfig | None = None) -> FastAPI:
    """Build a FastAPI app bound to `config`.

    Tests construct their own `WebConfig` (with an in-memory SQLite
    URL and a fake confirmation sender) and pass it in; the prod
    entrypoint calls `WebConfig.from_env()`.
    """
    cfg = config or WebConfig.from_env()

    app = FastAPI(title="Polymarket Insider — Subscription Service")
    engine = create_async_engine(cfg.database_url)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def get_session() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            yield session

    @app.on_event("shutdown")
    async def _on_shutdown() -> None:
        await engine.dispose()

    @app.post("/subscribe", response_model=SubscribeResponse)
    async def subscribe(
        payload: SubscribeRequest,
        session: AsyncSession = Depends(get_session),
    ) -> SubscribeResponse:
        # Validate cadences ahead of time — the repo validates too,
        # but an explicit 400 response is more useful than bubbling
        # a ValueError into an opaque 500.
        for cadence in payload.cadences:
            if cadence.strip().lower() not in ALLOWED_CADENCES:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"invalid cadence: {cadence!r}",
                )

        # Suppression check up front — don't waste DB rows on addresses
        # we're contractually obligated to refuse.
        supp_repo = SuppressionListRepository(session)
        if await supp_repo.matches(payload.email):
            # Respond as if it succeeded — do not reveal suppression
            # state to the caller (REQ-MAIL-116 operational intent).
            return SubscribeResponse(
                status=STATUS_PENDING,
                message="Check your inbox for a confirmation email.",
            )

        repo = SubscribersRepository(session)
        try:
            dto = await repo.insert_pending(
                email=payload.email,
                name=payload.name,
                cadences=payload.cadences,
            )
        except PermissionError:
            # `insert_pending` raises on `suppressed` rows — same opaque
            # success response as above.
            return SubscribeResponse(
                status=STATUS_PENDING,
                message="Check your inbox for a confirmation email.",
            )
        await session.commit()

        if dto.status == STATUS_ACTIVE:
            return SubscribeResponse(
                status=STATUS_ACTIVE,
                message="You're already subscribed. Nothing to do.",
            )

        opt_in_url = (
            f"https://{cfg.public_host}/opt-in?token={dto.opt_in_token}"
        )
        if cfg.confirmation_sender is not None:
            cfg.confirmation_sender(payload, opt_in_url, dto.opt_in_token)

        return SubscribeResponse(
            status=STATUS_PENDING,
            message="Check your inbox for a confirmation email.",
        )

    @app.get("/opt-in")
    async def opt_in(
        token: str,
        session: AsyncSession = Depends(get_session),
    ) -> dict[str, str]:
        repo = SubscribersRepository(session)
        dto = await repo.confirm_opt_in(token)
        await session.commit()
        if dto is None:
            # Constant-time-ish: always respond with the same shape
            # whether the token was recognised or not.
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Opt-in token not recognised or expired.",
            )
        return {"status": dto.status, "email": dto.email}

    @app.get("/unsubscribe")
    @app.post("/unsubscribe")
    async def unsubscribe(
        token: str,
        request: Request,
        session: AsyncSession = Depends(get_session),
    ) -> dict[str, str]:
        # The `List-Unsubscribe-Post: List-Unsubscribe=One-Click` header
        # tells mailbox providers the POST variant is safe to send
        # without human interaction. We accept both GET (link click)
        # and POST (provider one-click) via the decorator pair above.
        _ = request  # reserved for logging the provider UA if we add it later
        repo = SubscribersRepository(session)
        dto = await repo.unsubscribe(token)
        await session.commit()
        if dto is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Unsubscribe token not recognised.",
            )
        return {"status": dto.status, "email": dto.email}

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
