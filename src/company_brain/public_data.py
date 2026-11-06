"""Public test documents (T12): what is downloaded, from where, and what it should hash to.

`scripts/fetch_public_data.py` downloads these with curl (the firewall blocks
Python), checks every checksum, copies the files into the root's
`Test data (fictional)/Public/` and records them in `data/manifests/public.json`
and `data/facts.json`. CORD receipts come out of one pinned Parquet file of the
dataset's test split; only the published total-price label is a fact, because
CORD has no vendor or VAT label.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

PREFIX = "Test data (fictional)/Public"
TASK = "T12"

CUAD_REVISION = "a3c393f5d103fd0c516374e4fdff676c8176dcb1"
CORD_REVISION = "7f0115a4b758a71d6473b8d085751692da2fef98"
_CUAD = (
    f"https://huggingface.co/datasets/theatticusproject/cuad/resolve/{CUAD_REVISION}"
    "/CUAD_v1/full_contract_pdf/Part_I"
)
CUAD_SOURCE = (
    "https://huggingface.co/datasets/theatticusproject/cuad (CUAD v1, The Atticus Project)"
)
CORD_SOURCE = "https://huggingface.co/datasets/naver-clova-ix/cord-v2 (CORD v2, NAVER Clova)"


@dataclass(frozen=True)
class Download:
    path: str  # under PREFIX
    url: str
    sha256: str
    role: str
    language: str
    licence: str
    source: str
    category: str = ""


@dataclass(frozen=True)
class CordImage:
    image_id: int
    label: str  # total_price exactly as published in the dataset
    sha256: str


def _contract(category: str, name: str, sha256: str) -> Download:
    return Download(
        path=f"Contracts (CUAD)/{name}",
        url=f"{_CUAD}/{category}/{name.replace(' ', '%20')}",
        sha256=sha256,
        role="contract",
        language="en",
        licence="CC-BY-4.0",
        source=CUAD_SOURCE,
        category=category,
    )


DOWNLOADS: tuple[Download, ...] = (
    _contract(
        "Service",
        "GpaqAcquisitionHoldingsInc_20200123_S-4A_EX-10.8_11951679_EX-10.8_Service Agreement.pdf",
        "7837df24989af83a369b3f1e8cf7aaf8b2cb38d85faef493496dac19830debde",
    ),
    _contract(
        "Outsourcing",
        "FerroglobePlc_20150624_F-4A_EX-10.20_9154746_EX-10.20_Outsourcing Agreement.pdf",
        "9d8734bf7d20fe8e2e2503c7608e5f19f4ebf5858c65a26ef38cb84bc79eeaa3",
    ),
    _contract(
        "Development",
        "ArcaUsTreasuryFund_20200207_N-2_EX-99.K5_11971930_EX-99.K5_Development Agreement.pdf",
        "e917eb721ace4101a21cafb18c530c5449a1814aeee2f29a3a09978ab05ff896",
    ),
    _contract(
        "Hosting",
        "Freecook_20180605_S-1_EX-10.3_11233807_EX-10.3_Hosting Agreement.pdf",
        "4f7f73fad7d3f00c1f17c11894ac56867d049a7de006b38f172120e5c7bf458e",
    ),
    _contract(
        "IP",
        "CerenceInc_20191002_8-K_EX-10.4_11827494_EX-10.4_Intellectual Property Agreement.pdf",
        "90f601b037396d105278da91ff3db5edc973a673fa87da986327016e5f9a63d6",
    ),
    _contract(
        "License_Agreements",
        "AlliedEsportsEntertainmentInc_20190815_8-K_EX-10.19_11788293_EX-10.19_"
        "Content License Agreement.pdf",
        "2c5fe2d4f89cd8f305e99eefac75177858c8fd57155b4347a3b010e270e3ae8a",
    ),
    _contract(
        "Maintenance",
        "AzulSa_20170303_F-1A_EX-10.3_9943903_EX-10.3_Maintenance Agreement2.pdf",
        "8160e0472b7ba8fe90057d0f1fbb0ff7d0eae441317891219227aa61cb313ae3",
    ),
    _contract(
        "Reseller",
        "BravatekSolutionsInc_20170418_8-K_EX-10.1_10205739_EX-10.1_Reseller Agreement.pdf",
        "fa0947b28a7ac9a78dcb1f47c6090a2a173cb66db62d92fa05f4d017918744af",
    ),
    _contract(
        "Distributor",
        "FuseMedicalInc_20190321_10-K_EX-10.43_11575454_EX-10.43_Distributor Agreement.pdf",
        "692444f7ad492a7a15297115d7af9b44412d616570f4df9c72243895a8f7df93",
    ),
    _contract(
        "Supply",
        "AgapeAtpCorp_20191202_10-KA_EX-10.1_11911128_EX-10.1_Supply Agreement.pdf",
        "623d1896671b5e948c55988edc44cdd74d0a3e0056e54aa3a266ff4b1480bfb0",
    ),
    Download(
        path="Annual reports/UK Statistics Authority annual report 2024-25.pdf",
        url="https://uksa.statisticsauthority.gov.uk/wp-content/uploads/sites/2/2025/07/"
        "Annual-report-and-accounts-2024-to-2025.pdf",
        sha256="476bd0ecaf890f7841fcf18af0f7a7bcf866120189e2dce729eb4da281193123",
        role="annual-report",
        language="en",
        licence="OGL-UK-3.0",
        source="UK Statistics Authority, Annual Report and Accounts 2024/25 (Crown copyright)",
    ),
    Download(
        path="Annual reports/Ararteko informe anual 2024.pdf",
        url="https://www.theioi.org/downloads/btfis/Ararteko%20-%20Informe%20anual%202024%20-%20ES.pdf",
        sha256="53b23a666776f104e6e275ee5f93f938c53f324ec76f60b36efdd1de7ce85e19",
        role="annual-report",
        language="es",
        licence="CC-BY-4.0",
        source="Ararteko (Basque Ombudsman), Informe anual 2024, copy hosted by the "
        "International Ombudsman Institute",
    ),
)

# The test split as one Parquet file; the images are taken out of it by image ID.
CORD_PARQUET = Download(
    path="",
    url=f"https://huggingface.co/datasets/naver-clova-ix/cord-v2/resolve/{CORD_REVISION}"
    "/data/test-00000-of-00001-9c204eb3f4e11791.parquet",
    sha256="51c65f1788faff392abe2a0b55b023eb23e9be551c509138eaa3a832514224e7",
    role="dataset",
    language="id",
    licence="CC-BY-4.0",
    source=CORD_SOURCE,
)
CORD_IMAGES: tuple[CordImage, ...] = (
    CordImage(0, "60.000", "8612d04b70f430f3aef07fbbd5200e382dcc4152b344cc2eff9f735f05a257c8"),
    CordImage(1, "91000", "5852cbe48df03646524e717e987eb3d0ac329a52fd192070b5fda1222a053789"),
    CordImage(2, "28,000", "571ac6af26e75de8c06747a8c536c778f19a4562879090813571eddc74603d34"),
    CordImage(4, "174,600", "8f3eee7068c96e86cdb2e4b5c53085cb5e1439462edd55c373548cb1962801ad"),
    CordImage(7, "111,000", "810d3545f4105b19757c4b8b35934a477cde8cd1875302d9092315d2f960ef93"),
)


def amount(label: str) -> str:
    """CORD totals are whole rupiah written with dots or commas between thousands."""
    return re.sub(r"\D", "", label)


def cord_path(image_id: int) -> str:
    return f"{PREFIX}/Receipts (CORD)/CORD test {image_id:04d}.png"


def entries() -> list[dict[str, Any]]:
    """The public manifest, in a fixed order."""
    files = [
        {
            "path": f"{PREFIX}/{d.path}",
            "sha256": d.sha256,
            "task": TASK,
            "role": d.role,
            "language": d.language,
            "licence": d.licence,
            "source": d.url,
        }
        for d in DOWNLOADS
    ]
    files += [
        {
            "path": cord_path(image.image_id),
            "sha256": image.sha256,
            "task": TASK,
            "role": "receipt",
            "language": "id",
            "licence": CORD_PARQUET.licence,
            "source": f"{CORD_PARQUET.url} (image_id {image.image_id})",
        }
        for image in CORD_IMAGES
    ]
    return files


def facts() -> list[dict[str, Any]]:
    """Expected amounts for the CORD receipts (only the total is scored)."""
    return [
        {
            "id": f"public.cord.{image.image_id}",
            "task": TASK,
            "type": "receipt",
            "client": "public",
            "language": "id",
            "image_id": image.image_id,
            "label": image.label,
            "fields": {"total": amount(image.label)},
            "currency": "IDR",
            "files": [cord_path(image.image_id)],
        }
        for image in sorted(CORD_IMAGES, key=lambda i: i.image_id)
    ]
