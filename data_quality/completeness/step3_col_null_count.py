import json
from pathlib import Path
from typing import Any

from core.settings import config

step_config = {
    "step": 3,
    "dimension": "completeness",
    "depend_business": [],
}
def load_column_null_count(schema: str) -> list[dict[str, Any]]:
    """Load column null-count results from the schema metadata cache."""
    cache_file = Path(config.schema_cache_file(schema))
    with cache_file.open("r", encoding="utf-8") as file:
        metadata = json.load(file)

    return [
        {
            "schema_name": schema,
            "table_name": table["table_name"],
            "column_name": column["name"],
            "data_type": column.get("data_type", ""),
            "full_type": column.get("full_type", column.get("data_type", "")),
            "nullable": column.get("nullable", False),
            "default": column.get("default"),
            "pk": column.get("pk", False),
            "fk": column.get("fk", False),
            "uq": column.get("uq", False),
            "pii_phi": column.get("pii_phi", "") or "",
            "null_pct": column.get("statistics", {}).get("null_pct"),
            "distinct_count": column.get("statistics", {}).get("distinct_count"),
            "comment": column.get("comment", "") or "",
            "dirty_value_check": column.get("dirty_value_check", ""),
        }
        for table in metadata.values()
        for column in table.get("columns", [])
    ]
