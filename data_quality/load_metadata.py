from pathlib import Path
from typing import Any

from core.settings import config
from data_quality.load.load_schema import load_schema, load_schema_summary, load_schemas
from utils.excel import load_or_create_workbook, save_excel, setup_schema_sheet


def _reconcile_schemas(
    sheet: Any,
    existing_data: list[dict[str, Any]],
    rows: list[dict[str, Any]],
) -> None:
    """Update mismatched schema rows and append schemas missing from the sheet."""
    existing_rows = {
        item["schema"]: row
        for row, item in enumerate(existing_data, start=2)
    }
    existing_data_by_schema = {item["schema"]: item for item in existing_data}

    for item in rows:
        values = [
            item["schema"],
            item["table_count"],
            item["total_size"],
            item["comment"],
        ]
        row_number = existing_rows.get(item["schema"])
        if row_number is None:
            row_number = sheet.max_row + 1
            existing_rows[item["schema"]] = row_number
        elif all(
            existing_data_by_schema[item["schema"]][field] == value
            for field, value in zip(
                ("schema", "table_count", "total_size", "comment"), values
            )
        ):
            continue

        for column, value in enumerate(values, start=1):
            sheet.cell(row=row_number, column=column, value=value)

    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:D{max(sheet.max_row, 1)}"


def load_metadata(max_age_days: int = 5) -> list[Path]:
    if max_age_days < 0:
        raise ValueError("max_age_days must be greater than or equal to 0")

    cache_files: list[Path] = []
    summaries: list[dict[str, Any]] = []
    for schema in load_schemas():
        metadata = load_schema(schema=schema, max_age_days=max_age_days)
        cache_files.append(Path(config.schema_cache_file(schema)))
        summary = load_schema_summary(schema)
        summary["table_count"] = len(metadata)
        summaries.append(summary)

    workbook = load_or_create_workbook()
    sheet, existing_rows = setup_schema_sheet(workbook)
    _reconcile_schemas(sheet, existing_rows, summaries)
    save_excel(workbook)
    return cache_files


if __name__ == "__main__":
    load_metadata()
