"""Table- and column-level lineage built from the analysed SQL files."""

from __future__ import annotations

from iceguard.catalog import CatalogSource
from iceguard.models import Lineage


def get_lineage(table: str, catalog: CatalogSource) -> Lineage:
    raise NotImplementedError("Step 3")
