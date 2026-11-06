"""Where the system finds its data, database, Keychain entries and ports."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from company_brain import isolation


@dataclass(frozen=True)
class Settings:
    root: Path | None
    data_dir: Path
    exports_dir: Path
    database: str
    keychain_prefix: str
    openwebui_port: int
    backend_port: int
    ollama_port: int
    pg_port: int
    pg_data: Path
    launchd_prefix: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        """Read settings; under the test suite, real resources are refused."""
        env = os.environ if env is None else env
        home = Path.home()
        root = env.get(isolation.ENV_ROOT)
        settings = cls(
            root=Path(root) if root else None,
            data_dir=Path(env.get(isolation.ENV_DATA, str(home / "CompanyBrainData"))),
            exports_dir=Path(env.get(isolation.ENV_EXPORTS, str(home / "CompanyBrainExports"))),
            database=env.get(isolation.ENV_DB, isolation.REAL_DATABASE),
            keychain_prefix=env.get(isolation.ENV_KEYCHAIN, isolation.REAL_KEYCHAIN_PREFIX),
            openwebui_port=int(
                env.get(isolation.ENV_OPENWEBUI_PORT, isolation.REAL_PORTS["openwebui"])
            ),
            backend_port=int(env.get(isolation.ENV_BACKEND_PORT, isolation.REAL_PORTS["backend"])),
            ollama_port=int(
                env.get(isolation.ENV_OLLAMA_PORT, isolation.REAL_SERVICE_PORTS["ollama"])
            ),
            pg_port=int(env.get(isolation.ENV_PG_PORT, isolation.REAL_SERVICE_PORTS["postgresql"])),
            pg_data=Path(env.get(isolation.ENV_PG_DATA, str(isolation.REAL_PG_DATA))),
            launchd_prefix=env.get(isolation.ENV_LAUNCHD, isolation.REAL_LAUNCHD_PREFIX),
        )
        if env.get(isolation.ENV_TEST_FLAG) == "1" or isolation.guard_active():
            isolation.check_settings(settings)
        return settings
