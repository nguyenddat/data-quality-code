from collections.abc import Iterable
from pathlib import Path
from typing import Any

from core.database import get_connection
from core.settings import config
from utils.confirmed_business import load_table_tiers


Relationship = dict[str, Any]
TableKey = tuple[str, str]


step_config = {
    "step": 1,
    "dimension": "consistency",
    "depend_business": [
        Path(config.business_cf_file("completeness")) / "table_tiers.yaml",
    ],
}


RELATIONSHIP_SQL = """
SELECT
    fk.constraint_name,
    fk.table_schema AS from_schema,
    fk.table_name AS from_table,
    fk.column_name AS from_column,
    pk.table_schema AS to_schema,
    pk.table_name AS to_table,
    pk.column_name AS to_column,
    pk_constraint.constraint_type AS referenced_key_type,
    rc.update_rule,
    rc.delete_rule,
    fk.ordinal_position AS column_position
FROM information_schema.key_column_usage AS fk
JOIN information_schema.table_constraints AS fk_constraint
  ON fk_constraint.constraint_schema = fk.constraint_schema
 AND fk_constraint.constraint_name = fk.constraint_name
 AND fk_constraint.table_schema = fk.table_schema
 AND fk_constraint.table_name = fk.table_name
JOIN information_schema.referential_constraints AS rc
  ON rc.constraint_schema = fk.constraint_schema
 AND rc.constraint_name = fk.constraint_name
JOIN information_schema.key_column_usage AS pk
  ON pk.constraint_schema = rc.unique_constraint_schema
 AND pk.constraint_name = rc.unique_constraint_name
 AND pk.table_schema = rc.unique_constraint_schema
 AND pk.ordinal_position = fk.position_in_unique_constraint
JOIN information_schema.table_constraints AS pk_constraint
  ON pk_constraint.constraint_schema = pk.constraint_schema
 AND pk_constraint.constraint_name = pk.constraint_name
 AND pk_constraint.table_schema = pk.table_schema
 AND pk_constraint.table_name = pk.table_name
WHERE fk_constraint.constraint_type = 'FOREIGN KEY'
  AND pk_constraint.constraint_type IN ('PRIMARY KEY', 'UNIQUE')
  AND (? IS NULL OR fk.table_schema = ?)
ORDER BY fk.table_schema, fk.table_name, fk.constraint_name, fk.ordinal_position;
"""


def _is_tier_one(value: Any) -> bool:
    """Return whether a configured tier represents Tier 1."""
    if isinstance(value, int):
        return value == 1
    normalized = str(value or "").strip().casefold()
    normalized = normalized.replace("nhóm", "").replace("tier", "")
    return normalized.strip() in {"1", "1.0"}


def load_tier1_tables() -> set[TableKey]:
    """Read only confirmed Tier 1 tables from the business YAML configuration."""
    return {
        table_key
        for table_key, tier in load_table_tiers().items()
        if _is_tier_one(tier)
    }


def _filter_tier1_relationships(
    rows: Iterable[Relationship], tier1_tables: set[TableKey]
) -> list[Relationship]:
    """Keep edges whose child and parent are both confirmed Tier 1 tables."""
    result = []
    for row in rows:
        if (
            (row["from_schema"], row["from_table"]) not in tier1_tables
            or (row["to_schema"], row["to_table"]) not in tier1_tables
        ):
            continue

        result.append(
            {
                **row,
                "relationship": (
                    f"{row['from_schema']}.{row['from_table']}.{row['from_column']}"
                    f" -> {row['to_schema']}.{row['to_table']}.{row['to_column']}"
                ),
            }
        )
    return result


def load_table_relationships(
    schema: str | None = None,
    tier1_tables: set[TableKey] | None = None,
) -> list[Relationship]:
    """Return direct FK-to-PK/UK edges between Tier 1 tables.

    ``tier1_tables`` is injectable to keep the transformation testable without
    a workbook. When omitted, classifications are read from the confirmed
    business YAML. Missing classifications produce no rows rather than
    treating every table as Tier 1.
    """
    selected_tables = load_tier1_tables() if tier1_tables is None else tier1_tables
    if not selected_tables:
        return []

    with get_connection(read_only=True) as connection:
        cursor = connection.cursor()
        try:
            cursor.execute(RELATIONSHIP_SQL, (schema, schema))
            rows = [
                {
                    "constraint_name": row[0],
                    "from_schema": row[1],
                    "from_table": row[2],
                    "from_column": row[3],
                    "to_schema": row[4],
                    "to_table": row[5],
                    "to_column": row[6],
                    "referenced_key_type": row[7],
                    "update_rule": row[8],
                    "delete_rule": row[9],
                    "column_position": row[10],
                }
                for row in cursor.fetchall()
            ]
        finally:
            cursor.close()

    return _filter_tier1_relationships(rows, selected_tables)
