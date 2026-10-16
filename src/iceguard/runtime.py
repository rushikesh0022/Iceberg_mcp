"""Configuration shared by the MCP server and the Claude Code hook.

Environment:
  ICEGUARD_CATALOG  "file" (default): catalog/schema.toml
                    "clickhouse": discover tables from a running ClickHouse
  ICEGUARD_SCHEMA   schema file for the "file" catalog
  ICEGUARD_CH_HOST, ICEGUARD_CH_PORT   ClickHouse HTTP address (127.0.0.1:8123)
  ICEGUARD_DB       SQLite file for the analysis cache and check history
                    (default .iceguard/iceguard.sqlite; "off" disables it)
"""

from __future__ import annotations

import os
from pathlib import Path

from iceguard.catalog import CatalogSource, SchemaFileCatalog
from iceguard.store import Store

REPO_ROOT = Path(__file__).resolve().parents[2]


class CatalogUnavailable(RuntimeError):
    """The configured catalog cannot be loaded."""


def catalog_kind() -> str:
    kind = os.environ.get("ICEGUARD_CATALOG", "file")
    if kind not in ("file", "clickhouse"):
        raise CatalogUnavailable(f"ICEGUARD_CATALOG must be 'file' or 'clickhouse', not {kind!r}.")
    return kind


def load_catalog() -> CatalogSource:
    if catalog_kind() == "clickhouse":
        return _clickhouse_catalog()
    schema = Path(os.environ.get("ICEGUARD_SCHEMA", REPO_ROOT / "catalog" / "schema.toml"))
    if not schema.is_file():
        raise CatalogUnavailable(f"Catalog schema file not found: {schema}")
    return SchemaFileCatalog(schema, repo_root=schema.parent.parent)


def open_store() -> Store | None:
    setting = os.environ.get("ICEGUARD_DB", str(REPO_ROOT / ".iceguard" / "iceguard.sqlite"))
    return None if setting == "off" else Store(Path(setting))


def _clickhouse_catalog() -> CatalogSource:
    from clickhouse_connect.driver.exceptions import OperationalError

    from iceguard.clickhouse_catalog import ClickHouseCatalog

    host = os.environ.get("ICEGUARD_CH_HOST", "127.0.0.1")
    port = int(os.environ.get("ICEGUARD_CH_PORT", "8123"))
    try:
        return ClickHouseCatalog(REPO_ROOT, host=host, port=port)
    except OperationalError as e:
        raise CatalogUnavailable(
            f"Cannot reach ClickHouse at {host}:{port}. Start it with scripts/clickhouse.sh start."
        ) from e
