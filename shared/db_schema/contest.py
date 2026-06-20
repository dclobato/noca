#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SAEnum

from shared.enumerations import TaskType

from ._base import _created_at_column, _id_column, _updated_at_column, metadata

contests = Table(
    "contests",
    metadata,
    _id_column(),
    Column("contest_name", String(128), nullable=False, comment="Nome da competição"),
    Column("contest_url", String(256), nullable=False, comment="URL do site principal da competição"),
    Column(
        "login_slug",
        String(80),
        nullable=False,
        unique=True,
        index=True,
        comment="Slug usado para login e identificação da competição",
    ),
    Column(
        "owner_user_id",
        String(36),
        ForeignKey("users.id", use_alter=True, name="fk_contests_owner_user_id"),
        nullable=True,
        index=True,
        comment="ID do usuário admin inicial da competição",
    ),
    Column(
        "chief_judge_id",
        String(36),
        ForeignKey("users.id", use_alter=True, name="fk_contests_chief_judge_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="ID do judge que é o chief judge desta competição. Deve ter role=JUDGE e ser membro do contest.",
    ),
    Column(
        "created_by_uberadmin_id",
        String(36),
        ForeignKey("uber_admins.id"),
        nullable=False,
        index=True,
        comment="ID do uberadmin que criou a competição",
    ),
    Column(
        "start_time",
        DateTime(timezone=True),
        nullable=False,
        index=True,
        comment="Data e hora de início da competição (UTC)",
    ),
    Column("duration_minutes", Integer, nullable=False, comment="Duração da competição em minutos"),
    Column(
        "stop_answers_after",
        Integer,
        nullable=False,
        comment="Respostas param de ser enviadas após esse tempo (em minutos, a partir do início da competição)",
    ),
    Column(
        "stop_updating_scoreboard",
        Integer,
        nullable=False,
        comment="O placar para de ser atualizado após esse tempo (em minutos, a partir do início da competição)",
    ),
    Column("penalty", Integer, nullable=False, default=0, comment="Penalidade em minutos por submissão incorreta"),
    Column(
        "clarifications_timeout_minutes",
        Integer,
        nullable=False,
        default=0,
        comment="Tempo em minutos um judge responder a uma clarification depois de obte-la",
    ),
    Column(
        "tasks_timeout_minutes",
        Integer,
        nullable=False,
        default=0,
        comment="Tempo em minutos um membro do staff tem para concluir uma tarefa depois de obte-la",
    ),
    Column(
        "review_timeout_minutes",
        Integer,
        nullable=False,
        default=10,
        comment="Tempo em minutos que um judge tem para confirmar um veredito apos adquirir o lock. 0 = desabilitado",
    ),
    Column(
        "max_problem_file_size_bytes",
        Integer,
        nullable=False,
        default=0,
        comment="Tamanho máximo permitido para código fonte das submissões, em bytes. 0 significa sem limite",
    ),
    Column(
        "active",
        Boolean,
        nullable=False,
        default=True,
        comment="Indica se a competição está ativa. Inativa = inacessível a competidores, dados preservados",
    ),
    Column(
        "autojudge_only",
        Boolean,
        nullable=False,
        default=True,
        comment="A resposta final a uma submissão é determinada apenas pelo autojudge",
    ),
    Column(
        "show_limits",
        Boolean,
        nullable=False,
        default=True,
        comment="Os limites de tempo e memória dos problemas são exibidos aos competidores",
    ),
    Column(
        "allow_print_requests",
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
        comment="Indica se as equipes podem criar tarefas PRINT para solicitar impressao de codigo",
    ),
    Column(
        "contest_timezone",
        String(64),
        nullable=False,
        default="UTC",
        comment="Timezone da competição, usada para exibir horários locais aos competidores",
    ),
    Column(
        "wa_penalty",
        Integer,
        nullable=False,
        default=20,
        comment="Penalidade em minutos por submissão incorreta (WA), adicionada ao tempo total para classificação",
    ),
    Column(
        "accept_pe",
        Boolean,
        nullable=False,
        default=False,
        comment="Indica se a competição aceita PE como resultado correto (AC) ou se PE é considerado incorreto (WA)",
    ),
    Column(
        "ce_adds_penalty",
        Boolean,
        nullable=False,
        default=False,
        comment="Indica se uma submissão com CE (Compilation Error) adiciona penalidade de tempo para a equipe",
    ),
    Column(
        "release_scoreboard_after_end",
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        comment="Unfreeze scoreboard after the contest ends, reveling final score.",
    ),
    _created_at_column(),
    _updated_at_column(),
)

sites = Table(
    "sites",
    metadata,
    _id_column(),
    Column(
        "sitename", String(128), nullable=False, index=True, comment="Name of site contest (university campus, city...)"
    ),
    Column(
        "sitename_normalized", String(128), nullable=False, comment="Lowercased site name for contest-scoped uniqueness"
    ),
    Column("contest_id", String(36), ForeignKey("contests.id", ondelete="CASCADE"), nullable=False, index=True),
    _created_at_column(),
    _updated_at_column(),
    UniqueConstraint("contest_id", "id", name="uq_sites_contest_id_id"),
    UniqueConstraint("contest_id", "sitename_normalized", name="uq_sites_contest_sitename_normalized"),
)

contest_languages = Table(
    "contest_languages",
    metadata,
    Column(
        "contest_id",
        String(36),
        ForeignKey("contests.id", ondelete="RESTRICT"),
        primary_key=True,
        comment="Contest this language is allowed for.",
    ),
    Column(
        "language_id",
        String(64),
        ForeignKey("languages.id", ondelete="RESTRICT"),
        primary_key=True,
        comment="Language allowed in this contest.",
    ),
)

tasks = Table(
    "tasks",
    metadata,
    _id_column(),
    Column("team_id", String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True),
    Column("staff_id", String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True),
    Column(
        "type",
        SAEnum(TaskType, name="taskenum", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=TaskType.BALLOON,
    ),
    Column(
        "problem_id",
        String(36),
        ForeignKey("problems.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
        comment="Problem ID for this task. NULL for SOS tasks. RESTRICT on delete to prevent orphaned tasks.",
    ),
    Column(
        "created_timestamp_seconds",
        Integer,
        nullable=False,
        server_default="0",
        comment="Seconds since contest start when the task was created.",
    ),
    Column("finished_at", DateTime(timezone=True), nullable=True, comment="Time when the task was finished"),
    Column(
        "finished_timestamp_seconds",
        Integer,
        nullable=True,
        comment="Seconds since contest start when the task was finished.",
    ),
    Column("source_code", Text, nullable=False),
    Column(
        "source_hash",
        String(64),
        nullable=False,
        index=True,
        comment="SHA-256 hex digest of source_code for duplicate detection.",
    ),
    Column("source_size_bytes", Integer, nullable=False),
    _created_at_column(),
    _updated_at_column(),
)

Index(
    "ux_tasks_first_balloon_problem_id",
    tasks.c.problem_id,
    unique=True,
    postgresql_where=text("type = 'FIRST_BALLOON'"),
    sqlite_where=text("type = 'FIRST_BALLOON'"),
)
