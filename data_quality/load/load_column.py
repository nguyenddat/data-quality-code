from typing import Any

from core.database import get_connection
from utils.formatter import quote_identifier


def _load_column_statistics(
    conn: Any,
    schema: str,
    table: str,
    column: str,
) -> dict[str, Any]:
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT
                pc.reltuples,
                s.null_frac
            FROM pg_stats s
            JOIN pg_class pc
                ON pc.relname = s.tablename
               AND pc.relnamespace = (
                   SELECT oid
                   FROM pg_namespace
                   WHERE nspname = s.schemaname
               )
            WHERE s.schemaname = ?
              AND s.tablename = ?
              AND s.attname = ?;
            """,
            (schema, table, column),
        )
        row = cursor.fetchone()
        cursor.execute(
            f"SELECT COUNT(DISTINCT {quote_identifier(column)}) "
            f"FROM {quote_identifier(schema)}.{quote_identifier(table)}"
        )
        distinct_count = int(cursor.fetchone()[0] or 0)
    finally:
        cursor.close()

    if row is None:
        return {
            "total_rows": 0,
            "null_pct": None,
            "distinct_count": distinct_count,
        }

    total_rows = max(int(row[0] or 0), 0)
    null_frac = float(row[1] or 0)

    return {
        "total_rows": total_rows,
        "null_pct": round(null_frac * 100, 1),
        "distinct_count": distinct_count,
    }


def load_table_with_columns(schema: str, table: str) -> list[dict]:
    with get_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT
                    c.column_name,
                    c.data_type,
                    format_type(a.atttypid, a.atttypmod) AS full_type,
                    c.is_nullable,
                    c.column_default,
                    c.ordinal_position,
                    COALESCE(col_description(pc.oid, a.attnum), '') AS comment,
                    EXISTS (
                        SELECT 1 FROM information_schema.table_constraints tc
                        JOIN information_schema.key_column_usage kcu
                          ON kcu.constraint_schema = tc.constraint_schema
                         AND kcu.constraint_name = tc.constraint_name
                         AND kcu.table_schema = tc.table_schema
                         AND kcu.table_name = tc.table_name
                        WHERE tc.constraint_type = 'PRIMARY KEY'
                          AND kcu.table_schema = c.table_schema
                          AND kcu.table_name = c.table_name
                          AND kcu.column_name = c.column_name
                    ) AS is_pk,
                    EXISTS (
                        SELECT 1 FROM information_schema.table_constraints tc
                        JOIN information_schema.key_column_usage kcu
                          ON kcu.constraint_schema = tc.constraint_schema
                         AND kcu.constraint_name = tc.constraint_name
                         AND kcu.table_schema = tc.table_schema
                         AND kcu.table_name = tc.table_name
                        WHERE tc.constraint_type = 'FOREIGN KEY'
                          AND kcu.table_schema = c.table_schema
                          AND kcu.table_name = c.table_name
                          AND kcu.column_name = c.column_name
                    ) AS is_fk,
                    EXISTS (
                        SELECT 1 FROM information_schema.table_constraints tc
                        JOIN information_schema.key_column_usage kcu
                          ON kcu.constraint_schema = tc.constraint_schema
                         AND kcu.constraint_name = tc.constraint_name
                         AND kcu.table_schema = tc.table_schema
                         AND kcu.table_name = tc.table_name
                        WHERE tc.constraint_type = 'UNIQUE'
                          AND kcu.table_schema = c.table_schema
                          AND kcu.table_name = c.table_name
                          AND kcu.column_name = c.column_name
                    ) AS is_uq
                FROM information_schema.columns c
                JOIN pg_namespace pn ON pn.nspname = c.table_schema
                JOIN pg_class pc
                  ON pc.relnamespace = pn.oid AND pc.relname = c.table_name
                JOIN pg_attribute a
                  ON a.attrelid = pc.oid AND a.attname = c.column_name
                 AND a.attnum > 0 AND NOT a.attisdropped
                WHERE c.table_schema = ? AND c.table_name = ?
                ORDER BY c.ordinal_position;
                """,
                (schema, table),
            )
            columns = [
                {
                    "name": row[0],
                    "data_type": row[1],
                    "full_type": row[2],
                    "nullable": row[3] == "YES",
                    "default": row[4],
                    "position": row[5],
                    "comment": row[6] or "",
                    "pk": bool(row[7]),
                    "fk": bool(row[8]),
                    "uq": bool(row[9]),
                    "pii_phi": "",
                }
                for row in cursor.fetchall()
            ]

            for column in columns:
                column["statistics"] = _load_column_statistics(
                    conn,
                    schema,
                    table,
                    column["name"],
                )

            return columns
        finally:
            cursor.close()
