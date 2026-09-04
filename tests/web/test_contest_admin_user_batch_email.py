#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""``POST /c/{slug}/admin/users/batch/credentials/email`` under the email budget (issue #155).

Contract: the batch stops at the first budget refusal and marks every later
row ``budget_exceeded`` instead of attempting it; the summary and the security
event carry the counts; and in ``queue`` delivery the rows read ``queued``.
"""

from __future__ import annotations

import json
import logging

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema import security_events
from shared.queue_schema import MailJob
from shared.services.email_budget import EmailBudgetPolicy
from shared.services.email_service import EmailConfig, EmailService
from tests.web._contest_admin_test_support import actor_token, admin_on, build_contest_admin_app
from web.models.contest import Contest
from web.models.users import UberAdmin
from web.routes.contest_admin_user_batch import router as batch_router

pytestmark = pytest.mark.asyncio


class _Runtime:
    def __init__(self) -> None:
        self.jobs: list[MailJob] = []
        self.counts: dict[str, int] = {}

    async def enqueue_mail_job(self, job: MailJob, *, ttl_seconds: int) -> None:
        self.jobs.append(job)

    async def eval(self, script: str, numkeys: int, *args: str) -> object | None:
        self.counts[args[0]] = self.counts.get(args[0], 0) + 1
        return [self.counts[args[0]], 600_000]


def _email_service(runtime: _Runtime, *, admin_max: int) -> EmailService:
    """A producer with a runtime: every accepted message is a job handed to it."""
    return EmailService(
        config=EmailConfig(
            default_from_email="noreply@test.example",
            default_from_name="NOCA",
            budget=EmailBudgetPolicy(enabled=True, window_seconds=600, user_max=20, admin_max=admin_max),
        ),
        logger=logging.getLogger(__name__),
        valkey_runtime=runtime,
    )


async def _stub(slug: str) -> dict[str, str]:
    return {"slug": slug}


async def _post_batch(  # type: ignore[no-untyped-def]
    session: AsyncSession, running_contest: Contest, uberadmin: UberAdmin, service: EmailService, rows: int
):
    admin = await admin_on(session, running_contest, uberadmin, "batch-mailer")
    await session.commit()
    app, auth_service = build_contest_admin_app(session, routers=(batch_router,), extra_scoped_stub_names=("view",))
    for name in ("batch_import_submit", "download_batch_results", "contest_login_get"):
        app.add_api_route(f"/stub/{name}/{{slug}}", _stub, name=name, methods=["GET"])
    app.state.email_service = service
    token = actor_token(auth_service, username=admin.username, contest_id=running_contest.id)
    users = [
        {"username": f"team{i}", "fullname": f"Team {i}", "email": f"team{i}@test.example", "password": "Pw1!"}
        for i in range(rows)
    ]
    payload = {
        "contest-slug": running_contest.login_slug,
        "downloadable_users": users,
        "results": [{"username": u["username"], "status": "created"} for u in users],
    }
    async with AsyncClient(transport=ASGITransport(app=app, client=("203.0.113.9", 1)), base_url="http://t") as client:
        client.cookies.set("noca_access_token", token)
        response = await client.post(
            f"/c/{running_contest.login_slug}/admin/users/batch/credentials/email",
            data={"results_json": json.dumps(payload)},
        )
    return response


async def _batch_event(session: AsyncSession) -> dict[str, object]:
    row = (
        await session.execute(
            select(security_events.c.severity, security_events.c["metadata"]).where(
                security_events.c.event_type == "credential_email_batch_completed"
            )
        )
    ).one()
    return {"severity": row[0], **row[1]}


async def test_budget_stops_the_batch_and_marks_the_rest(
    session: AsyncSession, running_contest: Contest, uberadmin: UberAdmin
) -> None:
    runtime = _Runtime()
    service = _email_service(runtime, admin_max=2)

    response = await _post_batch(session, running_contest, uberadmin, service, rows=5)

    assert response.status_code == 200
    assert len(runtime.jobs) == 2
    assert "0 sent, 2 queued, 0 failed, 3 skipped" in response.text
    assert "Email budget exceeded after 2 messages" in response.text
    assert response.text.count(">budget<") == 3
    event = await _batch_event(session)
    assert event["severity"] == "warning"
    assert event["queued"] == 2 and event["skipped"] == 3 and event["budget_exceeded"] is True


async def test_queue_delivery_reports_queued_rows(
    session: AsyncSession, running_contest: Contest, uberadmin: UberAdmin
) -> None:
    runtime = _Runtime()
    service = _email_service(runtime, admin_max=200)

    response = await _post_batch(session, running_contest, uberadmin, service, rows=3)

    assert response.status_code == 200
    assert [job.to_email for job in runtime.jobs] == [f"team{i}@test.example" for i in range(3)]
    assert "0 sent, 3 queued, 0 failed, 0 skipped" in response.text
    assert response.text.count(">queued<") == 3
    # Every row was accepted, so the retry form is not offered again.
    assert "send_batch_credentials_email" not in response.text.split("Download results JSON")[-1]
    event = await _batch_event(session)
    assert event["severity"] == "info" and event["queued"] == 3 and event["budget_exceeded"] is False
