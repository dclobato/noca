# Arena url_for() Reference

Quick lookup for writing new Arena templates or debugging `url_for` calls.
All names are stable — changing a route's path no longer breaks templates.

## Static Assets

| `url_for` call | Generated path | Mount name |
|---|---|---|
| `request.url_for('arena_static_css', path='<file>.css')` | `/static/css/<file>.css` | `arena_static_css` |
| `request.url_for('arena_static_js', path='<file>.js')` | `/static/js/<file>.js` | `arena_static_js` |
| `request.url_for('static_shared_js', path='<file>.js')` | `/static/shared-js/<file>.js` | `static_shared_js` |
| `request.url_for('static_shared_css', path='<file>.css')` | `/static/shared-css/<file>.css` | `static_shared_css` |
| `request.url_for('static_shared_img', path='<file>')` | `/static/shared-img/<file>` | `static_shared_img` |
| `request.url_for('arena_static_img', path='<file>')` | `/static/img/<file>` | `arena_static_img` |
| `request.url_for('static_vendor', path='<file>')` | `/static/vendor/<file>` | `static_vendor` |
| `request.url_for('static_vendor', path='img/state-flags/<code>.svg')` | `/static/vendor/img/state-flags/<code>.svg` | `static_vendor` |
| `request.url_for('static_webfonts', path='<file>')` | `/static/webfonts/<file>` | `static_webfonts` |
| `request.url_for('arena_medal', band='<gold|silver|bronze>')` | `/assets/medal/<band>` | `arena_medal` |

## Health Route (`health.py`)

Use this endpoint for runtime health probes.

| Hardcoded path | Endpoint name | Path params | File |
|---|---|---|---|
| `GET /health` | `arena_health` | — | `health.py` |

## Help Routes (`arena/routes/help.py`)

| Hardcoded path | Endpoint name | Path params | File |
|---|---|---|---|
| `GET /help` | `arena_help_index` | — | `help.py` |
| `GET /help/rating` | `arena_help_rating` | — | `help.py` |
| `GET /help/rating/difficulty-distribution` | `arena_help_difficulty_distribution` | — | `help.py` |
| `GET /help/languages` | `arena_help_languages` | — | `help.py` |

## Legal Routes (`arena/routes/legal.py`)

| Hardcoded path | Endpoint name | Path params | File |
|---|---|---|---|
| `GET /legal/terms` | `arena_terms_of_service` | — | `legal.py` |
| `GET /legal/privacy` | `arena_privacy_policy` | — | `legal.py` |

## Announcement Routes (`arena/routes/announcements.py`)

| Hardcoded path | Endpoint name | Path params | File |
|---|---|---|---|
| `GET /announcements` | `arena_announcement_list` | Query: `page=` | `announcements.py` |
| `GET /announcements/{announcement_id}` | `arena_announcement_detail` | `announcement_id=`; query: `page=` (list page for the Back link) | `announcements.py` |
| `POST /announcements/{announcement_id}/acknowledge` | `arena_announcement_acknowledge` | `announcement_id=`; form: `acknowledge` (checkbox) | `announcements.py` |

## Problem routes (`arena/routes/problems.py`, `arena/routes/problem_editorial.py`, `arena/routes/problem_problem_sets.py`)

| `url_for` call | Generated path | Notes |
|---|---|---|
| `request.url_for('arena_problem_list')` | `/problems` | Query: `search`, `sort_by` (`relevance`, `number_asc`, `number_desc`, `title_asc`, `title_desc`, `solvers_asc`, `solvers_desc`, `rating_asc`, `rating_desc`), `category_slugs`, `language`, `page`; omitted sort defaults to relevance while searching and number ascending otherwise |
| `request.url_for('arena_problem_detail', arena_number=N)` | `/problems/{N}` | Query: `back_page`, `back_search`, `back_sort_by`, `back_category_slugs`, `back_language` |
| `request.url_for('arena_problem_print', arena_number=N)` | `/problems/{N}/print` | Standalone print-friendly problem page (statement, samples, limits); requires auth |
| `request.url_for('arena_problem_editorial_view', arena_number=N)` | `/problems/{N}/editorial` | Standalone Markdown editorial viewer, opened in a new tab from the detail page; requires auth; 404 unless the problem has an editorial and its `editorial_release_policy` gate passes (`always`, or `after_ac` with the current user already AC'd) |
| `request.url_for('arena_problem_rating_history_public', arena_number=N)` | `/problems/{N}/rating-history` | Returns JSON `{history:[…]}`; requires auth despite the endpoint name |
| `request.url_for('arena_problem_statistics', arena_number=N)` | `/problems/{N}/statistics` | Per-problem statistics page |
| `request.url_for('arena_problem_statistics_data', arena_number=N)` | `/problems/{N}/statistics.json` | Returns the precomputed statistics payload, or `{}`. Solver names are re-resolved through the age shield, so a shielded solver is named by their handle and carries no `profile_url` |
| `request.url_for('arena_problem_sample_testcases_zip', arena_number=N)` | `/problems/{N}/sample-testcases.zip` | Returns `application/zip` download of the sample (non-secret) test cases (Layout A); requires auth |
| `request.url_for('arena_problem_export', arena_number=N)` | `/problems/{N}/export` | Returns `application/zip` download of the public problem package (not importable) |
| `request.url_for('arena_problem_submit', arena_number=N)` | `/problems/{N}/submit` | POST only; requires auth; form fields `language_id`, `source_code`, and optional `problem_set_id` (ties the submission to a problem set so its teacher can see it; omitted = private) |
| `request.url_for('arena_problem_problem_set_add', arena_number=N)` | `/problems/{N}/problem-sets` | POST only; exact `ARENA_JUDGE` role and teacher-owned eligible set required; form field `problem_set_id`, with optional problem-list return-state fields |
| `request.url_for('arena_problem_toggle_favorite', arena_number=N)` | `/problems/{N}/favorite` | POST only; requires auth; returns `{"is_favorite": bool}`; guests get 401 |
| `request.url_for('arena_problem_request_removal', arena_number=N)` | `/problems/{N}/request-removal` | POST only; requires auth; owner without edit rights only; notifies all ARENA_ADMINs |

## Public Routes (`arena/routes/root.py`)

| Hardcoded path | Endpoint name | Path params | File |
|---|---|---|---|
| `GET /` | *(redirect, no name)* | — | `root.py` |
| `GET /dashboard` | `arena_dashboard` | — (anonymous; the leaderboard shows pseudonymous handles unless an adult opted in to their legal name) | `root.py` |
| `GET /assets/medal/{band}` | `arena_medal` | `band=` | `root.py` |
| `GET /<favicon asset>` | `arena_favicon_<file>` *(not used in templates; base templates reference the literal root paths)* | — | `root.py` |
| `GET /live` | `arena_live` | — | `live.py` |
| `GET /live/feed.json` | `arena_live_feed` | — | `live.py` |
| `GET /live/events` | `arena_live_events` | — | `live.py` (answers `429` when the IP/user already holds `NOCA_ARENA_SSE_MAX_PER_*` open streams) |

The `arena_live_feed` snapshot supplies separate affiliation and origin data.
Brazilian origins include the user's UF and local state-flag URL when available.

## Submission Routes (`arena/routes/submissions.py`)

| `url_for` call | Generated path | Notes |
|---|---|---|
| `request.url_for('arena_submission_detail', submission_id=ID)` | `/submissions/{ID}` | Requires auth; direct access is owner-only unless `ARENA_ADMIN`, while an authorized class-report drill-down may add `back_context=student_report`, `back_class_id`, `back_set_id`, and `back_user_id` to show the report return link. Those query values are navigation-only and checked against the persisted submission. The owner confirms AI review requests in a balance-preview modal and sees pending, batch-queued, or completed review states for non-AC submissions |
| `request.url_for('arena_submission_source_download', submission_id=ID)` | `/submissions/{ID}/source` | Downloads the raw source code as a UTF-8 attachment. Uses the same owner/admin authorization as the detail page; authorized class-report viewers must forward `back_context`, `back_class_id`, `back_set_id`, and `back_user_id` |
| `request.url_for('arena_submission_request_ai_review', submission_id=ID)` | `/submissions/{ID}/request-ai-review` | POST only; owner-only (no admin bypass); idempotent (a pending submission is never re-enqueued); capped per user by `NOCA_ARENA_AI_REVIEW_RATE_LIMIT_*` (danger flash + 303 over budget); requires `ai_api_key` or `ai_backend_credits > 0`; consumes one credit when using platform key |
| `request.url_for('arena_submission_teacher_feedback', submission_id=ID)` | `/submissions/{ID}/teacher-feedback` | POST only; manager-only (set's teacher or ARENA_ADMIN); non-AC, set-tied submissions; upserts feedback and notifies the student; `back_class_id`/`back_set_id`/`back_user_id`/`back_context` form fields are navigation-only |
| `request.url_for('arena_submission_force_rejudge', submission_id=ID)` | `/submissions/{ID}/force-rejudge` | POST only; ARENA_ADMIN-only; supersedes the active judgment, queues a new one, and enqueues a fresh judging job; rendered as a confirmation modal on the submission detail page |

## Notification Routes (`arena/routes/notifications.py`)

| Hardcoded path | Endpoint name | Path params | File |
|---|---|---|---|
| `GET /arena/notifications` | `arena_notifications_list` | — | `notifications.py` |
| `POST /arena/notifications/read-all` | `arena_notifications_mark_all_read` | — | `notifications.py` |
| `POST /arena/notifications/{notification_id}/read` | `arena_notification_mark_read` | `notification_id=` | `notifications.py` |

## Presence Routes (`arena/routes/presence.py`)

| Hardcoded path | Endpoint name | Path params | File |
|---|---|---|---|
| `POST /arena/presence/heartbeat` | `arena_presence_heartbeat` | — | `presence.py` |
| `POST /arena/presence/status` | `arena_presence_status` | body `{"ids": [...]}` | `presence.py` |

## Class Routes (`arena/routes/classes.py`)

| `url_for` call | Generated path | Notes |
|---|---|---|
| `request.url_for('arena_classes_index')` | `/classes` | Classes landing page with choice cards |
| `request.url_for('arena_classes_registered')` | `/classes/registered` | Registered classes; query: `search`, `sort`, `dir`, `page` |
| `request.url_for('arena_classes_open')` | `/classes/open` | Open for registration; query: `search`, `teacher_id`, `sort`, `dir`, `page` |
| `request.url_for('arena_classes_manage')` | `/classes/manage` | Manage classes (judges/admins only); query: `search`, `sort`, `dir`, `page` |
| `request.url_for('arena_class_new')` | `/classes/new` | Judge/admin create form |
| `request.url_for('arena_class_create')` | `/classes/new` | POST only |
| `request.url_for('arena_class_teacher_autocomplete')` | `/classes/teachers/autocomplete` | Authenticated JSON endpoint; query: `q`. Admins see all judges; regular users see affiliation-matching judges. |
| `request.url_for('arena_class_detail', class_id=ID)` | `/classes/{ID}` | Authenticated class detail placeholder; shows a green registration indicator for active members |
| `request.url_for('arena_class_request_registration', class_id=ID)` | `/classes/{ID}/request-registration` | POST only; creates in-app notification + best-effort email to teacher |
| `request.url_for('arena_class_edit', class_id=ID)` | `/classes/{ID}/edit` | Judge/admin edit form |
| `request.url_for('arena_class_update', class_id=ID)` | `/classes/{ID}/edit` | POST only |
| `request.url_for('arena_class_members', class_id=ID)` | `/classes/{ID}/members` | Teacher/admin membership management page |
| `request.url_for('arena_class_members_add', class_id=ID)` | `/classes/{ID}/members` | POST only; form fields: repeated `student_ids`; creates CLASS_MEMBERSHIP_ADDED in-app notification + best-effort email per added student |
| `request.url_for('arena_class_member_student_autocomplete', class_id=ID)` | `/classes/{ID}/members/autocomplete` | Teacher/admin JSON endpoint; query: `q` |
| `request.url_for('arena_class_problem_set_list', class_id=ID)` | `/classes/{ID}/problem-sets` | Teacher/admin problem-set list; query: `page`, `sort`, `direction` |
| `request.url_for('arena_class_problem_set_create', class_id=ID)` | `/classes/{ID}/problem-sets` | POST only |
| `request.url_for('arena_class_full_report', class_id=ID)` | `/classes/{ID}/problem-sets/report` | Teacher/admin class-wide students x problem-sets report; optional return query params `page`, `sort`, `direction` |
| `request.url_for('arena_class_full_report_csv', class_id=ID)` | `/classes/{ID}/problem-sets/report/csv` | Teacher/admin CSV download of the class-wide report; no query params |
| `request.url_for('arena_class_problem_set_manage', class_id=ID, set_id=SID)` | `/classes/{ID}/problem-sets/{SID}/problems` | Teacher/admin manage-problems page; optional return query params `page`, `sort`, `direction` |
| `request.url_for('arena_class_problem_set_problem_add', class_id=ID, set_id=SID)` | `/classes/{ID}/problem-sets/{SID}/problems` | POST only; form fields: repeated `problem_refs`, optional legacy `problem_ref` |
| `request.url_for('arena_class_problem_set_problem_remove', class_id=ID, set_id=SID, problem_id=PID)` | `/classes/{ID}/problem-sets/{SID}/problems/{PID}/remove` | POST only |
| `request.url_for('arena_class_problem_set_update_schedule', class_id=ID, set_id=SID)` | `/classes/{ID}/problem-sets/{SID}/schedule` | POST only; form fields: `description`, `starts_on`, `deadline` (all optional; dates use datetime-local format) |
| `request.url_for('arena_class_problem_set_stop_now', class_id=ID, set_id=SID)` | `/classes/{ID}/problem-sets/{SID}/stop-now` | POST only; no form fields |
| `request.url_for('arena_class_problem_set_delete', class_id=ID, set_id=SID)` | `/classes/{ID}/problem-sets/{SID}/delete` | POST only; form also sends `password`, `page`, `sort`, `direction` |
| `request.url_for('arena_class_problem_set_report', class_id=ID, set_id=SID)` | `/classes/{ID}/problem-sets/{SID}/report` | Teacher/admin report page; optional return query params `page`, `sort`, `direction` |
| `request.url_for('arena_class_problem_set_report_student', class_id=ID, set_id=SID, user_id=UID)` | `/classes/{ID}/problem-sets/{SID}/report/student/{UID}` | Teacher/admin student drill-down; optional return query params `page`, `sort`, `direction` |
| `request.url_for('arena_class_problem_set_batch_feedback', class_id=ID, set_id=SID, problem_id=PID)` | `/classes/{ID}/problem-sets/{SID}/problems/{PID}/batch-feedback` | Teacher/admin batch-feedback page for one problem |
| `request.url_for('arena_class_problem_set_batch_feedback_submit', class_id=ID, set_id=SID, problem_id=PID)` | `/classes/{ID}/problem-sets/{SID}/problems/{PID}/batch-feedback` | POST only; form fields: `feedback__{submission_id}` per entry |
| `request.url_for('arena_class_problem_set_problem_autocomplete', class_id=ID, set_id=SID)` | `/classes/{ID}/problem-sets/{SID}/problems/autocomplete` | Teacher/admin JSON endpoint; query: `q` |
| `request.url_for('arena_class_request_approve', request_id=ID)` | `/classes/registration-requests/{ID}/approve` | POST only; creates CLASS_REGISTRATION_APPROVED in-app notification + best-effort email to student |
| `request.url_for('arena_class_request_deny', request_id=ID)` | `/classes/registration-requests/{ID}/deny` | POST only; creates CLASS_REGISTRATION_DENIED in-app notification + best-effort email to student (with optional reason) |
| `request.url_for('arena_class_member_remove', class_id=ID, user_id=UID)` | `/classes/{ID}/members/{UID}/remove` | POST only; when actor ≠ removed user: CLASS_MEMBERSHIP_REMOVED notification + best-effort email; self-removal: no notification or email |

## Student Problem Set Routes (`arena/routes/student_problem_sets.py`)

| `url_for` call | Generated path | Notes |
|---|---|---|
| `request.url_for('arena_student_class_problem_set_list', class_id=ID)` | `/classes/{ID}/my-problem-sets` | Student-facing problem-set list; query: `page`, `sort` (`deadline`\|`name`), `direction` (`asc`\|`desc`) |
| `request.url_for('arena_student_class_problem_set_detail', class_id=ID, set_id=SID)` | `/classes/{ID}/my-problem-sets/{SID}` | Student-facing problem-set detail; optional return query params `page`, `sort`, `direction` forwarded to back button |

## Auth Routes (`arena/routes/auth.py`)

| Hardcoded path | Endpoint name | Path params | File |
|---|---|---|---|
| `GET /auth/login` | `arena_login` | Optional query: `next` (same-origin path target; defaults to `/auth/google/link-existing` while a `pending_google_existing_uid` session key is live) | `auth.py` |
| `POST /auth/login` | `arena_login_submit` | Optional form: `next` (same-origin path target) | `auth.py` |
| `POST /auth/resend-activation` | `arena_resend_activation` | — | `auth.py` |
| `POST /auth/resend-parental-consent` | `arena_resend_parental_consent` | — | `auth.py` |
| `POST /auth/update-parental-email` | `arena_update_parental_email` | — | `auth.py` |
| `POST /auth/update-date-of-birth` | `arena_update_date_of_birth` | — (IP-throttled; `429` + `Retry-After` when locked) | `auth.py` |
| `GET /auth/change-password` | `arena_change_password` | — | `auth.py` |
| `POST /auth/change-password` | `arena_change_password_submit` | — (password check throttled per user id + IP; `429` + `Retry-After` when locked) | `auth_password.py` |
| `GET /auth/2fa` | `arena_2fa` | — | `auth_2fa.py` |
| `POST /auth/2fa` | `arena_2fa_submit` | — (successful verification clears only the account throttle; IP spray history remains) | `auth_2fa.py` |
| `GET /auth/signup` | `arena_signup` | — | `auth_signup.py` |
| `POST /auth/signup` | `arena_signup_submit` | — (the request flood guard runs before form or multipart parsing; `429` + `Retry-After` when locked. `409` when a unique username could not be allocated after retries; `422` for every other validation failure) | `auth_signup.py` |
| `POST /auth/logout` | `arena_logout` | — | `auth.py` |
| `GET /auth/accept-terms` | `arena_accept_terms` | — | `auth.py` |
| `POST /auth/accept-terms` | `arena_accept_terms_submit` | — (IP-throttled; `429` + `Retry-After` when locked) | `auth_signup.py` |
| `GET /auth/activate` | `arena_activate` | Query: `token` | `auth.py` |
| `GET /auth/parental-consent` | `arena_parental_consent` | Query: `token` | `auth_parental_grant.py`; renders the review page, mutates nothing |
| `POST /auth/parental-consent` | `arena_parental_consent_submit` | Form: `token` (`token_redeem`-throttled; `429` + `Retry-After` when locked) | `auth_parental_grant.py`; `303` to `arena_login` |
| `GET /auth/parental-consent/revoke` | `arena_parental_consent_revoke` | Query: `token` | `auth_parental_revoke.py` |
| `POST /auth/parental-consent/revoke` | `arena_parental_consent_revoke_submit` | Form: `token` (IP-throttled; `429` + `Retry-After` when locked) | `auth_parental_revoke.py`; `303` to `arena_parental_consent_revoked` on success |
| `GET /auth/parental-consent/revoked` | `arena_parental_consent_revoked` | — | `auth_parental_revoke.py` |
| `GET /auth/google/login` | `arena_google_login` | Optional query: `next` (same-origin path target). `404` while Google sign-in is disabled | `auth_google.py` |
| `GET /auth/google/callback` | `arena_google_callback` | — (IP-throttled under the `google-login` action; `429` + `Retry-After` when locked. `404` while disabled) | `auth_google.py` |
| `POST /auth/google/link` | `arena_google_link` | — (requires a logged-in Arena user; `404` while disabled) | `auth_google.py` |
| `POST /auth/google/unlink` | `arena_google_unlink` | — (requires a logged-in Arena user; refused by the last-method guard when the account has no usable password) | `auth_google.py` |
| `POST /auth/google/avatar-source` | `arena_google_avatar_source` | Form: `source` (`arena` or `google`; requires a logged-in user with a linked Google identity) | `auth_google.py` |
| `GET /auth/google/complete` | `arena_google_complete` | — (requires a `pending_google_signup_uid` session key) | `auth_google_complete.py` |
| `POST /auth/google/complete` | `arena_google_complete_submit` | Form: `date_of_birth`, `terms`, optional `email_responsavel_legal` (required for ages 13-17) | `auth_google_complete.py` |
| `GET /auth/google/blocked` | `arena_google_blocked` | — (stateless; no session required) | `auth_google_complete.py` |
| `GET /auth/google/pending-consent` | `arena_google_pending_consent` | — (requires a `pending_google_consent_uid` session key) | `auth_google_complete.py` |
| `POST /auth/google/pending-consent/resend` | `arena_google_pending_consent_resend` | — (IP-throttled under the `resend_google_parental_consent` action) | `auth_google_complete.py` |
| `POST /auth/google/complete/existing-account` | `arena_google_complete_existing_account` | — (requires a `pending_google_signup_uid` session key naming a never-completed signup) | `auth_google_existing.py` |
| `GET /auth/google/link-existing` | `arena_google_link_existing` | — (requires a logged-in Arena user and a live `pending_google_existing_uid` session key) | `auth_google_existing.py` |
| `POST /auth/google/link-existing` | `arena_google_link_existing_submit` | Form: `decision` (`link`; any other value declines). Same requirements as the `GET` | `auth_google_existing.py` |
| `GET /auth/password-reset` | `arena_password_reset` | Optional query: `token` | `auth.py` |
| `POST /auth/password-reset` | `arena_password_reset_submit` | — | `auth.py` |

## User Routes (`arena/routes/users.py`)

| Hardcoded path | Endpoint name | Path params | File |
|---|---|---|---|
| `GET /user/profile` | `arena_user_profile` | Query: `tab` (`badges` supported), `solved_page`, `attempted_page`, `notifications_page` | `users.py` |
| `POST /user/profile/photo` | `arena_user_profile_photo_update` | — | `users.py` |
| `POST /user/profile/personal-data` | `arena_user_profile_personal_data_update` | JSON: `name`, `date_of_birth`, optional location/affiliation/programming language, `prefered_language`, `ranking_visible`, `public_profile`, `full_name_public` (enabling either publication flag on an age-shielded account is `400 age_shielded`) | `user_profile_api.py` |
| `POST /user/profile/notifications/{notification_id}/delete` | `arena_user_profile_notification_delete` | `notification_id=` | `users.py` |
| `POST /user/profile/notifications/delete-all` | `arena_user_profile_notifications_delete_all` | — | `users.py` |
| `POST /user/profile/notifications/mark-all-read` | `arena_user_profile_notifications_mark_all_read` | — | `users.py` |
| `GET /user/profile/subdivisions` | `arena_user_profile_subdivisions` | Query: `country_code` | `user_profile_api.py` |
| `POST /user/profile/location` | `arena_user_profile_location_update` | — | `user_profile_api.py` |
| `POST /user/profile/location/detect` | `arena_user_profile_location_detect` | JSON: `latitude`, `longitude` | `user_profile_api.py` |
| `GET /user/profile/affiliations/search` | `arena_user_profile_affiliations_search` | Query: `q` | `user_profile_api.py` |
| `POST /user/profile/affiliation` | `arena_user_profile_affiliation_update` | — | `user_profile_api.py` |
| `POST /user/profile/language` | `arena_user_profile_language_update` | — | `user_profile_api.py` |
| `GET /user/profile/rating-history` | `arena_user_profile_rating_history` | — | `user_profile_api.py` |
| `GET /user/profile/submission-heatmap` | `arena_user_profile_submission_heatmap` | — | `user_profile_api.py` |
| `GET /user/profile/statistics` | `arena_user_profile_statistics` | — | `user_profile_api.py` |
| `POST /user/profile/api-key` | `arena_user_profile_api_key_update` | — | `user_profile_api.py` |
| `GET /user/profile/username/available` | `arena_user_profile_username_available` | Query: `q` (per-user rate limit; `429` + `Retry-After` when exceeded) | `user_username_api.py` |
| `POST /user/profile/username` | `arena_user_profile_username_update` | JSON: `username` (`400` invalid, `409` taken, `429` cooldown) | `user_username_api.py` |
| `GET /user/submissions/status.json` | `arena_user_submissions_status` | Query: `ids` (CSV of owned submission UUIDs) | `user_submission_status.py` |
| `GET /user/submissions/status/events` | `arena_user_submissions_events` | Query: `ids` (CSV of owned submission UUIDs) | `user_submission_status.py` (answers `429` when the IP/user already holds `NOCA_ARENA_SSE_MAX_PER_*` open streams) |
| `GET /user/{user_id}/photo` | `arena_user_photo_by_id` | `user_id=` | `users.py` |
| `GET /user/{user_id}/avatar` | `arena_user_avatar_by_id` | `user_id=` | `users.py` |

## Public Profile Routes (`arena/routes/user_public_profile.py`)

"Public" names the opt-in profile other *logged-in* users may view; all four routes require auth.
All four also answer **404** when the profile owner is age-shielded (13-17, or an unknown date of
birth), whatever their stored `public_profile` flag says — an `ARENA_ADMIN` viewer bypasses that
for moderation. The refusal is a 404 rather than a 403 so a shielded profile stays
non-enumerable.

| Hardcoded path | Endpoint name | Path params | File |
|---|---|---|---|
| `GET /profile/{user_id}` | `arena_user_profile_public` | `user_id=` | `user_public_profile.py` |
| `GET /profile/{user_id}/rating-history.json` | `arena_user_profile_rating_history_public` | `user_id=` | `user_public_profile.py` |
| `GET /profile/{user_id}/submission-heatmap.json` | `arena_user_profile_submission_heatmap_public` | `user_id=` | `user_public_profile.py` |
| `GET /profile/{user_id}/statistics.json` | `arena_user_profile_statistics_public` | `user_id=` | `user_public_profile.py` |

## User Security Routes (`arena/routes/user_security.py`)

| Hardcoded path | Endpoint name | Path params | File |
|---|---|---|---|
| `GET /user/profile/2fa/setup` | `arena_2fa_setup` | — | `user_security.py` |
| `POST /user/profile/2fa/confirm` | `arena_2fa_confirm` | — (TOTP check throttled per user id + IP; `429` + `Retry-After` when locked) | `user_security.py` |
| `POST /user/profile/2fa/disable` | `arena_2fa_disable` | — (password check throttled per user id + IP; `429` + `Retry-After` when locked) | `user_security.py` |
| `POST /user/profile/backup-codes/regenerate` | `arena_backup_codes_regenerate` | — | `user_security.py` |
| `GET /user/profile/backup-codes` | `arena_backup_codes` | — | `user_security.py` |

## Arena Admin – Dashboard Routes

The dashboard routes require an Arena administrator and use HTMX for worker
card polling and removal. The AI credits route renders batch turnaround in
compact adaptive units when both staging and review-storage timestamps exist,
and summarizes the recent Valkey turnaround statistics above its filters.

| Hardcoded path | Endpoint name | Path params | File |
|---|---|---|---|
| `GET /admin/dashboard` | `arena_admin_dashboard` | — | `admin_dashboard.py` |
| `GET /admin/dashboard/service-status` | `arena_admin_dashboard_service_status` | — | `admin_dashboard.py` |
| `GET /admin/dashboard/workers` | `arena_admin_dashboard_workers` | — | `admin_dashboard.py` |
| `POST /admin/dashboard/workers/remove` | `arena_admin_dashboard_worker_remove` | Form: `worker_class`, `worker_id` | `admin_dashboard.py` |
| `POST /admin/dashboard/workers/pause` | `arena_admin_dashboard_worker_pause` | Form: `worker_class`, `worker_id` | `admin_dashboard.py` |
| `POST /admin/dashboard/workers/resume` | `arena_admin_dashboard_worker_resume` | Form: `worker_class`, `worker_id` | `admin_dashboard.py` |
| `POST /admin/dashboard/workers/flush-now` | `arena_admin_dashboard_worker_flush_now` | Form: `worker_class`, `worker_id` | `admin_dashboard.py` |
| `POST /admin/dashboard/workers/poll-now` | `arena_admin_dashboard_worker_poll_now` | Form: `worker_class`, `worker_id` | `admin_dashboard.py` |
| `GET /admin/dashboard/ai-usage` | `arena_admin_dashboard_ai_usage` | `search=`, `sort_dir=`, `per_page=`, `page=`, `date_from=`, `date_to=` | `admin_dashboard.py` |
| `GET /admin/dashboard/login-history` | `arena_admin_dashboard_login_history` | `search=`, `sort_dir=`, `per_page=`, `page=`, `date_from=`, `date_to=` | `admin_dashboard_history.py` |
| `GET /admin/dashboard/submissions` | `arena_admin_dashboard_submissions` | `search=`, `verdict_filter=`, `status_filter=`, `ai_filter=`, `language_filter=`, `problem_filter=`, `date_from=`, `date_to=`, `sort_dir=`, `per_page=`, `page=` | `admin_dashboard_history.py` |
| `POST /admin/dashboard/submissions/{submission_id}/reenqueue` | `arena_admin_dashboard_submission_reenqueue` | path: `submission_id` | `admin_dashboard_history.py` |
| `GET /admin/dashboard/security-events` | `arena_admin_dashboard_security_events` | `module=`, `event_type=`, `per_page=`, `page=` | `admin_dashboard_security.py` |
| `GET /admin/dashboard/security-events.csv` | `arena_admin_dashboard_security_events_csv` | none | `admin_dashboard_security.py` |
| `GET /admin/dashboard/terms` | `arena_admin_dashboard_terms` | none | `admin_dashboard_terms.py` |
| `POST /admin/dashboard/terms/reset` | `arena_admin_terms_reset` | Form: `confirm_password` | `admin_dashboard_terms.py` |
| `GET /admin/dashboard/lockouts` | `arena_admin_dashboard_lockouts` | `ip=`, `identifier=`, `identifier_hash=` (prefill + live status) | `admin_dashboard_lockouts.py` |
| `POST /admin/dashboard/lockouts/unlock-ip` | `arena_admin_dashboard_unlock_ip` | Form: `ip`, `confirm_password` | `admin_dashboard_lockouts.py` |
| `POST /admin/dashboard/lockouts/unlock-account` | `arena_admin_dashboard_unlock_account` | Form: `identifier` and/or `identifier_hash`, `confirm_password` | `admin_dashboard_lockouts.py` |

## Arena Admin – User Management Routes

GET routes: `arena/routes/admin_users.py` · POST routes: `arena/routes/admin_users_actions.py` · Helpers: `arena/routes/admin_user_route_support.py`

| Hardcoded path | Endpoint name | Path params | File |
|---|---|---|---|
| `GET /admin/users` | `arena_admin_user_list` | `search=` (name, username, account email, guardian email), `role=`, `can_edit=`, `per_page=`, `page=` | `admin_users.py` |
| `GET /admin/users/{user_id}` | `arena_admin_user_profile` | `user_id=`, query: `tab`, `credits_page`, `notifications_page`, `submissions_page`, `submissions_search`, `submissions_verdict`, `login_page`, `login_per_page`, `login_sort_dir`, `login_date_from`, `login_date_to` | `admin_users.py` |
| `GET /admin/users/{user_id}/rating-history` | `arena_admin_user_rating_history` | `user_id=` | `admin_users.py` |
| `GET /admin/users/{user_id}/submission-heatmap` | `arena_admin_user_submission_heatmap` | `user_id=` | `admin_users.py` |
| `GET /admin/users/{user_id}/statistics` | `arena_admin_user_statistics` | `user_id=` | `admin_users.py` |
| `POST /admin/users/{user_id}/role` | `arena_admin_user_change_role` | `user_id=` | `admin_users_actions.py` |
| `POST /admin/users/{user_id}/toggle-active` | `arena_admin_user_toggle_active` | `user_id=` | `admin_users_actions.py` |
| `POST /admin/users/{user_id}/force-password-change` | `arena_admin_user_force_pw_change` | `user_id=` | `admin_users_actions.py` |
| `POST /admin/users/{user_id}/remove-photo` | `arena_admin_user_remove_photo` | `user_id=` | `admin_users_actions.py` |
| `POST /admin/users/{user_id}/disable-2fa` | `arena_admin_user_disable_2fa` | `user_id=` | `admin_users_actions.py` |
| `POST /admin/users/{user_id}/unlock` | `arena_admin_user_unlock` | `user_id=` (admin-only; password-confirmed; Form: `confirm_password` + `NavState` fields) | `admin_users_lockout.py` |
| `POST /admin/users/{user_id}/unlink-google` | `arena_admin_user_unlink_google` | `user_id=` (admin-only; password-confirmed; allowed for a completed Google-only account, unlike the self-service unlink, but refused for an unfinished Google-first signup) | `admin_users_google.py` |
| `POST /admin/users/{user_id}/change-name` | `arena_admin_user_change_name` | `user_id=` | `admin_users_actions.py` |
| `POST /admin/users/{user_id}/date-of-birth` | `arena_admin_user_change_date_of_birth` | `user_id=` | `admin_users_actions.py` |
| `POST /admin/users/{user_id}/personal-info` | `arena_admin_user_change_personal_info` | `user_id=` | `admin_users_actions.py` |
| `POST /admin/users/{user_id}/remove-location` | `arena_admin_user_remove_location` | `user_id=` | `admin_users_actions.py` |
| `POST /admin/users/{user_id}/remove-affiliation` | `arena_admin_user_remove_affiliation` | `user_id=` | `admin_users_actions.py` |
| `POST /admin/users/{user_id}/reset-api-key` | `arena_admin_user_reset_api_key` | `user_id=` | `admin_users_actions.py` |
| `POST /admin/users/{user_id}/topup-credits` | `arena_admin_user_topup_credits` | `user_id=` | `admin_users_actions.py` |
| `POST /admin/users/{user_id}/toggle-email-confirmed` | `arena_admin_user_toggle_email_confirmed` | `user_id=` | `admin_users_actions.py` |
| `POST /admin/users/{user_id}/toggle-parental-consent` | `arena_admin_user_toggle_parental_consent` | `user_id=`, form: `confirm_password` | `admin_users_consent.py` |
| `POST /admin/users/{user_id}/toggle-can-edit` | `arena_admin_user_toggle_can_edit` | `user_id=` | `admin_users_actions.py` |
| `POST /admin/users/{user_id}/toggle-ranking-visible` | `arena_admin_user_toggle_ranking_visible` | `user_id=` | `admin_users_actions.py` |
| `POST /admin/users/{user_id}/toggle-public-profile` | `arena_admin_user_toggle_public_profile` | `user_id=` (enabling is refused for an age-shielded target) | `admin_users_actions.py` |
| `GET /admin/users/{user_id}/username/available` | `arena_admin_user_username_available` | `user_id=`; query `q` (admin-only; excludes the target, not the caller) | `admin_users_username.py` |
| `POST /admin/users/{user_id}/change-username` | `arena_admin_user_change_username` | `user_id=`; form `new_username`, `confirm_password`, optional `allow_immediate_change` (clears the target's cooldown instead of restarting it) | `admin_users_username.py` |

## Arena Admin – Category Management Routes (`arena/routes/admin_categories.py`)

| Hardcoded path | Endpoint name | Path params | File |
|---|---|---|---|
| `GET /admin/categories` | `arena_admin_category_list` | — | `admin_categories.py` |
| `GET /admin/categories/new` | `arena_admin_category_new` | — | `admin_categories.py` |
| `POST /admin/categories/new` | `arena_admin_category_create` | — | `admin_categories.py` |
| `GET /admin/categories/{category_id}/edit` | `arena_admin_category_edit` | `category_id=` | `admin_categories.py` |
| `POST /admin/categories/{category_id}/edit` | `arena_admin_category_update` | `category_id=` | `admin_categories.py` |
| `POST /admin/categories/{category_id}/delete` | `arena_admin_category_delete` | `category_id=` | `admin_categories.py` |

## Arena Admin – Announcement Management Routes (`arena/routes/admin_announcements.py`)

| Hardcoded path | Endpoint name | Path params | File |
|---|---|---|---|
| `GET /admin/announcements` | `arena_admin_announcement_list` | Query: `page=` | `admin_announcements.py` |
| `GET /admin/announcements/new` | `arena_admin_announcement_new` | — | `admin_announcements.py` |
| `POST /admin/announcements` | `arena_admin_announcement_create` | Form: `title`, `body`, `required` (checkbox) | `admin_announcements.py` |
| `POST /admin/announcements/{announcement_id}/delete` | `arena_admin_announcement_delete` | `announcement_id=`; form: `page` | `admin_announcements.py` |

## Arena Admin – Problem Management Routes (`arena/routes/admin_problems.py`)

| Hardcoded path | Endpoint name | Path params | File |
|---|---|---|---|
| `GET /admin/problems` | `arena_admin_problem_list` | Query: `search`, `sort_by` (`relevance`, `number_asc`, `number_desc`, `title_asc`, `title_desc`, `rating_asc`, `rating_desc`), `owner_id`, `category_slugs`, `language`, `enabled` (`1`/`0`, any other value means all statuses), `editorial` (`none`/`never`/`always`/`after_ac`, any other value means all), `per_page`, `page`; omitted sort defaults to relevance while searching and number ascending otherwise | `admin_problems.py` |
| `GET /admin/problems/new` | `arena_admin_problem_new_choose` | Optional query: list-return state | `admin_problem_new.py` |
| `GET /admin/problems/new/{validator_type}` | `arena_admin_problem_new` | `validator_type=`; optional query: list-return state, `tab=` | `admin_problems.py` |
| `POST /admin/problems/new/{validator_type}` | `arena_admin_problem_create` | `validator_type=`; form `active_tab`. Definition only; HTML 422 on invalid fields, otherwise redirects to judgment | `admin_problem_save.py` |
| `GET /admin/problems/{problem_id}/edit` | `arena_admin_problem_edit` | `problem_id=`, optional query: list-return state, `tab=` | `admin_problems.py` |
| `POST /admin/problems/{problem_id}/edit` | `arena_admin_problem_update` | `problem_id=`; form `active_tab`, required `save_action` (`enable`/`disable`). Saves the definition and targets the publication state atomically; HTML 422 opens the first invalid field or reports a failed enablement gate | `admin_problem_save.py` |
| `POST /admin/problems/{problem_id}/toggle-enabled` | `arena_admin_problem_toggle_enabled` | `problem_id=`, Query: `page`, `per_page`, `search`, `sort_by`, `owner_id`, `category_slugs`, `language`, `enabled`, `editorial` | `admin_problems.py` |
| `POST /admin/problems/{problem_id}/set-enabled` | `arena_admin_problem_set_enabled` | `problem_id=`, Form: `save_action`, `next_url` | `admin_problems.py` |
| `POST /admin/problems/{problem_id}/delete` | `arena_admin_problem_delete` | `problem_id=`, Form: `password`, list-return state | `admin_problems.py` |
| `POST /admin/problems/{problem_id}/rejudge-all` | `arena_admin_problem_rejudge_all` | `problem_id=`, Form: `password`, optional safe local `next_url` | `admin_problems.py` |
| `GET /admin/problems/suggestions` | `arena_admin_problem_suggestions` | Required query: `field` (`author`, `license`, or `source`), literal `q` (3–256 characters) whose whitespace-separated terms are matched independently as substrings, each needing three letters or digits (a shorter term is declined, not scanned for); admins see all problems, while editors see enabled problems plus their own disabled drafts | `admin_problem_api.py` |
| `POST /admin/problems/detect-language` | `arena_admin_problem_detect_language` | JSON body: `statement`, `title` | `admin_problem_api.py` |

## Arena Admin – Problem Import/Export Routes (`arena/routes/admin_problem_io.py`)

| Hardcoded path | Endpoint name | Path params | File |
|---|---|---|---|
| `GET /admin/problems/import` | `arena_admin_problem_import_form` | — | `admin_problem_io.py` |
| `GET /admin/problems/import/sample` | `arena_admin_problem_sample_package` | — | `admin_problem_io.py` |
| `POST /admin/problems/import` | `arena_admin_problem_import_submit` | Form: `package` (file) | `admin_problem_io.py` |
| `GET /admin/problems/{problem_id}/export` | `arena_admin_problem_export` | `problem_id=` | `admin_problem_io.py` |

## Arena Admin – Affiliation Management Routes (`arena/routes/admin_affiliations.py`, `arena/routes/affiliations.py`)

| Hardcoded path | Endpoint name | Path params | File |
|---|---|---|---|
| `GET /admin/affiliations` | `arena_admin_affiliation_list` | Query: `search`, `country_code`, `subdivision_code`, `per_page`, `page` | `admin_affiliations.py` |
| `POST /admin/affiliations/new` | `arena_admin_affiliation_create` | — | `admin_affiliations.py` |
| `POST /admin/affiliations/{affiliation_id}/edit` | `arena_admin_affiliation_update` | `affiliation_id=` | `admin_affiliations.py` |
| `POST /admin/affiliations/{affiliation_id}/logo` | `arena_admin_affiliation_logo` | `affiliation_id=` | `admin_affiliations.py` |
| `POST /admin/affiliations/{affiliation_id}/delete` | `arena_admin_affiliation_delete` | `affiliation_id=` | `admin_affiliations.py` |
| `GET /affiliations/{affiliation_id}/logo` | `arena_affiliation_logo` | `affiliation_id=` | `affiliations.py` |
| `GET /affiliations/{affiliation_id}/logo/thumbnail` | `arena_affiliation_logo_thumbnail` | `affiliation_id=` | `affiliations.py` |
| `GET /affiliations/{affiliation_id}/rating-history` | `arena_affiliation_rating_history` | `affiliation_id=` | `affiliations.py` |

## Ranking Routes (`arena/routes/ranking.py`)

| Hardcoded path | Endpoint name | Path params | File |
|---|---|---|---|
| `GET /ranking` | `arena_ranking_index` | — | `ranking.py` |
| `GET /ranking/users` | `arena_ranking_users` | Query: `search`, `page` (pseudonymous display; `search` will not match an age-shielded user by real name, only by username or email) | `ranking.py` |
| `GET /ranking/affiliations` | `arena_ranking_affiliations` | Query: `search`, `country_code`, `subdivision_code`, `page` | `ranking.py` |
| `GET /ranking/affiliations/{affiliation_id}/users` | `arena_ranking_affiliation_users` | `affiliation_id=`, Query: `search`, `page` (same pseudonymous display and shielded search as `/ranking/users`) | `ranking.py` |

## Arena Admin – Problem Test Case Routes (`arena/routes/admin_problem_tc.py`)

| Hardcoded path | Endpoint name | Path params | File |
|---|---|---|---|
| `GET /admin/problems/{problem_id}/judgment` | `arena_admin_problem_judgment` | `problem_id=` | `admin_problem_judgment.py` |
| `GET\|POST /admin/problems/{problem_id}/judgment/test-cases` | `arena_admin_problem_judgment_cases` / `..._cases_save` | `problem_id=`; invalid inline rows return retained HTML (422) | `admin_problem_judgment.py` |
| `POST /admin/problems/{problem_id}/judgment/test-cases/upload` | `arena_admin_problem_judgment_case_upload` | `problem_id=` | `admin_problem_judgment.py` |
| `POST /admin/problems/{problem_id}/judgment/test-cases/bulk` | `arena_admin_problem_judgment_cases_replace_all` | `problem_id=` | `admin_problem_judgment.py` |
| `POST /admin/problems/{problem_id}/judgment/test-cases/{tc_id}/toggle-sample` | `arena_admin_problem_judgment_case_toggle_sample` | `problem_id=`, `tc_id=` | `admin_problem_judgment.py` |
| `POST /admin/problems/{problem_id}/judgment/test-cases/{tc_id}/replace` | `arena_admin_problem_judgment_case_replace` | `problem_id=`, `tc_id=` | `admin_problem_judgment.py` |
| `POST /admin/problems/{problem_id}/judgment/test-cases/{tc_id}/delete` | `arena_admin_problem_judgment_case_delete` | `problem_id=`, `tc_id=` | `admin_problem_judgment.py` |
| `GET /admin/problems/{problem_id}/judgment/validator` | `arena_admin_problem_judgment_validator` | `problem_id=` | `admin_problem_judgment.py` |
| `GET\|POST /admin/problems/{problem_id}/judgment/interactions` | `arena_admin_problem_judgment_interactions` / `..._interactions_save` | `problem_id=`; invalid inline rows return retained HTML (422) | `admin_problem_judgment.py` |
| `POST /admin/problems/{problem_id}/judgment/interactions/{si_id}/delete` | `arena_admin_problem_judgment_interaction_delete` | `problem_id=`, `si_id=` | `admin_problem_judgment.py` |
| `GET /admin/problems/{problem_id}/testcases/{tc_id}/edit` | `arena_admin_problem_tc_edit` | `problem_id=`, `tc_id=`; Back/Cancel return to `#tc-{tc_id}` on judgment data | `admin_problem_tc.py` |
| `POST /admin/problems/{problem_id}/testcases/{tc_id}/edit` | `arena_admin_problem_tc_update` | `problem_id=`, `tc_id=` | `admin_problem_tc.py` |
| `POST /admin/problems/{problem_id}/testcases/{tc_id}/move` | `arena_admin_problem_tc_move` | `problem_id=`, `tc_id=`, Query: `new_ordinal` | `admin_problem_tc.py` |
| `GET /admin/problems/{problem_id}/testcases/{tc_id}/download` | `arena_admin_problem_tc_download` | `problem_id=`, `tc_id=` | `admin_problem_tc.py` |
| `POST /admin/problems/{problem_id}/testcases/{tc_id}/replace` | `arena_admin_problem_tc_replace` | `problem_id=`, `tc_id=` | `admin_problem_tc.py` (deprecated) |

## Notes

- `GET /` performs a plain 302 redirect to `/dashboard` and has no endpoint name.
- For StaticFiles mounts, `path=` is the filename relative to the mount directory (no leading slash).
| `POST /admin/problems/{problem_id}/validator` | `arena_admin_problem_validator_upload` | `problem_id=` (deprecated: the editor uploads through the problem Save) |
| `GET /admin/problems/{problem_id}/validator/status` | `arena_admin_problem_validator_status` | `problem_id=` |
| `GET /admin/problems/{problem_id}/validator/source` | `arena_admin_problem_validator_download` | `problem_id=` |
| `GET /admin/problems/{problem_id}/validator/source/view` | `arena_admin_problem_validator_source_view` | `problem_id=` |
| `POST /admin/problems/{problem_id}/validator/remove` | `arena_admin_problem_validator_remove` | `problem_id=`, Form: `keep_interactions` (`"true"`/`"false"`, required) (deprecated: the editor removes through the problem Save) |
| `GET /admin/problems/{problem_id}/interactions/{si_id}/edit` | `arena_admin_problem_interaction_edit` | `problem_id=`, `si_id=` |
| `POST /admin/problems/{problem_id}/interactions/{si_id}/edit` | `arena_admin_problem_interaction_update` | `problem_id=`, `si_id=` |
| `POST /admin/problems/{problem_id}/interactions/{si_id}/move` | `arena_admin_problem_interaction_move` | `problem_id=`, `si_id=`, Query: `new_ordinal` |
