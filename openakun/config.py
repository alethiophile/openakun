#!python3

from __future__ import annotations

from attrs import define
from cattrs import structure
from enum import Enum
from pathlib import Path
import tomllib, os

class CSPLevel(Enum):
    Report = 'report'
    Enforce = 'enforce'

@define
class Config:
    database_url: str
    secret_key: str
    echo_sql: bool
    use_alembic: bool
    sentry_dsn: str | None
    redis_url: str
    csp_level: CSPLevel
    proxy_fix: bool
    main_origin: str

    @classmethod
    def get_config(cls, fn: Path | None = None) -> Config:
        if fn is None:
            fn = Path(os.environ.get("OPENAKUN_CONFIG",
                                     'openakun_config.toml'))
        cdata = tomllib.loads(fn.read_text())

        if 'secret_key' in cdata:
            raise ValueError("warning: may not specify secret_key in config file")
        if 'sentry_dsn' in cdata:
            raise ValueError("warning: may not specify sentry_dsn in config file")

        cdata['secret_key'] = os.environ['OPENAKUN_SECRET_KEY']
        if len(cdata['secret_key']) < 40:
            raise ValueError("invalid OPENAKUN_SECRET_KEY")
        cdata['sentry_dsn'] = os.environ.get('OPENAKUN_SENTRY_DSN')

        return structure(cdata, Config)
