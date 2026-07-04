# url_for() Reference

Quick lookup for writing new templates or debugging `url_for` calls.
All names are stable — changing a route's path no longer breaks templates.

## Static Assets

| `url_for` call | Generated path | Mount name |
|---|---|---|
| `request.url_for('static_js', path='<file>.js')` | `/static/js/<file>.js` | `static_js` |
| `request.url_for('static_css', path='<file>.css')` | `/static/css/<file>.css` | `static_css` |
| `request.url_for('static_shared_js', path='<file>.js')` | `/static/shared-js/<file>.js` | `static_shared_js` |
| `request.url_for('static_shared_css', path='<file>.css')` | `/static/shared-css/<file>.css` | `static_shared_css` |
| `request.url_for('static_img', path='<file>')` | `/static/img/<file>` | `static_img` |
| `request.url_for('static_vendor', path='<file>')` | `/static/vendor/<file>` | `static_vendor` |
| `request.url_for('static_webfonts', path='<file>')` | `/static/webfonts/<file>` | `static_webfonts` |

## Public / Auth Routes (`auth.py`, `root.py`)

| Hardcoded path | Endpoint name | Path params | File |
|---|---|---|---|
| `/` or `/contests` | `contests_list` | — | `root.py` |
| `GET /login` | `login_get` | — | `auth.py` |
| `POST /login` | `login_post` | — | `auth.py` |
| `/logout` | `logout` | — | `auth.py` |
| `GET /c/{slug}/login` | `contest_login_get` | `slug=` | `auth.py` |
| `POST /c/{slug}/login` | `contest_login_post` | `slug=` | `auth.py` |

## Health Route (`health.py`)

The endpoint returns `200` when PostgreSQL and Valkey are available, or `503`
with a degraded payload when either required backend is unavailable.

| Hardcoded path | Endpoint name | Path params | File |
|---|---|---|---|
| `GET /health` | `health` | — | `health.py` |

## UberAdmin Routes (`uberadmin_dashboard.py`)

| Hardcoded path | Endpoint name | Path params | File |
|---|---|---|---|
| `GET /uberadmin/` | `uberadmin_dashboard` | — | `uberadmin_dashboard.py` |
| `GET /uberadmin/security-events` | `uberadmin_security_events` | `event_type=`, `per_page=`, `page=` | `uberadmin_security.py` |
| `GET /uberadmin/uberadmins` | `list_uberadmins_route` | — | `uberadmin_users.py` |
| `GET /uberadmin/uberadmins/new` | `add_uberadmin` | — | `uberadmin_dashboard.py` |
| `POST /uberadmin/uberadmins/new` | `add_uberadmin_submit` | — | `uberadmin_dashboard.py` |
| `GET /uberadmin/uberadmins/{uberadmin_id}/edit` | `edit_uberadmin_form` | `uberadmin_id=` | `uberadmin_users.py` |
| `POST /uberadmin/uberadmins/{uberadmin_id}/edit` | `edit_uberadmin_submit` | `uberadmin_id=` | `uberadmin_users.py` |
| `POST /uberadmin/uberadmins/{uberadmin_id}/toggle` | `toggle_uberadmin_route` | `uberadmin_id=` | `uberadmin_users.py` |
| `POST /uberadmin/uberadmins/credentials.json` | `download_uberadmin_credentials_json` | — | `uberadmin_dashboard.py` |
| `GET /uberadmin/contests/new` | `add_contest` | — | `uberadmin_dashboard.py` |
| `POST /uberadmin/contests/new` | `add_contest_submit` | — | `uberadmin_dashboard.py` |
| `GET /uberadmin/contests/inactive` | `uberadmin_inactive_contests` | — | `uberadmin_dashboard.py` |
| `POST /uberadmin/contests/{contest_id}/deactivate` | `uberadmin_deactivate_contest` | `contest_id=` | `uberadmin_dashboard.py` |
| `POST /uberadmin/contests/credentials.json` | `download_contest_credentials_json` | — | `uberadmin_dashboard.py` |
| `POST /uberadmin/contests/credentials/email` | `send_contest_credentials_email` | — | `uberadmin_dashboard.py` |

## Contest Dashboard Routes (`generaluser_dashboard.py`)

| Hardcoded path | Endpoint name | Path params | File |
|---|---|---|---|
| `GET /c/{slug}/` | `contest_dashboard` | `slug=` | `generaluser_dashboard.py` |
| `GET /c/{slug}/clock` | `contest_clock` | `slug=` | `generaluser_dashboard.py` |

## Contest Module Routes

| Hardcoded path | Endpoint name | Path params | File |
|---|---|---|---|
| `GET /c/{slug}/scoreboard/` | `contest_score` | `slug=` | `contest_score.py` |
| `GET /c/{slug}/problems/` | `contest_problems` | `slug=` | `contest_problems.py` |
| `GET /c/{slug}/problems/{problem_label}` | `contest_problem_detail` | `slug=`, `problem_label=` | `contest_problems.py` |
| `GET /c/{slug}/problems/{problem_label}/statement` | `contest_problem_statement` | `slug=`, `problem_label=` | `contest_problems.py` |
| `GET /c/{slug}/problems/{problem_label}/export` | `contest_problem_export` | `slug=`, `problem_label=` | `contest_problems.py` |
| `GET /c/{slug}/clarifications/` | `contest_clarifications` | `slug=` | `contest_clarifications.py` |
| `GET /c/{slug}/clarifications/list` | `contest_clarifications_list` | `slug=` | `contest_clarifications.py` |
| `POST /c/{slug}/clarifications/new` | `contest_clarifications_new` | `slug=` | `contest_clarifications_submit.py` |
| `POST /c/{slug}/clarifications/announcement` | `contest_clarifications_announcement` | `slug=` | `contest_clarifications_submit.py` |
| `POST /c/{slug}/clarifications/acquire` | `contest_clarifications_acquire` | `slug=` | `contest_clarifications_judge.py` |
| `GET /c/{slug}/clarifications/answer` | `contest_clarifications_answer` | `slug=` | `contest_clarifications_judge.py` |
| `POST /c/{slug}/clarifications/answer` | `contest_clarifications_answer_submit` | `slug=` | `contest_clarifications_judge.py` |
| `GET /c/{slug}/clarifications/hide` | `contest_clarifications_hide` | `slug=` | `contest_clarifications_admin.py` |
| `POST /c/{slug}/clarifications/hide` | `contest_clarifications_hide_submit` | `slug=` | `contest_clarifications_admin.py` |
| `GET /c/{slug}/clarifications/togglehide` | `contest_clarifications_togglehide` | `slug=` | `contest_clarifications_admin.py` |
| `POST /c/{slug}/clarifications/togglehide` | `contest_clarifications_togglehide_submit` | `slug=` | `contest_clarifications_admin.py` |
| `POST /c/{slug}/clarifications/releaselock` | `contest_clarifications_releaselock` | `slug=` | `contest_clarifications_admin.py` |
| `GET /c/{slug}/runs/` | `contest_runs` | `slug=` | `contest_runs.py` |
| `GET /c/{slug}/runs/list` | `contest_runs_list` | `slug=` | `contest_runs.py` |
| `GET /c/{slug}/runs/language-info` | `contest_runs_language_info` | `slug=` | `contest_runs.py` |
| `GET /c/{slug}/runs/events` | `contest_runs_events` | `slug=` | `contest_runs_events.py` |
| `POST /c/{slug}/runs/submit` | `contest_runs_submit` | `slug=` | `contest_runs_review.py` |
| `POST /c/{slug}/runs/{submission_id}/override` | `contest_runs_override` | `slug=`, `submission_id=` | `contest_runs_review.py` |
| `GET /c/{slug}/runs/{submission_id}/judging-history` | `contest_runs_judging_history` | `slug=`, `submission_id=` | `contest_runs_events.py` |
| `GET /c/{slug}/live` | `contest_live` | `slug=` | `contest_live_feed.py` |
| `GET /c/{slug}/live/feed.json` | `contest_live_feed` | `slug=` | `contest_live_feed.py` |
| `GET /c/{slug}/live/events` | `contest_live_events` | `slug=` | `contest_live_feed.py` |
| `GET /c/{slug}/tasks/` | `contest_tasks` | `slug=` | `contest_tasks.py` |
| `GET /c/{slug}/tasks/list` | `contest_tasks_list` | `slug=` | `contest_tasks.py` |
| `POST /c/{slug}/tasks/sos` | `contest_tasks_sos` | `slug=` | `contest_tasks.py` |
| `POST /c/{slug}/tasks/print` | `contest_tasks_print` | `slug=` | `contest_tasks.py` |
| `POST /c/{slug}/tasks/{task_id}/acquire` | `contest_tasks_acquire` | `slug=`, `task_id=` | `contest_tasks_staff.py` |
| `POST /c/{slug}/tasks/{task_id}/finish` | `contest_tasks_finish` | `slug=`, `task_id=` | `contest_tasks_staff.py` |
| `POST /c/{slug}/tasks/{task_id}/release` | `contest_tasks_release` | `slug=`, `task_id=` | `contest_tasks_staff.py` |
| `GET /c/{slug}/tasks/{task_id}/source` | `contest_tasks_source` | `slug=`, `task_id=` | `contest_tasks_staff.py` |
| `GET /c/{slug}/reports/` | `contest_reports` | `slug=` | `contest_reports.py` |
| `GET /c/{slug}/submissions/download-all` | `team_submissions_download` | `slug=` | `contest_submissions.py` |
| `GET /c/{slug}/submissions/{submission_id}/review` | `submission_review` | `slug=`, `submission_id=` | `contest_submissions.py` |
| `POST /c/{slug}/submissions/{submission_id}/acquire-review` | `submission_acquire_review` | `slug=`, `submission_id=` | `contest_submissions_review.py` |
| `POST /c/{slug}/submissions/{submission_id}/release-review` | `submission_release_review` | `slug=`, `submission_id=` | `contest_submissions_review.py` |
| `POST /c/{slug}/submissions/{submission_id}/confirm` | `submission_confirm_post` | `slug=`, `submission_id=` | `contest_submissions_review.py` |
| `POST /c/{slug}/submissions/{submission_id}/rejudge` | `submission_rejudge_post` | `slug=`, `submission_id=` | `contest_submissions_review.py` |
| `GET /c/{slug}/submissions/{submission_id}/source` | `submission_source_download` | `slug=`, `submission_id=` | `contest_submissions_files.py` |
| `GET /c/{slug}/submissions/{submission_id}/test-cases/{test_case_id}/download` | `submission_test_case_download` | `slug=`, `submission_id=`, `test_case_id=` | `contest_submissions_files.py` |
| `GET /c/{slug}/submissions/{submission_id}/test-cases/{test_case_id}/detail` | `submission_tc_detail` | `slug=`, `submission_id=`, `test_case_id=` | `contest_submissions_files.py` |

## Contest Clarification Routes (`contest_clarifications.py`)

(This section is redundant; see Contest Module Routes above)

## Contest Administration Routes (`contest_admin*.py`)

`edit_metadata` / `edit_metadata_submit` now also cover allowed-language edits before contest start; the endpoint names and paths are unchanged.

| Hardcoded path | Endpoint name | Path params | File |
|---|---|---|---|
| `GET /c/{slug}/admin/` | `view` | `slug=` | `contest_admin.py` |
| `GET /c/{slug}/admin/counters` | `contest_admin_counters` | `slug=` | `contest_admin.py` |
| `GET /c/{slug}/admin/metadata` | `edit_metadata` | `slug=` | `contest_admin_metadata.py` |
| `POST /c/{slug}/admin/metadata` | `edit_metadata_submit` | `slug=` | `contest_admin_metadata.py` |
| `GET /c/{slug}/admin/users` | `manage_users` | `slug=` | `contest_admin_reports.py` |
| `GET /c/{slug}/admin/import_export` | `import_export` | `slug=` | `contest_admin_export.py` |
| `GET /c/{slug}/admin/export-animeitor` | `export_animeitor` | `slug=` | `contest_admin_export.py` |
| `GET /c/{slug}/admin/export-events` | `export_contest_timeline` | `slug=` | `contest_admin_export.py` |
| `GET /c/{slug}/admin/users-per-site-report` | `users_per_site_report` | `slug=` | `contest_admin_export.py` |
| `POST /c/{slug}/admin/start-now` | `contest_start_now` | `slug=` | `contest_admin.py` |
| `POST /c/{slug}/admin/end-now` | `contest_end_now` | `slug=` | `contest_admin.py` |
| `POST /c/{slug}/admin/chief-judge` | `contest_admin_set_chief_judge` | `slug=` | `contest_admin.py` |
| `POST /c/{slug}/admin/release-scoreboard` | `contest_admin_release_scoreboard` | `slug=` | `contest_admin.py` |

## Contest Problem Management Routes

Core routes (`contest_admin_problem.py`):

| Hardcoded path | Endpoint name | Path params | File |
|---|---|---|---|
| `GET /c/{slug}/admin/problems` | `manage_problems` | `slug=` | `contest_admin_problem.py` |
| `GET /c/{slug}/admin/problems/new` | `new_problem_form` | `slug=` | `contest_admin_problem.py` |
| `POST /c/{slug}/admin/problems/new` | `new_problem_submit` | `slug=` | `contest_admin_problem.py` |
| `POST /c/{slug}/admin/problems/{problem_id}/move` | `move_problem_htmx` | `slug=`, `problem_id=` | `contest_admin_problem.py` |
| `POST /c/{slug}/admin/problems/{problem_id}/remove` | `remove_problem` | `slug=`, `problem_id=` | `contest_admin_problem.py` |

Edit routes (`contest_admin_problem_edit.py`):

| Hardcoded path | Endpoint name | Path params | File |
|---|---|---|---|
| `GET /c/{slug}/admin/problems/{problem_id}/edit` | `edit_problem_form` | `slug=`, `problem_id=` | `contest_admin_problem_edit.py` |
| `POST /c/{slug}/admin/problems/{problem_id}/edit` | `edit_problem_submit` | `slug=`, `problem_id=` | `contest_admin_problem_edit.py` |

Limits routes (`contest_admin_problem_limits.py`):

| Hardcoded path | Endpoint name | Path params | File |
|---|---|---|---|
| `POST /c/{slug}/admin/problems/{problem_id}/profiling` | `enqueue_problem_profiling` | `slug=`, `problem_id=` | `contest_admin_problem_limits.py` |
| `GET /c/{slug}/admin/problems/{problem_id}/profiling-status` | `problem_profiling_status_partial` | `slug=`, `problem_id=` | `contest_admin_problem_limits.py` |
| `POST /c/{slug}/admin/problems/{problem_id}/fallback-limits` | `apply_problem_fallback_limits` | `slug=`, `problem_id=` | `contest_admin_problem_limits.py` |
| `GET /c/{slug}/admin/problems/{problem_id}/limit-change-batches/{batch_id}` | `problem_limit_change_batch_review` | `slug=`, `problem_id=`, `batch_id=` | `contest_admin_problem_limits.py` |
| `POST /c/{slug}/admin/problems/{problem_id}/limit-change-batches/{batch_id}/rejudge-all` | `problem_limit_change_batch_rejudge_all` | `slug=`, `problem_id=`, `batch_id=` | `contest_admin_problem_limits.py` |
| `POST /c/{slug}/admin/problems/{problem_id}/limit-change-batches/{batch_id}/languages/{language_id}/rejudge` | `problem_limit_change_batch_rejudge_language` | `slug=`, `problem_id=`, `batch_id=`, `language_id=` | `contest_admin_problem_limits.py` |

Categories routes (`contest_admin_problem_categories.py`):

| Hardcoded path | Endpoint name | Path params | File |
|---|---|---|---|
| `GET /categories/autocomplete` | `problem_categories_autocomplete` | — | `contest_admin_problem_categories.py` |
| `GET /categories` | `manage_categories` | — | `contest_admin_problem_categories.py` |
| `GET /categories/new` | `new_category_form` | — | `contest_admin_problem_categories.py` |
| `POST /categories/new` | `new_category_submit` | — | `contest_admin_problem_categories.py` |
| `GET /categories/{category_id}/edit` | `edit_category_form` | `category_id=` | `contest_admin_problem_categories.py` |
| `POST /categories/{category_id}/edit` | `edit_category_submit` | `category_id=` | `contest_admin_problem_categories.py` |
| `POST /categories/{category_id}/delete` | `delete_category` | `category_id=` | `contest_admin_problem_categories.py` |

Import / export / serve routes (`contest_admin_problem_io.py`):

| Hardcoded path | Endpoint name | Path params | File |
|---|---|---|---|
| `GET /c/{slug}/admin/problems/import` | `import_problem_form` | `slug=` | `contest_admin_problem_io.py` |
| `POST /c/{slug}/admin/problems/import` | `import_problem_submit` | `slug=` | `contest_admin_problem_io.py` |
| `GET /c/{slug}/admin/problems/{problem_id}/statement` | `problem_statement` | `slug=`, `problem_id=` | `contest_admin_problem_io.py` |
| `GET /c/{slug}/admin/problems/{problem_id}/export` | `export_problem` | `slug=`, `problem_id=` | `contest_admin_problem_io.py` |

Test case routes (`contest_admin_problem_tc.py`):

| Hardcoded path | Endpoint name | Path params | File |
|---|---|---|---|
| `POST /c/{slug}/admin/problems/{problem_id}/test-cases/zip` | `upload_testcase_zip` | `slug=`, `problem_id=` | `contest_admin_problem_tc.py` |
| `GET /c/{slug}/admin/problems/{problem_id}/test-cases/new` | `new_test_case_form` | `slug=`, `problem_id=` | `contest_admin_problem_tc.py` |
| `POST /c/{slug}/admin/problems/{problem_id}/test-cases/add` | `add_test_case` | `slug=`, `problem_id=` | `contest_admin_problem_tc.py` |
| `POST /c/{slug}/admin/problems/{problem_id}/test-cases/add-zip` | `add_test_case_zip` | `slug=`, `problem_id=` | `contest_admin_problem_tc.py` |
| `GET /c/{slug}/admin/problems/{problem_id}/test-cases/{tc_id}/edit` | `edit_test_case_form` | `slug=`, `problem_id=`, `tc_id=` | `contest_admin_problem_tc.py` |
| `POST /c/{slug}/admin/problems/{problem_id}/test-cases/{tc_id}/edit` | `edit_test_case` | `slug=`, `problem_id=`, `tc_id=` | `contest_admin_problem_tc.py` |
| `POST /c/{slug}/admin/problems/{problem_id}/test-cases/{tc_id}/toggle-sample` | `toggle_test_case_sample` | `slug=`, `problem_id=`, `tc_id=` | `contest_admin_problem_tc.py` |
| `POST /c/{slug}/admin/problems/{problem_id}/test-cases/{tc_id}/remove` | `remove_test_case_route` | `slug=`, `problem_id=`, `tc_id=` | `contest_admin_problem_tc.py` |
| `POST /c/{slug}/admin/problems/{problem_id}/test-cases/{tc_id}/move` | `move_test_case_route` | `slug=`, `problem_id=`, `tc_id=` | `contest_admin_problem_tc.py` |
| `GET /c/{slug}/admin/problems/{problem_id}/test-cases/{tc_id}/download` | `download_test_case` | `slug=`, `problem_id=`, `tc_id=` | `contest_admin_problem_tc.py` |
| `POST /c/{slug}/admin/problems/{problem_id}/test-cases/{tc_id}/replace` | `replace_test_case` | `slug=`, `problem_id=`, `tc_id=` | `contest_admin_problem_tc.py` |

Shared helpers (`contest_admin_problem_helpers.py` and
`contest_admin_problem_limits_helpers.py`) — no routes.

## Contest User Management Routes (`contest_admin_user.py`)

| Hardcoded path | Endpoint name | Path params | File |
|---|---|---|---|
| `GET /c/{slug}/admin/users/new` | `add_user_form` | `slug=` | `contest_admin_user.py` |
| `POST /c/{slug}/admin/users/new` | `add_user_submit` | `slug=` | `contest_admin_user.py` |
| `POST /c/{slug}/admin/users/credentials.json` | `download_user_credentials` | `slug=` | `contest_admin_user.py` |
| `POST /c/{slug}/admin/users/credentials/email` | `send_single_user_credentials_email` | `slug=` | `contest_admin_user.py` |
| `GET /c/{slug}/admin/users/batch` | `batch_import_form` | `slug=` | `contest_admin_user_batch.py` |
| `POST /c/{slug}/admin/users/batch` | `batch_import_submit` | `slug=` | `contest_admin_user_batch.py` |
| `POST /c/{slug}/admin/users/batch/results.json` | `download_batch_results` | `slug=` | `contest_admin_user_batch.py` |
| `POST /c/{slug}/admin/users/batch/credentials/email` | `send_batch_credentials_email` | `slug=` | `contest_admin_user_batch.py` |
| `GET /c/{slug}/admin/users/export.json` | `export_users` | `slug=` | `contest_admin_user_edit.py` |
| `GET /c/{slug}/admin/users/{user_id}/edit` | `edit_user_form` | `slug=`, `user_id=` | `contest_admin_user_edit.py` |
| `POST /c/{slug}/admin/users/{user_id}/edit` | `edit_user_submit` | `slug=`, `user_id=` | `contest_admin_user_edit.py` |
| `POST /c/{slug}/admin/users/{user_id}/remove` | `remove_user_route` | `slug=`, `user_id=` | `contest_admin_user_edit.py` |

## Profile Routes (`profile.py`)

| Hardcoded path | Endpoint name | Path params | File |
|---|---|---|---|
| `GET /profile` | `profile_get` | — | `profile.py` |
| `POST /profile/fullname` | `profile_fullname_submit` | — | `profile.py` |
| `POST /profile/email` | `profile_email_submit` | — | `profile.py` |
| `POST /profile/password` | `profile_password_submit` | — | `profile.py` |
| `POST /user/{user_id}/photo` | `user_photo_submit` | `user_id=` | `profile.py` |
| `POST /user/{user_id}/photo/remove` | `user_photo_remove` | `user_id=` | `profile.py` |

## Asset Routes (`profile.py`)

| Hardcoded path | Endpoint name | Path params | File |
|---|---|---|---|
| `GET /user/{user_id}/avatar` | `user_avatar_by_id` | `user_id=` | `profile.py` |
| `GET /user/{user_id}/photo` | `user_photo_by_id` | `user_id=` | `profile.py` |

## Notes

- Several route functions are named `view`, but only explicitly named routes are stable for `url_for(...)`. Prefer the endpoint names listed above instead of relying on function names.
- For StaticFiles mounts, `path=` is the filename relative to the mount directory (no leading slash).
- Trailing slashes: routes mounted with `prefix + "/"` (e.g. `/c/{slug}/clarifications/`) get a trailing slash in `url_for` output. FastAPI redirects the slash-less version automatically.
