from pathlib import Path

import pytest

from iceguard.catalog import SchemaFileCatalog

REPO_ROOT = Path(__file__).resolve().parent.parent
PROPOSED_DIR = REPO_ROOT / "pipelines" / "proposed"


@pytest.fixture(scope="session")
def catalog() -> SchemaFileCatalog:
    return SchemaFileCatalog(REPO_ROOT / "catalog" / "schema.toml", repo_root=REPO_ROOT)
