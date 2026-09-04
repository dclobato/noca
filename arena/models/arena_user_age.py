#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Age computation and formatting for the Arena user model.

These derive from ``dta_nascimento`` alone and answer a display question. The
*policy* question -- whether an age gates an account -- belongs to
``shared.age_check`` and is deliberately not implemented here.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from dateutil.relativedelta import relativedelta
from sqlalchemy.orm import Mapped


class ArenaUserAgeMixin:
    """Provide age computation and formatting for the Arena user model."""

    dta_nascimento: Mapped[date | None]

    def idade(
        self,
        meses: bool = False,
        dias: bool = False,
        data_referencia: date | None = None,
    ) -> dict[str, int] | None:
        """Calculate the user's age with optional month and day granularity.

        Args:
            meses: Include months in the returned dict.
            dias: Include days in the returned dict (implies meses=True).
            data_referencia: Reference date for the calculation; defaults to
                today in UTC.

        Returns:
            dict[str, int] | None: Dict with keys 'anos', optionally 'meses'
                and 'dias'. None if dta_nascimento is not set.
        """
        if not isinstance(self.dta_nascimento, date):
            return None
        ref = data_referencia or datetime.now(UTC).date()
        incluir_meses = meses or dias
        diff = relativedelta(ref, self.dta_nascimento)
        resultado: dict[str, int] = {"anos": diff.years}
        if incluir_meses:
            resultado["meses"] = diff.months
        if dias:
            resultado["dias"] = diff.days
        return resultado

    def idade_str(
        self,
        meses: bool = False,
        dias: bool = False,
        data_referencia: date | None = None,
    ) -> str | None:
        """Return a human-readable Portuguese string for the user's age.

        Args:
            meses: Include months when age >= 1 year.
            dias: Include days (implies meses=True).
            data_referencia: Reference date; defaults to today in UTC.

        Returns:
            str | None: e.g. "25 anos", "1 ano e 3 meses", or None.
        """
        ref = data_referencia or datetime.now(UTC).date()
        incluir_meses = meses or dias
        idade = self.idade(meses=incluir_meses, dias=dias, data_referencia=ref)
        if not idade:
            return None
        anos = idade.get("anos", 0)
        meses_ = idade.get("meses", 0)
        dias_ = idade.get("dias", 0)
        partes: list[str] = []
        if anos > 0:
            partes.append(f"{anos} ano{'s' if anos != 1 else ''}")
            if incluir_meses and meses_:
                partes.append(f"{meses_} {'meses' if meses_ != 1 else 'mês'}")
            if dias and dias_:
                partes.append(f"{dias_} dia{'s' if dias_ != 1 else ''}")
        elif meses_ > 0:
            partes.append(f"{meses_} {'meses' if meses_ != 1 else 'mês'}")
            if dias and dias_:
                partes.append(f"{dias_} dia{'s' if dias_ != 1 else ''}")
        else:
            partes.append(f"{dias_} dia{'s' if dias_ != 1 else ''}")
        if len(partes) > 1:
            return ", ".join(partes[:-1]) + " e " + partes[-1]
        return partes[0]
