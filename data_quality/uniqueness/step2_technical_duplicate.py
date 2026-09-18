from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from core.database import get_connection
from core.settings import config
from utils.confirmed_business import load_table_tiers
from utils.formatter import quote_identifier


TechnicalDuplicateResult = dict[str, Any]
TableKey = tuple[str, str]


step_config = {
    "step": 2,
    "dimension": "uniqueness",
    "depend_business": [
        Path(config.business_cf_file("completeness")) / "table_tiers.yaml",
    ],
}


# Step 1 is intentionally folded into Layer 1 of Step 2.
EXCLUDED_TABLE_TOKENS = (
    "log",
    "audit",
    "history",
    "hist",
    "config",
    "setting",
    "catalog",
    "danh_muc",
)
TECHNICAL_COLUMN_TOKENS = (
    "created_at",
    "updated_at",
    "deleted_at",
    "inserted_at",
    "modified_at",
    "timestamp",
)
TECHNICAL_DATA_TYPES = {
    "time without time zone",
    "time with time zone",
    "timestamp without time zone",
    "timestamp with time zone",
}


def _is_excluded_table(table: str) -> bool:
    normalized = str(table).casefold()
    return any(token in normalized for token in EXCLUDED_TABLE_TOKENS)


def _is_tier_one(value: Any) -> bool:
    if isinstance(value, int):
        return value == 1
    normalized = str(value or "").strip().casefold()
    normalized = normalized.replace("nhóm", "").replace("tier", "")
    return normalized.strip() in {"1", "1.0"}


def _is_technical_column(row: dict[str, Any]) -> bool:
    column = str(row.get("column_name", "")).casefold()
    data_type = str(row.get("data_type", "")).casefold()
    if row.get("pk") or data_type in TECHNICAL_DATA_TYPES:
        return True
    if column == "id" or column.endswith("_id"):
        return True
    return any(token in column for token in TECHNICAL_COLUMN_TOKENS)


def select_layer1_scope(
    rows: Iterable[dict[str, Any]],
    tier1_tables: set[TableKey] | None = None,
) -> dict[TableKey, list[dict[str, Any]]]:
    """Select Tier 1, non-infrastructure tables for the Layer 2 scan."""
    selected_tiers = load_table_tiers() if tier1_tables is None else {
        key: 1 for key in tier1_tables
    }
    scope: dict[TableKey, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = (str(row["schema_name"]), str(row["table_name"]))
        if key not in selected_tiers or not _is_tier_one(selected_tiers[key]):
            continue
        if _is_excluded_table(key[1]) or _is_technical_column(row):
            continue
        scope[key].append(row)
    return dict(scope)


def _qualified_table(schema: str, table: str) -> str:
    return f"{quote_identifier(schema)}.{quote_identifier(table)}"


def _build_duplicate_query(schema: str, table: str, columns: list[str]) -> str:
    if not columns:
        raise ValueError("columns must not be empty")
    table_ref = _qualified_table(schema, table)
    quoted_columns = [quote_identifier(column) for column in columns]
    selected_columns = ", ".join(quoted_columns)
    not_null = " AND ".join(f"{column} IS NOT NULL" for column in quoted_columns)
    return f"""
WITH grouped_records AS (
    SELECT {selected_columns}, COUNT(*) AS record_count
    FROM {table_ref}
    WHERE {not_null}
    GROUP BY {selected_columns}
)
SELECT
    COALESCE(SUM(record_count), 0) AS checked_count,
    COUNT(*) FILTER (WHERE record_count > 1) AS duplicate_group_count,
    COALESCE(SUM(CASE WHEN record_count > 1 THEN record_count ELSE 0 END), 0)
        AS duplicate_row_count
FROM grouped_records;
"""


def _make_result(
    schema: str,
    table: str,
    columns: list[str],
    checked_count: int,
    duplicate_group_count: int,
    duplicate_row_count: int,
) -> TechnicalDuplicateResult:
    duplicate_pct = (
        round((duplicate_row_count / checked_count) * 100, 2)
        if checked_count
        else 0.0
    )
    return {
        "schema_name": schema,
        "table_name": table,
        "columns": columns,
        "checked_count": checked_count,
        "duplicate_group_count": duplicate_group_count,
        "duplicate_row_count": duplicate_row_count,
        "duplicate_pct": duplicate_pct,
    }


def load_technical_duplicates(
    rows: list[dict[str, Any]],
    tier1_tables: set[TableKey] | None = None,
) -> list[TechnicalDuplicateResult]:
    """Run the Layer 2 whole-business-column duplicate scan."""
    scope = select_layer1_scope(rows, tier1_tables=tier1_tables)
    if not scope:
        return []

    results = []
    with get_connection(read_only=True) as connection:
        cursor = connection.cursor()
        try:
            for (schema, table), column_rows in sorted(scope.items()):
                columns = [str(row["column_name"]) for row in column_rows]
                cursor.execute(_build_duplicate_query(schema, table, columns))
                checked_count, group_count, row_count = cursor.fetchone()
                results.append(
                    _make_result(
                        schema,
                        table,
                        columns,
                        int(checked_count or 0),
                        int(group_count or 0),
                        int(row_count or 0),
                    )
                )
        finally:
            cursor.close()
    return results
