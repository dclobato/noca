#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Copy Arena legal markdown documents into the template tree."""

from pathlib import Path
from shutil import copy2

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = REPO_ROOT / "docs" / "legal"
TARGET_DIR = REPO_ROOT / "arena" / "template" / "legal"
DOCUMENTS = {
    "termos_e_condicoes_arena_ptbr.md": "terms_of_service.md",
    "politica_de_privacidade_arena_ptbr.md": "privacy_policy.md",
}


def copy_legal_docs() -> None:
    """Copy legal markdown documents from docs/legal to arena/template/legal."""
    TARGET_DIR.mkdir(parents=True, exist_ok=True)

    for source_name, target_name in DOCUMENTS.items():
        source_path = SOURCE_DIR / source_name
        if not source_path.is_file():
            raise FileNotFoundError(f"Required legal document not found: {source_path}")
        copy2(source_path, TARGET_DIR / target_name)


if __name__ == "__main__":
    copy_legal_docs()
