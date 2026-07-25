#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena image-upload routes and their streaming byte ceilings."""

from shared.services.multipart_file_size import MultipartFileSizeRule
from shared.services.problem_image import MAX_PROBLEM_IMAGE_BYTES

ARENA_LOGO_MAX_FILE_SIZE = 2 * 1024 * 1024


def arena_image_upload_rules(max_file_size: int) -> tuple[MultipartFileSizeRule, ...]:
    """Return fail-fast multipart rules for every Arena image-upload route."""
    return (
        MultipartFileSizeRule(
            path_pattern=r"^/auth/signup$",
            max_file_size=max_file_size,
            label="Profile photo",
            field_names=frozenset({"profile_photo", "foto_cropada"}),
        ),
        MultipartFileSizeRule(
            path_pattern=r"^/user/profile/photo$",
            max_file_size=max_file_size,
            label="Profile photo",
        ),
        MultipartFileSizeRule(
            path_pattern=r"^/admin/affiliations/[^/]+/logo$",
            max_file_size=ARENA_LOGO_MAX_FILE_SIZE,
            label="Affiliation logo",
        ),
        MultipartFileSizeRule(
            path_pattern=r"^/admin/problems/new$",
            max_file_size=MAX_PROBLEM_IMAGE_BYTES,
            label="Problem image",
            field_names=frozenset({"image"}),
        ),
        MultipartFileSizeRule(
            path_pattern=r"^/admin/problems/[^/]+/edit$",
            max_file_size=MAX_PROBLEM_IMAGE_BYTES,
            label="Problem image",
            field_names=frozenset({"image"}),
        ),
    )
