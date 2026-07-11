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
| `request.url_for('arena_static_img', path='<file>')` | `/static/img/<file>` | `arena_static_img` |
| `request.url_for('static_vendor', path='<file>')` | `/static/vendor/<file>` | `static_vendor` |
| `request.url_for('static_webfonts', path='<file>')` | `/static/webfonts/<file>` | `static_webfonts` |

## Health Route (`health.py`)

Use this endpoint for runtime health probes.

| Hardcoded path | Endpoint name | Path params | File |
|---|---|---|---|
| `GET /health` | `arena_health` | — | `health.py` |

## Help Routes (`arena/routes/help.py`)

| Hardcoded path | Endpoint name | Path params | File |
|---|---|---|---|
| `GET /help/rating` | `arena_help_rating` | — | `help.py` |
| `GET /help/rating/difficulty-distribution` | `arena_help_difficulty_distribution` | — | `help.py` |
| `GET /help/languages` | `arena_help_languages` | — | `help.py` |

## Legal Routes (`arena/routes/legal.py`)

| Hardcoded path | Endpoint name | Path params | File |
|---|---|---|---|
| `GET /legal/terms` | `arena_terms_of_service` | — | `legal.py` |
| `GET /legal/privacy` | `arena_privacy_policy` | — | `legal.py` |

## Public Problem Routes (`arena/routes/problems.py`)

| `url_for` call | Generated path | Notes |
|---|---|---|
| `request.url_for('arena_problem_list')` | `/problems` | Query: `search`, `sort_by` (`number_asc`, `number_desc`, `title_asc`, `title_desc`, `solvers_asc`, `solvers_desc`, `rating_asc`, `rating_desc`), `category_slugs`, `page` |
| `request.url_for('arena_problem_detail', arena_number=N)` | `/problems/{N}` | Query: `back_page`, `back_search`, `back_sort_by`, `back_category_slugs` |
| `request.url_for('arena_problem_rating_history_public', arena_number=N)` | `/problems/{N}/rating-history` | Returns JSON `{history:[…]}` |
| `request.url_for('arena_problem_statistics', arena_number=N)` | `/problems/{N}/statistics` | Per-problem statistics page |
| `request.url_for('arena_problem_statistics_data', arena_number=N)` | `/problems/{N}/statistics.json` | Returns the precomputed statistics payload, or `{}` |
| `request.url_for('arena_problem_sample_testcases_zip', arena_number=N)` | `/problems/{N}/sample-testcases.zip` | Returns `application/zip` download of public sample test cases (Layout A) |
| `request.url_for('arena_problem_submit', arena_number=N)` | `/problems/{N}/submit` | POST only; requires auth; form fields `language_id`, `source_code`, and optional `problem_set_id` (ties the submission to a problem set so its teacher can see it; omitted = private) |
| `request.url_for('arena_problem_toggle_favorite', arena_number=N)` | `/problems/{N}/favorite` | POST only; requires auth; returns `{"is_favorite": bool}`; guests get 401 |
| `request.url_for('arena_problem_request_removal', arena_number=N)` | `/problems/{N}/request-removal` | POST only; requires auth; owner without edit rights only; notifies all ARENA_ADMINs |

## Public Routes (`arena/routes/root.py`)

| Hardcoded path | Endpoint name | Path params | File |
|---|---|---|---|
| `GET /` | *(redirect, no name)* | — | `root.py` |
| `GET /dashboard` | `arena_dashboard` | — | `root.py` |
| `GET /status` | `arena_status` | — | `status.py` |
| `GET /<favicon asset>` | `arena_favicon_<file>` *(not used in templates; base templates reference the literal root paths)* | — | `root.py` |
| `GET /live` | `arena_live` | — | `live.py` |
| `GET /live/feed.json` | `arena_live_feed` | — | `live.py` |
| `GET /live/events` | `arena_live_events` | — | `live.py` |

## Submission Routes (`arena/routes/submissions.py`)

| `url_for` call | Generated path | Notes |
|---|---|---|
| `request.url_for('arena_submission_detail', submission_id=ID)` | `/submissions/{ID}` | Requires auth; 404 if not owned by user (unless ARENA_ADMIN); owner confirms AI review requests in a balance-preview modal and sees pending, batch-queued, or completed review states for non-AC submissions |
| `request.url_for('arena_submission_request_ai_review', submission_id=ID)` | `/submissions/{ID}/request-ai-review` | POST only; owner-only (no admin bypass); idempotent; requires `ai_api_key` or `ai_backend_credits > 0`; consumes one credit when using platform key |
| `request.url_for('arena_submission_teacher_feedback', submission_id=ID)` | `/submissions/{ID}/teacher-feedback` | POST only; manager-only (set's teacher or ARENA_ADMIN); non-AC, set-tied submissions; upserts feedback and notifies the student; `back_class_id`/`back_set_id`/`back_user_id` form fields are navigation-only |

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
| `GET /auth/login` | `arena_login` | Optional query: `next` (same-origin path target) | `auth.py` |
| `POST /auth/login` | `arena_login_submit` | Optional form: `next` (same-origin path target) | `auth.py` |
| `POST /auth/resend-activation` | `arena_resend_activation` | — | `auth.py` |
| `POST /auth/resend-parental-consent` | `arena_resend_parental_consent` | — | `auth.py` |
| `POST /auth/update-parental-email` | `arena_update_parental_email` | — | `auth.py` |
| `POST /auth/update-date-of-birth` | `arena_update_date_of_birth` | — | `auth.py` |
| `GET /auth/change-password` | `arena_change_password` | — | `auth.py` |
| `POST /auth/change-password` | `arena_change_password_submit` | — | `auth.py` |
| `GET /auth/2fa` | `arena_2fa` | — | `auth_2fa.py` |
| `POST /auth/2fa` | `arena_2fa_submit` | — | `auth_2fa.py` |
| `GET /auth/signup` | `arena_signup` | — | `auth.py` |
| `POST /auth/signup` | `arena_signup_submit` | — | `auth.py` |
| `POST /auth/logout` | `arena_logout` | — | `auth.py` |
| `GET /auth/accept-terms` | `arena_accept_terms` | — | `auth.py` |
| `POST /auth/accept-terms` | `arena_accept_terms_submit` | — | `auth.py` |
| `GET /auth/activate` | `arena_activate` | Query: `token` | `auth.py` |
| `GET /auth/parental-consent` | `arena_parental_consent` | Query: `token` | `auth.py` |
| `GET /auth/password-reset` | `arena_password_reset` | Optional query: `token` | `auth.py` |
| `POST /auth/password-reset` | `arena_password_reset_submit` | — | `auth.py` |

## User Routes (`arena/routes/users.py`)

| Hardcoded path | Endpoint name | Path params | File |
|---|---|---|---|
| `GET /user/profile` | `arena_user_profile` | Query: `tab` (`badges` supported), `solved_page`, `attempted_page`, `notifications_page` | `users.py` |
| `GET /user/profile/complete` | `arena_user_profile_completion` | — | `users.py` |
| `POST /user/profile/photo` | `arena_user_profile_photo_update` | — | `users.py` |
| `POST /user/profile/personal-data` | `arena_user_profile_personal_data_update` | JSON: `name`, `date_of_birth`, optional location/affiliation/programming language, `prefered_language` | `user_profile_api.py` |
| `POST /user/profile/notifications/{notification_id}/delete` | `arena_user_profile_notification_delete` | `notification_id=` | `users.py` |
| `POST /user/profile/notifications/delete-all` | `arena_user_profile_notifications_delete_all` | — | `users.py` |
| `POST /user/profile/notifications/mark-all-read` | `arena_user_profile_notifications_mark_all_read` | — | `users.py` |
| `GET /user/profile/subdivisions` | `arena_user_profile_subdivisions` | Query: `country_code` | `user_profile_api.py` |
| `POST /user/profile/location` | `arena_user_profile_location_update` | — | `user_profile_api.py` |
| `POST /user/profile/location/detect` | `arena_user_profile_location_detect` | — | `user_profile_api.py` |
| `GET /user/profile/affiliations/search` | `arena_user_profile_affiliations_search` | Query: `q` | `user_profile_api.py` |
| `POST /user/profile/affiliation` | `arena_user_profile_affiliation_update` | — | `user_profile_api.py` |
| `POST /user/profile/language` | `arena_user_profile_language_update` | — | `user_profile_api.py` |
| `GET /user/profile/rating-history` | `arena_user_profile_rating_history` | — | `user_profile_api.py` |
| `GET /user/profile/submission-heatmap` | `arena_user_profile_submission_heatmap` | — | `user_profile_api.py` |
| `POST /user/profile/api-key` | `arena_user_profile_api_key_update` | — | `user_profile_api.py` |
| `GET /user/submissions/status.json` | `arena_user_submissions_status` | Query: `ids` (CSV of owned submission UUIDs) | `user_submission_status.py` |
| `GET /user/submissions/status/events` | `arena_user_submissions_events` | Query: `ids` (CSV of owned submission UUIDs) | `user_submission_status.py` |
| `GET /user/{user_id}/photo` | `arena_user_photo_by_id` | `user_id=` | `users.py` |
| `GET /user/{user_id}/avatar` | `arena_user_avatar_by_id` | `user_id=` | `users.py` |

## Public Profile Routes (`arena/routes/user_public_profile.py`)

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
| `POST /user/profile/2fa/confirm` | `arena_2fa_confirm` | — | `user_security.py` |
| `POST /user/profile/2fa/disable` | `arena_2fa_disable` | — | `user_security.py` |
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
| `GET /admin/dashboard/security-events` | `arena_admin_dashboard_security_events` | `module=`, `event_type=`, `per_page=`, `page=` | `admin_dashboard_history.py` |

## Arena Admin – User Management Routes

GET routes: `arena/routes/admin_users.py` · POST routes: `arena/routes/admin_users_actions.py` · Helpers: `arena/routes/admin_user_route_support.py`

| Hardcoded path | Endpoint name | Path params | File |
|---|---|---|---|
| `GET /admin/users` | `arena_admin_user_list` | `search=`, `role=`, `can_edit=`, `per_page=`, `page=` | `admin_users.py` |
| `GET /admin/users/{user_id}` | `arena_admin_user_profile` | `user_id=`, query: `tab`, `credits_page`, `notifications_page`, `submissions_page`, `submissions_search`, `submissions_verdict`, `login_page`, `login_per_page`, `login_sort_dir`, `login_date_from`, `login_date_to` | `admin_users.py` |
| `GET /admin/users/{user_id}/rating-history` | `arena_admin_user_rating_history` | `user_id=` | `admin_users.py` |
| `GET /admin/users/{user_id}/submission-heatmap` | `arena_admin_user_submission_heatmap` | `user_id=` | `admin_users.py` |
| `POST /admin/users/{user_id}/role` | `arena_admin_user_change_role` | `user_id=` | `admin_users_actions.py` |
| `POST /admin/users/{user_id}/toggle-active` | `arena_admin_user_toggle_active` | `user_id=` | `admin_users_actions.py` |
| `POST /admin/users/{user_id}/force-password-change` | `arena_admin_user_force_pw_change` | `user_id=` | `admin_users_actions.py` |
| `POST /admin/users/{user_id}/remove-photo` | `arena_admin_user_remove_photo` | `user_id=` | `admin_users_actions.py` |
| `POST /admin/users/{user_id}/disable-2fa` | `arena_admin_user_disable_2fa` | `user_id=` | `admin_users_actions.py` |
| `POST /admin/users/{user_id}/change-name` | `arena_admin_user_change_name` | `user_id=` | `admin_users_actions.py` |
| `POST /admin/users/{user_id}/date-of-birth` | `arena_admin_user_change_date_of_birth` | `user_id=` | `admin_users_actions.py` |
| `POST /admin/users/{user_id}/personal-info` | `arena_admin_user_change_personal_info` | `user_id=` | `admin_users_actions.py` |
| `POST /admin/users/{user_id}/remove-location` | `arena_admin_user_remove_location` | `user_id=` | `admin_users_actions.py` |
| `POST /admin/users/{user_id}/remove-affiliation` | `arena_admin_user_remove_affiliation` | `user_id=` | `admin_users_actions.py` |
| `POST /admin/users/{user_id}/reset-api-key` | `arena_admin_user_reset_api_key` | `user_id=` | `admin_users_actions.py` |
| `POST /admin/users/{user_id}/topup-credits` | `arena_admin_user_topup_credits` | `user_id=` | `admin_users_actions.py` |
| `POST /admin/users/{user_id}/toggle-email-confirmed` | `arena_admin_user_toggle_email_confirmed` | `user_id=` | `admin_users_actions.py` |
| `POST /admin/users/{user_id}/toggle-parental-consent` | `arena_admin_user_toggle_parental_consent` | `user_id=` | `admin_users_actions.py` |
| `POST /admin/users/{user_id}/toggle-can-edit` | `arena_admin_user_toggle_can_edit` | `user_id=` | `admin_users_actions.py` |
| `POST /admin/users/{user_id}/toggle-ranking-visible` | `arena_admin_user_toggle_ranking_visible` | `user_id=` | `admin_users_actions.py` |
| `POST /admin/users/{user_id}/toggle-public-profile` | `arena_admin_user_toggle_public_profile` | `user_id=` | `admin_users_actions.py` |

## Arena Admin – Category Management Routes (`arena/routes/admin_categories.py`)

| Hardcoded path | Endpoint name | Path params | File |
|---|---|---|---|
| `GET /admin/categories` | `arena_admin_category_list` | — | `admin_categories.py` |
| `GET /admin/categories/new` | `arena_admin_category_new` | — | `admin_categories.py` |
| `POST /admin/categories/new` | `arena_admin_category_create` | — | `admin_categories.py` |
| `GET /admin/categories/{category_id}/edit` | `arena_admin_category_edit` | `category_id=` | `admin_categories.py` |
| `POST /admin/categories/{category_id}/edit` | `arena_admin_category_update` | `category_id=` | `admin_categories.py` |
| `POST /admin/categories/{category_id}/delete` | `arena_admin_category_delete` | `category_id=` | `admin_categories.py` |

## Arena Admin – Problem Management Routes (`arena/routes/admin_problems.py`)

| Hardcoded path | Endpoint name | Path params | File |
|---|---|---|---|
| `GET /admin/problems` | `arena_admin_problem_list` | Query: `search`, `sort_by`, `owner_id`, `category_slugs`, `per_page`, `page` | `admin_problems.py` |
| `GET /admin/problems/new` | `arena_admin_problem_new` | Optional query: list-return state | `admin_problems.py` |
| `POST /admin/problems/new` | `arena_admin_problem_create` | — | `admin_problems.py` |
| `GET /admin/problems/{problem_id}/edit` | `arena_admin_problem_edit` | `problem_id=`, optional query: list-return state | `admin_problems.py` |
| `POST /admin/problems/{problem_id}/edit` | `arena_admin_problem_update` | `problem_id=` | `admin_problems.py` |
| `POST /admin/problems/{problem_id}/toggle-enabled` | `arena_admin_problem_toggle_enabled` | `problem_id=`, Query: `page`, `per_page`, `search`, `sort_by`, `owner_id`, `category_slugs` | `admin_problems.py` |
| `POST /admin/problems/{problem_id}/delete` | `arena_admin_problem_delete` | `problem_id=`, Form: `password`, list-return state | `admin_problems.py` |
| `POST /admin/problems/{problem_id}/rejudge-all` | `arena_admin_problem_rejudge_all` | `problem_id=`, Form: `password` | `admin_problems.py` |

## Arena Admin – Problem Import/Export Routes (`arena/routes/admin_problem_io.py`)

| Hardcoded path | Endpoint name | Path params | File |
|---|---|---|---|
| `GET /admin/problems/import` | `arena_admin_problem_import_form` | — | `admin_problem_io.py` |
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
| `GET /ranking/users` | `arena_ranking_users` | Query: `search`, `page` | `ranking.py` |
| `GET /ranking/affiliations` | `arena_ranking_affiliations` | Query: `search`, `country_code`, `subdivision_code`, `page` | `ranking.py` |
| `GET /ranking/affiliations/{affiliation_id}/users` | `arena_ranking_affiliation_users` | `affiliation_id=`, Query: `search`, `page` | `ranking.py` |

## Arena Admin – Problem Test Case Routes (`arena/routes/admin_problem_tc.py`)

| Hardcoded path | Endpoint name | Path params | File |
|---|---|---|---|
| `GET /admin/problems/{problem_id}/testcases/new` | `arena_admin_problem_tc_new` | `problem_id=` | `admin_problem_tc.py` |
| `POST /admin/problems/{problem_id}/testcases/add` | `arena_admin_problem_tc_add` | `problem_id=` | `admin_problem_tc.py` |
| `POST /admin/problems/{problem_id}/testcases/add-zip` | `arena_admin_problem_tc_add_from_zip` | `problem_id=` | `admin_problem_tc.py` |
| `GET /admin/problems/{problem_id}/testcases/{tc_id}/edit` | `arena_admin_problem_tc_edit` | `problem_id=`, `tc_id=` | `admin_problem_tc.py` |
| `POST /admin/problems/{problem_id}/testcases/{tc_id}/edit` | `arena_admin_problem_tc_update` | `problem_id=`, `tc_id=` | `admin_problem_tc.py` |
| `POST /admin/problems/{problem_id}/testcases/{tc_id}/toggle-sample` | `arena_admin_problem_tc_toggle_sample` | `problem_id=`, `tc_id=` | `admin_problem_tc.py` |
| `POST /admin/problems/{problem_id}/testcases/{tc_id}/move` | `arena_admin_problem_tc_move` | `problem_id=`, `tc_id=`, Query: `new_ordinal` | `admin_problem_tc.py` |
| `POST /admin/problems/{problem_id}/testcases/zip-replace` | `arena_admin_problem_tc_zip_replace` | `problem_id=` | `admin_problem_tc.py` |
| `GET /admin/problems/{problem_id}/testcases/{tc_id}/download` | `arena_admin_problem_tc_download` | `problem_id=`, `tc_id=` | `admin_problem_tc.py` |
| `POST /admin/problems/{problem_id}/testcases/{tc_id}/replace` | `arena_admin_problem_tc_replace` | `problem_id=`, `tc_id=` | `admin_problem_tc.py` |

## Notes

- `GET /` performs a plain 302 redirect to `/dashboard` and has no endpoint name.
- For StaticFiles mounts, `path=` is the filename relative to the mount directory (no leading slash).
