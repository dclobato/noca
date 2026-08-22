#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Unauthenticated download of a finished contest's full problem set.

Once a contest is over and its problem set has been released, the complete
problem materials (statements, all test cases, validator sources, editorials)
are public. This route bundles them as one ZIP without requiring any login,
which is why it deliberately does not reuse the contest-scoped dependencies:
those resolve an authenticated actor and redirect anonymous visitors to the
login page.

Because the route is anonymous, it is also the most exposed endpoint in Web:
when ``NOCA_WEB_PUBLIC_PROBLEM_PACK_PATH`` is configured, archives are built
once per contest and served from the on-disk cache (see
``web.services.problem_set_cache``), so a burst of downloads costs one build
plus zero database work per hit. Without a cache directory, every download is
built fresh into a temporary file — acceptable for development, not for a
public deployment.
"""

import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import select
from starlette.background import BackgroundTask

from shared.services.problem_package import PackageError
from web.config import settings
from web.models.contest import Contest
from web.services.problem_set_cache import ensure_cached_archive
from web.services.problem_set_service import build_problem_set_archive, problem_set_filename

router = APIRouter(tags=["problem_set"])


async def _load_released_contest(request: Request, slug: str) -> Contest:
    """Return the contest when the problem set is public for ``slug``.

    The contest must be over and carry ``release_problem_set_after_end``. That
    flag is deliberately independent of ``release_scoreboard_after_end`` (which
    still gates the scoreboard and the team submissions download): publishing
    standings and publishing every secret test case, validator source and
    editorial are separate admin decisions.

    The ``is_past`` half is not part of that decoupling. An admin may set the
    flag at any time -- doing so before the end simply arms the publication for
    then -- so this check is what keeps the materials private while the contest
    is still being run.

    A contest that fails the gate answers 404 -- not 403 -- so the route does not
    confirm that an unreleased contest exists. The returned contest is detached
    (the session is closed); only its preloaded scalar attributes are usable
    afterwards.
    """
    async with request.app.state.db_session() as session:
        contest: Contest | None = (
            await session.execute(select(Contest).where(Contest.login_slug == slug, Contest.active))  # noqa: E712
        ).scalar_one_or_none()
        if contest is None or not contest.is_past or not contest.release_problem_set_after_end:
            raise HTTPException(status_code=404)
        # Every attribute the builders touch must be read before the session
        # closes: the contest row travels on its own from here on.
        session.expunge(contest)
        return contest


async def _build_archive(request: Request, slug: str, dest_path: Path) -> None:
    """Build the contest's problem-set archive at ``dest_path``."""
    async with request.app.state.db_session() as session:
        contest = (
            await session.execute(select(Contest).where(Contest.login_slug == slug, Contest.active))  # noqa: E712
        ).scalar_one_or_none()
        if contest is None:
            raise HTTPException(status_code=404)
        await build_problem_set_archive(
            session,
            contest,
            dest_path,
            testcase_dir=Path(settings.PROBLEM_TESTCASE_DIR),
            statement_dir=Path(settings.PROBLEM_STATEMENT_DIR),
        )


@router.get("/problem-set/{slug}.zip", name="problem_set_download")
async def problem_set_download(request: Request, slug: str) -> FileResponse:
    """Stream the contest's public problem-set archive, when released."""
    contest = await _load_released_contest(request, slug)
    filename = problem_set_filename(contest)
    cache_dir = settings.PUBLIC_PROBLEM_PACK_PATH

    if cache_dir is not None:
        try:
            archive_path = await ensure_cached_archive(
                cache_dir,
                contest,
                lambda dest: _build_archive(request, slug, dest),
            )
        except PackageError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        # The archive lives in the cache, so the response must not delete it.
        return FileResponse(archive_path, media_type="application/zip", filename=filename)

    handle, temp_name = tempfile.mkstemp(suffix=".zip", prefix="noca-problem-set-")
    os.close(handle)
    dest_path = Path(temp_name)
    try:
        await _build_archive(request, slug, dest_path)
    except PackageError as exc:
        dest_path.unlink(missing_ok=True)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BaseException:
        dest_path.unlink(missing_ok=True)
        raise

    return FileResponse(
        dest_path,
        media_type="application/zip",
        filename=filename,
        background=BackgroundTask(dest_path.unlink, missing_ok=True),
    )
