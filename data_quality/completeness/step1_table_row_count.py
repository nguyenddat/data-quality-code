import json
from pathlib import Path
from typing import Any

from core.settings import config


step_config = {
    "step": 1,
    "dimension": "completeness",
    "depend_business": [],
}

def load_table_row_count(schema: str) -> list[dict[str, Any]]:
    """Load table row-count results from the schema metadata cache."""
    cache_file = Path(config.schema_cache_file(schema))
    with cache_file.open("r", encoding="utf-8") as file:
        metadata = json.load(file)

    results = [
        {
            "schema_name": schema,
            "table_name": table["table_name"],
            "comment": table.get("comment", "") or "",
            "row_count": table["row_count"],
            "columns": table["column_count"],
            "total_size": table["total_size"],
            "data_size": table["data_size"],
            "index_size": table["index_size"],
            "has_pk": table["has_pk"],
            "fk_count": table["fk_count"],
        }
        for table in metadata.values()
    ]
    return results
