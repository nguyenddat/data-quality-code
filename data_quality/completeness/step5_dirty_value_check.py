import json
from pathlib import Path
from typing import Any

from core.database import get_connection
from core.settings import config
from utils.formatter import quote_identifier

TEXT_TYPES = {"character", "character varying", "text", "name"}
NUMERIC_TYPES = {
    "bigint",
    "bigserial",
    "decimal",
    "double precision",
    "integer",
    "numeric",
    "real",
    "smallint",
    "smallserial",
    "serial",
}
DATE_TYPES = {
    "date",
    "timestamp without time zone",
    "timestamp with time zone",
}

step_config = {
    "step": 5,
    "dimension": "completeness",
    "depend_business": [],
}


def _dirty_condition(column: dict[str, Any]) -> tuple[str, list[str]] | None:
    identifier = quote_identifier(column["name"])
    data_type = column.get("data_type", "").lower()

    if data_type in TEXT_TYPES:
        return (
            f"lower(trim(CAST({identifier} AS VARCHAR))) IN (?, ?, ?, ?, ?, ?, ?)",
            ["", "n/a", ".", "-", "_", "unknown", "x"],
        )
    if data_type in NUMERIC_TYPES:
        return f"CAST({identifier} AS VARCHAR) IN (?, ?, ?)", ["0", "-1", "9999"]
    if data_type in DATE_TYPES:
        return (
            f"CAST({identifier} AS VARCHAR) LIKE ? OR CAST({identifier} AS VARCHAR) LIKE ?",
            ["1900-01-01%", "1970-01-01%"],
        )
    return None


def load_dirty_value_check(schema: str) -> list[dict[str, Any]]:
    """Find dirty placeholder values by column using a read-only DB query."""
    cache_file = Path(config.schema_cache_file(schema))
    with cache_file.open("r", encoding="utf-8") as file:
        metadata = json.load(file)

    results: list[dict[str, Any]] = []
    with get_connection() as conn:
        cursor = conn.cursor()
        try:
            for table in metadata.values():
                table_identifier = quote_identifier(table["table_name"])
                cursor.execute(
                    f"SELECT COUNT(*) FROM {quote_identifier(schema)}."
                    f"{table_identifier}"
                )
                total_rows = int(cursor.fetchone()[0] or 0)
                for column in table.get("columns", []):
                    condition = _dirty_condition(column)
                    dirty_check = "Pass"
                    if condition is not None:
                        expression, parameters = condition
                        cursor.execute(
                            f"SELECT CAST({quote_identifier(column['name'])} AS VARCHAR), "
                            f"COUNT(*) FROM {quote_identifier(schema)}."
                            f"{table_identifier} WHERE {quote_identifier(column['name'])} "
                            f"IS NOT NULL AND ({expression}) GROUP BY 1 ORDER BY 1",
                            parameters,
                        )
                        dirty_values = cursor.fetchall()
                        if dirty_values:
                            details = ", ".join(
                                f"{display_value} ({count / total_rows * 100:.2f}%)"
                                if total_rows
                                else f"{display_value} (N/A)"
                                for value, count in dirty_values
                                for display_value in ["''" if value == "" else value]
                            )
                            dirty_check = f"Failed: {details}"
                    results.append(
                        {
                            "schema_name": schema,
                            "table_name": table["table_name"],
                            "column_name": column["name"],
                            "dirty_value_check": dirty_check,
                        }
                    )
        finally:
            cursor.close()

    return results
