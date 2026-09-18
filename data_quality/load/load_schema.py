import json
from datetime import date, datetime
from pathlib import Path

from core.settings import config
from core.database import get_connection

from .load_column import load_table_with_columns
from .load_table import load_tables


def load_schemas() -> list[str]:
    with get_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT schema_name
                FROM information_schema.schemata
                WHERE schema_name NOT IN (
                    'pg_catalog',
                    'information_schema',
                    'pg_toast'
                )
                  AND schema_name NOT LIKE 'pg_temp_%'
                ORDER BY schema_name;
            """)
            return [row[0] for row in cursor.fetchall()]

        finally:
            cursor.close()


def load_schema_summary(schema: str) -> dict:
    with get_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT
                    obj_description(n.oid, 'pg_namespace') AS comment,
                    COUNT(DISTINCT c.relname) AS table_count,
                    pg_size_pretty(
                        COALESCE(SUM(pg_total_relation_size(c.oid)), 0)
                    ) AS total_size
                FROM pg_namespace n
                LEFT JOIN pg_class c
                    ON c.relnamespace = n.oid
                   AND c.relkind = 'r'
                WHERE n.nspname = ?
                GROUP BY n.nspname, n.oid;
                """,
                (schema,),
            )
            row = cursor.fetchone()
        finally:
            cursor.close()

    if row is None:
        raise ValueError(f"Schema not found: {schema}")

    return {
        "schema": schema,
        "table_count": row[1] or 0,
        "total_size": row[2] or "0 bytes",
        "comment": row[0] or "",
    }

def save_schema(schema: str, data: dict) -> Path:
    file_path = Path(config.schema_cache_file(schema))
    file_path.parent.mkdir(parents=True, exist_ok=True)

    with file_path.open("w", encoding="utf-8") as file:
        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2,
            default=lambda value: (
                value.isoformat()
                if isinstance(value, (date, datetime))
                else str(value)
            ),
        )
        file.write("\n")

    return file_path

def load_schema(
    schema: str,
    max_age_days: int = 5,
    refresh: bool = False,
) -> dict:
    file_path = Path(config.schema_cache_file(schema))
    if file_path.exists() and not refresh:
        with file_path.open("r", encoding="utf-8") as file:
            return json.load(file)

    data = {}
    for table in load_tables(schema, max_age_days=max_age_days):
        table_name = table["table_name"]
        data[table_name] = {
            **table,
            "columns": load_table_with_columns(schema, table_name),
        }

    save_schema(schema, data)
    return data
