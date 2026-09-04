#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Download Brazilian national and state flag assets."""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

import requests

BRAZILIAN_FLAG_FILES = {
    "01-brasil-circle.svg": "BR.svg",
    "02-acre-circle.svg": "AC.svg",
    "03-alagoas-circle.svg": "AL.svg",
    "04-amapa-circle.svg": "AP.svg",
    "05-amazonas-circle.svg": "AM.svg",
    "06-bahia-circle.svg": "BA.svg",
    "07-ceara-circle.svg": "CE.svg",
    "08-distrito-federal-circle.svg": "DF.svg",
    "09-espirito-santo-circle-v2.svg": "ES.svg",
    "10-goias-circle.svg": "GO.svg",
    "11-maranhao-circle.svg": "MA.svg",
    "12-mato-grosso-circle.svg": "MT.svg",
    "13-mato-grosso-do-sul-circle.svg": "MS.svg",
    "14-minas-gerais-circle.svg": "MG.svg",
    "15-para-circle.svg": "PA.svg",
    "16-paraiba-circle.svg": "PB.svg",
    "17-parana-circle.svg": "PR.svg",
    "18-pernambuco-circle.svg": "PE.svg",
    "19-piaui-circle.svg": "PI.svg",
    "20-rio-de-janeiro-circle.svg": "RJ.svg",
    "21-rio-grande-do-norte-circle.svg": "RN.svg",
    "22-rio-grande-do-sul-circle.svg": "RS.svg",
    "23-rondonia-circle.svg": "RO.svg",
    "24-roraima-circle.svg": "RR.svg",
    "25-santa-catarina-circle.svg": "SC.svg",
    "26-sao-paulo-circle.svg": "SP.svg",
    "27-sergipe-circle.svg": "SE.svg",
    "28-tocantins-circle.svg": "TO.svg",
}


def download_brazilian_flags(
    *,
    vendor_dir: Path,
    sha: str,
    expected_sha256: str,
    failures: list[str],
    session: requests.Session,
    request_headers: dict[str, str],
) -> None:
    """Download the circular Brazilian national and state flag SVGs.

    Args:
        vendor_dir: Vendor asset output directory.
        sha: Full upstream commit SHA to download.
        expected_sha256: Expected hex SHA-256 of the ZIP file.
        failures: Mutable failure list.
        session: HTTP session used to download the archive.
        request_headers: Headers sent with the archive request.
    """
    repository = "pierrelapalu/icones-bandeiras-br-uf"
    url = f"https://github.com/{repository}/archive/{sha}.zip"
    print(f"Fetching Brazilian flags from {url}...")
    try:
        response = session.get(url, headers=request_headers, timeout=60)
        response.raise_for_status()
    except Exception as exc:
        failures.append(f"{url}: {exc}")
        return

    actual_sha256 = hashlib.sha256(response.content).hexdigest()
    if actual_sha256 != expected_sha256:
        failures.append(f"{url}: sha256 mismatch (got {actual_sha256}, expected {expected_sha256})")
        return

    prefix = f"icones-bandeiras-br-uf-{sha[:7]}"
    try:
        extracted: dict[str, bytes] = {}
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            for entry in archive.infolist():
                parts = entry.filename.split("/")
                if (
                    len(parts) == 5
                    and parts[0].startswith(prefix)
                    and parts[1:4] == ["dist", "circle", "svg"]
                    and parts[4] in BRAZILIAN_FLAG_FILES
                ):
                    extracted[parts[4]] = archive.read(entry)

        missing = sorted(set(BRAZILIAN_FLAG_FILES) - set(extracted))
        if missing:
            raise ValueError(f"missing expected SVGs: {', '.join(missing)}")

        flags_dir = vendor_dir / "img" / "state-flags"
        flags_dir.mkdir(parents=True, exist_ok=True)
        for source_name, destination_name in BRAZILIAN_FLAG_FILES.items():
            (flags_dir / destination_name).write_bytes(extracted[source_name])
    except Exception as exc:
        failures.append(f"{url}: extraction failed: {exc}")
