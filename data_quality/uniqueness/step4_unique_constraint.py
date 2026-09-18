from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from core.database import get_connection
from data_quality.uniqueness.step2_technical_duplicate import (
    TableKey,
    _is_excluded_table,
    _is_tier_one,
    _qualified_table,
)
from utils.confirmed_business import load_table_tiers
from utils.formatter import quote_identifier


UniqueConstraint = dict[str, Any]
UniqueConstraintResult = dict[str, Any]


step_config = {
    "step": 4,
    "dimension": "uniqueness",
    "depend_business": [],
}


UNIQUE_CONSTRAINT_SQL = """
SELECT
    tc.constraint_name,
    tc.table_schema,
    tc.table_name,
    kcu.column_name,
    kcu.ordinal_position
FROM information_schema.table_constraints AS tc
JOIN information_schema.key_column_usage AS kcu
  ON kcu.constraint_schema = tc.constraint_schema
 AND kcu.constraint_name = tc.constraint_name
 AND kcu.table_schema = tc.table_schema
 AND kcu.table_name = tc.table_name
WHERE tc.constraint_type = 'UNIQUE'
ORDER BY tc.table_schema, tc.table_name, tc.constraint_name, kcu.ordinal_position;
"""


def _group_constraints(
    rows: Iterable[dict[str, Any]],
    tier1_tables: set[TableKey],
) -> list[UniqueConstraint]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        table_key = (str(row["schema_name"]), str(row["table_name"]))
        if table_key not in tier1_tables or _is_excluded_table(table_key[1]):
            continue
        grouped[
            (
                table_key[0],
                table_key[1],
                str(row["constraint_name"]),
            )
        ].append(row)

    return [
        {
            "schema_name": schema,
            "table_name": table,
            "constraint_name": constraint_name,
            "columns": [
                str(row["column_name"])
                for row in sorted(group, key=lambda item: item["ordinal_position"])
            ],
        }
        for (schema, table, constraint_name), group in sorted(grouped.items())
    ]


def load_unique_constraints(
    tier1_tables: set[TableKey] | None = None,
) -> list[UniqueConstraint]:
    """Load declared UNIQUE constraints for the selected Uniqueness scope."""
    if tier1_tables is None:
        tier1_tables = {
            table_key
            for table_key, tier in load_table_tiers().items()
            if _is_tier_one(tier)
        }
    if not tier1_tables:
        return []

    with get_connection(read_only=True) as connection:
        cursor = connection.cursor()
        try:
            cursor.execute(UNIQUE_CONSTRAINT_SQL)
            rows = [
                {
                    "constraint_name": row[0],
                    "schema_name": row[1],
                    "table_name": row[2],
                    "column_name": row[3],
                    "ordinal_position": row[4],
                }
                for row in cursor.fetchall()
            ]
        finally:
            cursor.close()
    return _group_constraints(rows, tier1_tables)


def _build_constraint_query(constraint: UniqueConstraint) -> str:
    columns = constraint["columns"]
    if not columns:
        raise ValueError("A unique constraint must contain at least one column")
    quoted_columns = [quote_identifier(column) for column in columns]
    selected_columns = ", ".join(quoted_columns)
    not_null = " AND ".join(f"{column} IS NOT NULL" for column in quoted_columns)
    table = _qualified_table(constraint["schema_name"], constraint["table_name"])
    return f"""
WITH grouped_records AS (
    SELECT {selected_columns}, COUNT(*) AS record_count
    FROM {table}
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
    constraint: UniqueConstraint,
    checked_count: int,
    duplicate_group_count: int,
    duplicate_row_count: int,
) -> UniqueConstraintResult:
    duplicate_pct = (
        round((duplicate_row_count / checked_count) * 100, 2)
        if checked_count
        else 0.0
    )
    return {
        **constraint,
        "checked_count": checked_count,
        "duplicate_group_count": duplicate_group_count,
        "duplicate_row_count": duplicate_row_count,
        "duplicate_pct": duplicate_pct,
    }


def load_unique_constraint_violations(
    constraints: list[UniqueConstraint] | None = None,
) -> list[UniqueConstraintResult]:
    """Recheck current data against declared UNIQUE constraints."""
    selected_constraints = (
        load_unique_constraints() if constraints is None else constraints
    )
    if not selected_constraints:
        return []

    results = []
    with get_connection(read_only=True) as connection:
        cursor = connection.cursor()
        try:
            for constraint in selected_constraints:
                cursor.execute(_build_constraint_query(constraint))
                checked_count, group_count, row_count = cursor.fetchone()
                results.append(
                    _make_result(
                        constraint,
                        int(checked_count or 0),
                        int(group_count or 0),
                        int(row_count or 0),
                    )
                )
        finally:
            cursor.close()
    return results
