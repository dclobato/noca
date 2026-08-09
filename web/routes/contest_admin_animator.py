#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Contest-admin page for animator access, medal bands, and operator secrets.

This is a dedicated admin surface so the already large metadata page does not
absorb another responsibility. It reuses the existing ``get_contest_admin_context``
authorization and the ``site_service`` animator wrappers; it never opens a second
authentication path and never renders a secret digest. A freshly generated
operator token is shown exactly once, directly in the POST response, and is never
persisted or flashed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, TypedDict

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from pydantic import BaseModel, Field, ValidationError, model_validator

from shared.services import animator_access_service
from shared.services.admin_audit import record_admin_action
from shared.services.animator_access_service import AnimatorAccessError, SiteSecretMetadata
from web.dependencies import ContestAdminContext, get_contest_admin_context
from web.models.site import Site
from web.routes.contest_admin_helpers import _html
from web.services.contest_service import update_contest_global_medals
from web.services.site_service import (
    create_global_secret,
    create_site_secret,
    delete_site_secret,
    get_site_in_contest,
    list_contest_sites,
    update_site_medals,
)
from web.services.user_credentials_email_service import (
    build_animator_credential_email_content,
    send_credentials_email,
)

router = APIRouter(prefix="/c/{slug}/admin/animator", tags=["contest_admin"])


def _first_error_message(exc: ValidationError) -> str:
    """Return a human-readable message for the first validation error.

    Cutoff parsing failures (non-numeric input) are reported with a stable
    message; explicit ``ValueError`` messages raised by the ordering validator
    are surfaced verbatim.
    """
    for error in exc.errors():
        if error.get("type") in {"value_error", "assertion_error"}:
            message = error.get("msg", "")
            return message.removeprefix("Value error, ") or "Invalid medal settings."
    return "Medal cutoffs must be positive whole numbers."


class MedalSettingsInput(BaseModel):
    """Validated medal cutoffs for one site.

    The model itself enforces the full contract: positive cutoffs ordered
    ``gold <= silver <= bronze``, so invalid input never reaches the service
    layer.
    """

    gold_cutoff: int = Field(ge=1)
    silver_cutoff: int = Field(ge=1)
    bronze_cutoff: int = Field(ge=1)

    @model_validator(mode="after")
    def _check_cutoff_ordering(self) -> MedalSettingsInput:
        """Require the medal bands to be ordered gold <= silver <= bronze."""
        if not (self.gold_cutoff <= self.silver_cutoff <= self.bronze_cutoff):
            raise ValueError("Medal cutoffs must satisfy gold <= silver <= bronze.")
        return self


class GlobalMedalSettingsInput(BaseModel):
    """Validated contest-level (global) medal cutoffs, all-or-nothing.

    Global medals are optional: all three cutoffs blank means the contest-wide
    scoreboard and reveal ceremony show no medals. Validation is delegated to
    the shared ``validate_optional_cutoffs`` so this boundary and the service
    layer can never drift.
    """

    global_gold_cutoff: int | None = None
    global_silver_cutoff: int | None = None
    global_bronze_cutoff: int | None = None

    @model_validator(mode="before")
    @classmethod
    def _blank_means_unset(cls, data: object) -> object:
        """Map blank or whitespace-only form values to ``None``.

        Pydantic does not coerce ``""`` to ``None`` for ``int | None``; it raises
        an integer-parsing error instead. Without this the "clear the fields"
        path would fail validation rather than disable global medals.
        """
        if not isinstance(data, dict):
            return data
        return {key: (None if isinstance(value, str) and not value.strip() else value) for key, value in data.items()}

    @model_validator(mode="after")
    def _check_optional_cutoffs(self) -> GlobalMedalSettingsInput:
        """Require all three cutoffs together, positive and correctly ordered."""
        try:
            animator_access_service.validate_optional_cutoffs(
                self.global_gold_cutoff,
                self.global_silver_cutoff,
                self.global_bronze_cutoff,
            )
        except AnimatorAccessError as exc:
            raise ValueError(str(exc)) from exc
        return self


class SecretLabelInput(BaseModel):
    """Validated human-readable label for an operator secret."""

    label: str = Field(min_length=1, max_length=200)


class OperatorRow(TypedDict):
    """Digest-free operator credential row rendered by Jinja templates."""

    id: str
    label: str
    scope_label: str
    is_global: bool
    created_at: datetime


async def _render_settings(
    request: Request,
    ctx: ContestAdminContext,
) -> HTMLResponse:
    """Render the animator settings page with digest-free credential metadata.

    Args:
        request: Incoming request.
        ctx: Resolved contest-admin context.
    Returns:
        The rendered ``admin/animator_settings.html`` response.
    """
    templates = request.app.state.templates
    operators_context = await _build_operators_context(ctx)
    return _html(
        templates.TemplateResponse(
            request,
            "admin/animator_settings.html",
            {
                "current_user": ctx.actor,
                "contest": ctx.contest,
                **operators_context,
            },
        )
    )


async def _build_operators_context(ctx: ContestAdminContext) -> dict[str, object]:
    """Build ordered site and credential rows for animator templates.

    Args:
        ctx: Resolved contest-admin context.

    Returns:
        Template context with sites and global-first credential rows.
    """
    sites = await list_contest_sites(ctx.session, ctx.contest.id)
    all_secrets = await animator_access_service.list_site_secrets(ctx.session, ctx.contest.id)
    global_secrets = [secret for secret in all_secrets if secret.site_id is None]
    secrets_by_site: dict[str, list[SiteSecretMetadata]] = {site.id: [] for site in sites}
    for secret in all_secrets:
        if secret.site_id in secrets_by_site:
            secrets_by_site[secret.site_id].append(secret)

    operator_rows: list[OperatorRow] = [
        _operator_row(secret, scope_label="Global", is_global=True) for secret in global_secrets
    ]
    for site in sites:
        operator_rows.extend(
            _operator_row(secret, scope_label=site.sitename, is_global=False) for secret in secrets_by_site[site.id]
        )
    return {"sites": sites, "operator_rows": operator_rows}


def _operator_row(
    secret: SiteSecretMetadata,
    *,
    scope_label: str,
    is_global: bool,
) -> OperatorRow:
    """Convert safe credential metadata into a template row.

    Args:
        secret: Digest-free credential metadata.
        scope_label: Human-readable global or site scope.
        is_global: Whether the credential covers all contest sites.

    Returns:
        A template-friendly credential row.
    """
    return {
        "id": secret.id,
        "label": secret.label,
        "scope_label": scope_label,
        "is_global": is_global,
        "created_at": secret.created_at,
    }


async def _render_operators_partial(
    request: Request,
    ctx: ContestAdminContext,
    *,
    new_secret: str | None = None,
    new_secret_label: str | None = None,
    emailed_to: str | None = None,
    email_failed: bool = False,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    """Render the self-replacing animator operators partial.

    Args:
        request: Incoming request.
        ctx: Resolved contest-admin context.
        new_secret: One-time plaintext token to display.
        new_secret_label: Label associated with the new token.
        emailed_to: Address that received a copy of the token.
        email_failed: Whether non-blocking email delivery failed.
        error: Inline mutation error to display.
        status_code: HTTP response status.

    Returns:
        The rendered ``admin/animator_operators.html`` partial.
    """
    context = await _build_operators_context(ctx)
    context.update(
        {
            "contest": ctx.contest,
            "new_secret": new_secret,
            "new_secret_label": new_secret_label,
            "emailed_to": emailed_to,
            "email_failed": email_failed,
            "error": error,
        }
    )
    return _html(
        request.app.state.templates.TemplateResponse(
            request,
            "admin/animator_operators.html",
            context,
            status_code=status_code,
        )
    )


@router.get("/", response_class=HTMLResponse, name="animator_settings")
async def animator_settings(
    request: Request,
    ctx: Annotated[ContestAdminContext, Depends(get_contest_admin_context)],
) -> HTMLResponse:
    """Render animator settings and operator-credential metadata."""
    return await _render_settings(request, ctx)


@router.post("/settings", response_model=None, name="animator_update_settings")
async def update_settings(
    request: Request,
    flash: FlashDep,
    ctx: Annotated[ContestAdminContext, Depends(get_contest_admin_context)],
    animator_enabled: Annotated[str, Form()] = "no",
) -> Response:
    """Enable or disable animator exposure for the contest."""
    enabled = animator_enabled == "yes"
    ctx.contest.animator_enabled = enabled
    await record_admin_action(
        ctx.session,
        request,
        module="web",
        actor_user_id=ctx.actor.id,
        actor_label=ctx.actor.username,
        action="animator_toggle",
        target_type="contest",
        target_id=ctx.contest.id,
        detail=f"animator_enabled={enabled}",
    )
    await ctx.session.commit()
    flash("Animator settings saved.", FlashCategory.SUCCESS)
    return RedirectResponse(request.url_for("animator_settings", slug=ctx.contest.login_slug), status_code=303)


@router.post("/medals", response_model=None, name="animator_update_all_medals")
async def update_all_medals(
    request: Request,
    flash: FlashDep,
    ctx: Annotated[ContestAdminContext, Depends(get_contest_admin_context)],
) -> Response:
    """Validate and update the global and every site's medal cutoffs atomically.

    Everything is validated before anything is written, and all writes land in a
    single commit, so a rejected site cannot leave the global cutoffs changed.
    """
    redirect = RedirectResponse(request.url_for("animator_settings", slug=ctx.contest.login_slug), status_code=303)
    form = await request.form()

    # A form that carries none of the global fields is left alone rather than
    # read as "clear them": only an explicit blank triple disables global medals.
    global_field_names = ("global_gold_cutoff", "global_silver_cutoff", "global_bronze_cutoff")
    global_payload: GlobalMedalSettingsInput | None = None
    if any(field_name in form for field_name in global_field_names):
        try:
            global_payload = GlobalMedalSettingsInput.model_validate(
                {field_name: form.get(field_name, "") for field_name in global_field_names}
            )
        except ValidationError as exc:
            flash(f"Global medals: {_first_error_message(exc)}", FlashCategory.DANGER)
            return redirect

    sites = await list_contest_sites(ctx.session, ctx.contest.id)
    validated: list[tuple[Site, MedalSettingsInput]] = []
    for site in sites:
        field_names = {
            "gold_cutoff": f"gold_cutoff_{site.id}",
            "silver_cutoff": f"silver_cutoff_{site.id}",
            "bronze_cutoff": f"bronze_cutoff_{site.id}",
        }
        if any(field_name not in form for field_name in field_names.values()):
            flash(
                f"Medal settings are missing for site {site.sitename}. Reload the page and try again.",
                FlashCategory.DANGER,
            )
            return redirect
        try:
            payload = MedalSettingsInput.model_validate(
                {field: form[field_name] for field, field_name in field_names.items()}
            )
        except ValidationError as exc:
            flash(f"{site.sitename}: {_first_error_message(exc)}", FlashCategory.DANGER)
            return redirect
        validated.append((site, payload))

    if global_payload is not None:
        try:
            await update_contest_global_medals(
                ctx.session,
                ctx.contest,
                global_payload.global_gold_cutoff,
                global_payload.global_silver_cutoff,
                global_payload.global_bronze_cutoff,
            )
        except AnimatorAccessError as exc:
            await ctx.session.rollback()
            flash(f"Global medals: {exc}", FlashCategory.DANGER)
            return redirect

    for site, payload in validated:
        site_name = site.sitename
        try:
            await update_site_medals(
                ctx.session,
                site,
                payload.gold_cutoff,
                payload.silver_cutoff,
                payload.bronze_cutoff,
            )
        except AnimatorAccessError as exc:
            await ctx.session.rollback()
            flash(f"{site_name}: {exc}", FlashCategory.DANGER)
            return redirect
    await ctx.session.commit()
    flash("Medal settings saved.", FlashCategory.SUCCESS)
    return redirect


@router.post("/secrets", response_class=HTMLResponse, name="animator_create_secret")
async def create_secret(
    request: Request,
    ctx: Annotated[ContestAdminContext, Depends(get_contest_admin_context)],
    scope: Annotated[str, Form()] = "",
    label: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Create a scoped operator credential and return the updated partial."""
    try:
        payload = SecretLabelInput.model_validate({"label": label.strip()})
    except ValidationError as exc:
        message = (
            "Credential label must be 200 characters or fewer."
            if any(error.get("type") == "string_too_long" for error in exc.errors())
            else "A credential label is required."
        )
        return await _render_operators_partial(request, ctx, error=message)

    if scope == "global":
        token = await create_global_secret(ctx.session, ctx.contest, payload.label)
        audit_scope = "global"
        email_scope_label = "Global (all sites)"
    else:
        site = await get_site_in_contest(ctx.session, ctx.contest, scope)
        if site is None:
            return await _render_operators_partial(request, ctx, error="Select a valid credential scope.")
        token = await create_site_secret(ctx.session, site, payload.label)
        audit_scope = f"site:{site.id}"
        email_scope_label = f"Site: {site.sitename}"
    await record_admin_action(
        ctx.session,
        request,
        module="web",
        actor_user_id=ctx.actor.id,
        actor_label=ctx.actor.username,
        action="animator_credential_create",
        target_type="animator_operator_credential",
        target_id=None,
        detail=f"contest={ctx.contest.login_slug}; scope={audit_scope}; label={payload.label}",
    )
    await ctx.session.commit()

    emailed_to = None
    email_failed = False
    admin_email = ctx.actor.email
    if admin_email:
        result = send_credentials_email(
            request.app.state.email_service,
            to_email=admin_email,
            fullname=ctx.actor.fullname,
            content=build_animator_credential_email_content(
                fullname=ctx.actor.fullname,
                contest_name=ctx.contest.contest_name,
                label=payload.label,
                scope_label=email_scope_label,
                token=token,
            ),
        )
        if result.success:
            emailed_to = admin_email
        else:
            email_failed = True
    return await _render_operators_partial(
        request,
        ctx,
        new_secret=token,
        new_secret_label=payload.label,
        emailed_to=emailed_to,
        email_failed=email_failed,
    )


@router.post("/secrets/{secret_id}/revoke", response_model=None, name="animator_revoke_secret")
async def revoke_secret_route(
    request: Request,
    ctx: Annotated[ContestAdminContext, Depends(get_contest_admin_context)],
    secret_id: str,
) -> HTMLResponse:
    """Revoke a contest-owned credential and return the updated partial."""
    removed = await delete_site_secret(ctx.session, ctx.contest, secret_id)
    if removed:
        await record_admin_action(
            ctx.session,
            request,
            module="web",
            actor_user_id=ctx.actor.id,
            actor_label=ctx.actor.username,
            action="animator_credential_revoke",
            target_type="animator_operator_credential",
            target_id=secret_id,
            detail=f"contest={ctx.contest.login_slug}",
        )
        await ctx.session.commit()
        return await _render_operators_partial(request, ctx)
    return await _render_operators_partial(request, ctx, error="Credential not found.")
