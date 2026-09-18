from datetime import datetime, timedelta, timezone
from typing import Any

from core.database import get_connection
from utils.formatter import quote_identifier


def _load_statistics(
    conn: Any,
    schema: str,
    table: str,
    max_age_days: int,
) -> dict[str, Any]:
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT last_analyze, last_autoanalyze
            FROM pg_stat_all_tables
            WHERE schemaname = ?
              AND relname = ?;
            """,
            (schema, table),
        )
        row = cursor.fetchone()
    finally:
        cursor.close()

    if row is None:
        raise ValueError(f"Table not found: {schema}.{table}")

    timestamps = [
        timestamp.replace(tzinfo=timezone.utc)
        if timestamp.tzinfo is None
        else timestamp
        for timestamp in row
        if timestamp is not None
    ]
    analyzed_at = max(timestamps, default=None)
    age = (
        None
        if analyzed_at is None
        else datetime.now(timezone.utc) - analyzed_at
    )
    needs_analyze = analyzed_at is None or age > timedelta(days=max_age_days)
    analyzed_now = False

    if needs_analyze:
        qualified_table = (
            f"{quote_identifier(schema)}.{quote_identifier(table)}"
        )
        conn.execute(f"ANALYZE {qualified_table}")
        analyzed_now = True

    return {
        "last_analyze": analyzed_at,
        "age_days": None if age is None else age.total_seconds() / 86400,
        "needs_analyze": needs_analyze,
        "analyzed_now": analyzed_now,
    }


def load_tables(schema: str, max_age_days: int = 5) -> list[dict]:
    if max_age_days < 0:
        raise ValueError("max_age_days must be greater than or equal to 0")

    # ANALYZE updates PostgreSQL statistics, so this loader needs a
    # read-write attachment while the other loaders remain read-only.
    with get_connection(read_only=False) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT
                    t.table_schema AS schema_name,
                    t.table_name,
                    obj_description(pc.oid, 'pg_class') AS table_comment,
                    COALESCE(s.n_live_tup, 0) AS row_count,
                    COUNT(DISTINCT c.column_name) AS column_count,
                    pg_size_pretty(pg_total_relation_size(pc.oid)) AS total_size,
                    pg_size_pretty(pg_relation_size(pc.oid)) AS data_size,
                    pg_size_pretty(pg_indexes_size(pc.oid)) AS index_size,
                    (
                        SELECT COUNT(*)
                        FROM information_schema.table_constraints x
                        WHERE x.table_schema = t.table_schema
                          AND x.table_name = t.table_name
                          AND x.constraint_type = 'PRIMARY KEY'
                    ) AS has_pk,
                    (
                        SELECT COUNT(*)
                        FROM information_schema.table_constraints x
                        WHERE x.table_schema = t.table_schema
                          AND x.table_name = t.table_name
                          AND x.constraint_type = 'FOREIGN KEY'
                    ) AS fk_count,
                    t.table_type
                FROM information_schema.tables t
                JOIN information_schema.columns c
                    ON c.table_schema = t.table_schema
                   AND c.table_name = t.table_name
                LEFT JOIN pg_class pc
                    ON pc.relname = t.table_name
                   AND pc.relnamespace = (
                       SELECT oid
                       FROM pg_namespace
                       WHERE nspname = t.table_schema
                   )
                LEFT JOIN pg_stat_user_tables s
                    ON s.schemaname = t.table_schema
                   AND s.relname = t.table_name
                WHERE t.table_schema = ?
                  AND t.table_type = 'BASE TABLE'
                GROUP BY
                    t.table_schema,
                    t.table_name,
                    pc.oid,
                    obj_description(pc.oid, 'pg_class'),
                    s.n_live_tup,
                    t.table_type
                ORDER BY t.table_name;
                """,
                (schema,),
            )
            tables = [
                {
                    "schema_name": row[0],
                    "table_name": row[1],
                    "comment": row[2],
                    "row_count": row[3],
                    "column_count": row[4],
                    "total_size": row[5],
                    "data_size": row[6],
                    "index_size": row[7],
                    "has_pk": row[8],
                    "fk_count": row[9],
                    "table_type": row[10],
                }
                for row in cursor.fetchall()
            ]

            for table_metadata in tables:
                table_metadata["statistics"] = _load_statistics(
                    conn,
                    schema,
                    table_metadata["table_name"],
                    max_age_days,
                )

            return tables
        finally:
            cursor.close()
