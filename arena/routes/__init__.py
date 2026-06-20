#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena route packages."""

from arena.routes.auth import router as auth_router
from arena.routes.legal import router as legal_router
from arena.routes.root import router as root_router
from arena.routes.users import router as users_router

__all__ = ["auth_router", "legal_router", "root_router", "users_router"]
