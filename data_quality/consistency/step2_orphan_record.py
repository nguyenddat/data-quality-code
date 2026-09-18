from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from core.database import get_connection
from data_quality.consistency.step1_table_relationship import (
    Relationship,
    load_table_relationships,
)
from utils.formatter import quote_identifier


OrphanResult = dict[str, Any]


step_config = {
    "step": 2,
    "dimension": "consistency",
    "depend_business": [],
}


def _group_relationships(
    relationships: Iterable[Relationship],
) -> list[list[Relationship]]:
    """Group relationship columns so composite FK constraints are one check."""
    grouped: dict[tuple[str, str, str], list[Relationship]] = defaultdict(list)
    for relationship in relationships:
        key = (
            relationship["from_schema"],
            relationship["from_table"],
            relationship["constraint_name"],
        )
        grouped[key].append(relationship)

    return [
        sorted(group, key=lambda item: item.get("column_position") or 0)
        for group in grouped.values()
    ]


def _qualified_table(schema: str, table: str) -> str:
    return f"{quote_identifier(schema)}.{quote_identifier(table)}"


def _build_orphan_query(relationship_columns: list[Relationship]) -> str:
    """Build a read-only orphan query for one FK constraint."""
    if not relationship_columns:
        raise ValueError("relationship_columns must not be empty")

    first = relationship_columns[0]
    child_table = _qualified_table(first["from_schema"], first["from_table"])
    parent_table = _qualified_table(first["to_schema"], first["to_table"])
    child_alias = "child_record"
    parent_alias = "parent_record"

    child_columns = [
        f"{child_alias}.{quote_identifier(item['from_column'])}"
        for item in relationship_columns
    ]
    not_null = " AND ".join(f"{column} IS NOT NULL" for column in child_columns)
    matches_parent = " AND ".join(
        f"{parent_alias}.{quote_identifier(item['to_column'])} = "
        f"{child_alias}.{quote_identifier(item['from_column'])}"
        for item in relationship_columns
    )

    return f"""
SELECT
    COUNT(*) AS checked_count,
    COALESCE(
        SUM(
            CASE WHEN NOT EXISTS (
                SELECT 1
                FROM {parent_table} AS {parent_alias}
                WHERE {matches_parent}
            ) THEN 1 ELSE 0 END
        ),
        0
    ) AS orphan_count
FROM {child_table} AS {child_alias}
WHERE {not_null};
"""


def _make_result(
    relationship_columns: list[Relationship],
    checked_count: int,
    orphan_count: int,
) -> OrphanResult:
    first = relationship_columns[0]
    orphan_pct = round((orphan_count / checked_count) * 100, 2) if checked_count else 0.0
    return {
        "constraint_name": first["constraint_name"],
        "child_schema": first["from_schema"],
        "child_table": first["from_table"],
        "fk_column": ", ".join(
            item["from_column"] for item in relationship_columns
        ),
        "parent_schema": first["to_schema"],
        "parent_table": first["to_table"],
        "parent_column": ", ".join(
            item["to_column"] for item in relationship_columns
        ),
        "checked_count": checked_count,
        "orphan_count": orphan_count,
        "orphan_pct": orphan_pct,
    }


def load_orphan_records(
    relationships: Iterable[Relationship],
) -> list[OrphanResult]:
    """Return orphan counts for the relationships produced by Step 1.

    The denominator is the number of child records whose complete FK value is
    non-NULL. Nullable FK values are optional relationships and are excluded
    from the orphan check. The database connection is read-only.
    """
    relationship_groups = _group_relationships(relationships)
    results: list[OrphanResult] = []
    if not relationship_groups:
        return results

    with get_connection(read_only=True) as connection:
        cursor = connection.cursor()
        try:
            for relationship_columns in relationship_groups:
                cursor.execute(_build_orphan_query(relationship_columns))
                checked_count, orphan_count = cursor.fetchone()
                results.append(
                    _make_result(
                        relationship_columns,
                        int(checked_count or 0),
                        int(orphan_count or 0),
                    )
                )
        finally:
            cursor.close()

    return results
