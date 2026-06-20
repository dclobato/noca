#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from sqlalchemy.orm import Mapped

from shared.db_schema import languages as languages_table
from web.database import Base


class Language(Base):
    __table__ = languages_table

    id: Mapped[str]
    name: Mapped[str]
    icon: Mapped[str]
    compile_image: Mapped[str]
    run_image: Mapped[str]
    compile_cmd: Mapped[list[str] | None]
    run_cmd: Mapped[list[str]]
    source_filename: Mapped[str]
    artifact_path: Mapped[str]
    artifact_is_source: Mapped[bool]
    compile_timeout_s: Mapped[float]
    profiling_repetitions_default: Mapped[int]
    profiled_pids_floor: Mapped[int]
    active: Mapped[bool]
