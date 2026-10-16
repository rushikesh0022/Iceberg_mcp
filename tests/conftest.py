from pathlib import Path

import pytest

from iceguard.catalog import SchemaFileCatalog

REPO_ROOT = Path(__file__).resolve().parent.parent
PROPOSED_DIR = REPO_ROOT / "pipelines" / "proposed"


@pytest.fixture(scope="session")
def catalog() -> SchemaFileCatalog:
    return SchemaFileCatalog(REPO_ROOT / "catalog" / "schema.toml", repo_root=REPO_ROOT)


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch, tmp_path):
    """Every test gets its own SQLite file and the default (file) catalog."""
    monkeypatch.setenv("ICEGUARD_DB", str(tmp_path / "iceguard.sqlite"))
    monkeypatch.delenv("ICEGUARD_CATALOG", raising=False)
    monkeypatch.delenv("ICEGUARD_SCHEMA", raising=False)
