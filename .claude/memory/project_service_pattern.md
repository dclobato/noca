---
name: service layer pattern for user operations
description: User operations (create, update, remove, batch import) must live in web/services/, not routes. Routes orchestrate only.
type: project
---

User operations (create, update, remove, batch import) belong in `web/services/`, not in route files.

**Why:** Clean separation of concerns — routes only orchestrate calls to service functions and render templates. Business logic lives in services.

**How to apply:** When adding any user management feature (contest users, uberadmins, etc.), put the logic in a service file under `web/services/`. Route handlers should call service functions and pass results to templates — no ORM queries or business rules in route files.

Reference implementation: `web/services/contest_user_service.py`