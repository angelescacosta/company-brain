"""Security scan F3: the Pipe's token sits in Open WebUI's database, so that database and the
data folder must be readable by the owner only, and an empty token is never accepted."""

import stat
from pathlib import Path

import pytest

from company_brain import backend, services


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_the_data_folder_and_open_webui_database_are_private(tmp_path: Path) -> None:
    data = tmp_path / "CompanyBrainData"
    webui = data / "openwebui"
    webui.mkdir(parents=True)
    data.chmod(0o755)
    webui.chmod(0o755)
    database = webui / "webui.db"
    database.write_bytes(b"")
    database.chmod(0o644)
    services.make_private(data)
    assert (mode(data), mode(webui), mode(database)) == (0o700, 0o700, 0o600)
    fresh = tmp_path / "new"
    services.make_private(fresh)  # created private when missing
    assert mode(fresh) == 0o700 and mode(fresh / "openwebui") == 0o700


def test_a_new_token_folder_is_private(tmp_path: Path) -> None:
    backend.ensure_token(tmp_path / "data")
    assert mode(tmp_path / "data") == 0o700


def test_an_empty_token_is_refused() -> None:
    with pytest.raises(ValueError):
        backend.serve(backend.LazyAssistant(lambda: None), 0, "")  # type: ignore[arg-type,return-value]
