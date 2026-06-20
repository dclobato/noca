#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

import json
from collections.abc import Mapping, Sequence
from typing import cast

from fastapi import Request
from fastapi.responses import HTMLResponse, Response

from web.config import settings


def _html(response: object) -> HTMLResponse:
    return cast(HTMLResponse, response)


def _clean_export_user(user_data: Mapping[str, str | None]) -> dict[str, str]:
    return {key: value for key, value in user_data.items() if value is not None}


def _credentials_payload(slug: str, users: Sequence[Mapping[str, str | None]]) -> str:
    return json.dumps(
        {"contest-slug": slug, "users": [_clean_export_user(user) for user in users]},
        ensure_ascii=True,
        indent=2,
    )


def _build_contest_login_url(request: Request, slug: str) -> str:
    url = request.url_for("contest_login_get", slug=slug)
    if settings.WEB_URL_BASE:
        return settings.WEB_URL_BASE + str(url.path)
    return str(url)


def _render_download_json(content: str, filename: str) -> Response:
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _friendly_user_create_error(exc: Exception) -> str:
    text = str(exc)
    if "uq_users_contest_username" in text or "duplicate key value violates unique constraint" in text:
        return "A user with that username already exists in this contest."
    return "Could not create user. Please review the submitted data and try again."
