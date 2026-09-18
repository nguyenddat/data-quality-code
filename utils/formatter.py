def quote_identifier(identifier: str) -> str:
    """Quote a PostgreSQL identifier safely for SQL statements."""
    return '"' + identifier.replace('"', '""') + '"'


def parse_table_reference(value: str) -> tuple[str, str]:
    """Parse a compact ``schema.table`` reference from a business rule."""
    parts = str(value).split(".")
    if len(parts) != 2 or not all(parts):
        raise ValueError(f"Expected schema.table reference, got: {value!r}")
    return parts[0], parts[1]


def parse_column_reference(value: str) -> tuple[str, str, str]:
    """Parse a compact ``schema.table.column`` reference from a business rule."""
    parts = str(value).split(".")
    if len(parts) != 3 or not all(parts):
        raise ValueError(f"Expected schema.table.column reference, got: {value!r}")
    return parts[0], parts[1], parts[2]
