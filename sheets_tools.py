"""Google Sheets tool implementations.

Sheets API is range-oriented. Token-efficient defaults:
- Reads return just values (not full cell-format objects) unless include_formatting=True.
- Writes accept 2D arrays as-is.
- Formulas round-trip — pass value_input_option='USER_ENTERED' (default) to let
  Google parse '=SUM(A1:A10)' as a formula, not literal text.
"""

from __future__ import annotations

from typing import Any

from accounts import service


def create_spreadsheet(
    title: str,
    parent_folder_id: str | None = None,
    account: str | None = None,
) -> dict:
    """Create a new Google Sheets workbook."""
    sheets = service("sheets", "v4", account=account)
    drive = service("drive", "v3", account=account)

    created = sheets.spreadsheets().create(
        body={"properties": {"title": title}},
        fields="spreadsheetId,properties(title),sheets(properties(sheetId,title))",
    ).execute()
    spreadsheet_id = created["spreadsheetId"]

    if parent_folder_id:
        current = drive.files().get(fileId=spreadsheet_id, fields="parents").execute()
        previous = ",".join(current.get("parents", []))
        drive.files().update(
            fileId=spreadsheet_id,
            addParents=parent_folder_id,
            removeParents=previous,
            fields="id,parents",
        ).execute()

    return {
        "id": spreadsheet_id,
        "title": title,
        "link": f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit",
        "sheets": [s["properties"] for s in created.get("sheets", [])],
    }


def list_sheets(spreadsheet_id: str, account: str | None = None) -> list[dict]:
    """List all tabs in a workbook."""
    svc = service("sheets", "v4", account=account)
    resp = svc.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="sheets(properties(sheetId,title,index,gridProperties))",
    ).execute()
    return [
        {
            "sheet_id": s["properties"]["sheetId"],
            "title": s["properties"]["title"],
            "index": s["properties"].get("index"),
            "rows": s["properties"].get("gridProperties", {}).get("rowCount"),
            "cols": s["properties"].get("gridProperties", {}).get("columnCount"),
        }
        for s in resp.get("sheets", [])
    ]


def add_sheet(
    spreadsheet_id: str,
    title: str,
    rows: int = 1000,
    cols: int = 26,
    account: str | None = None,
) -> dict:
    """Add a new tab to an existing workbook."""
    svc = service("sheets", "v4", account=account)
    resp = svc.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={
            "requests": [
                {
                    "addSheet": {
                        "properties": {
                            "title": title,
                            "gridProperties": {"rowCount": rows, "columnCount": cols},
                        }
                    }
                }
            ]
        },
    ).execute()
    props = resp["replies"][0]["addSheet"]["properties"]
    return {
        "spreadsheet_id": spreadsheet_id,
        "sheet_id": props["sheetId"],
        "title": props["title"],
    }


def read_range(
    spreadsheet_id: str,
    range_a1: str,
    account: str | None = None,
    value_render: str = "FORMATTED_VALUE",
    date_render: str = "FORMATTED_STRING",
) -> dict:
    """Read a range of cells. Returns 2D array of values.

    Args:
        range_a1: A1 notation, e.g. 'Sheet1!A1:D100' or 'Sheet1'.
        value_render: 'FORMATTED_VALUE' | 'UNFORMATTED_VALUE' | 'FORMULA'.
            Use 'FORMULA' to see raw formulas instead of computed results.
        date_render: 'FORMATTED_STRING' | 'SERIAL_NUMBER'.
    """
    svc = service("sheets", "v4", account=account)
    resp = svc.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=range_a1,
        valueRenderOption=value_render,
        dateTimeRenderOption=date_render,
    ).execute()
    values = resp.get("values", [])
    return {
        "range": resp.get("range"),
        "row_count": len(values),
        "values": values,
    }


def write_range(
    spreadsheet_id: str,
    range_a1: str,
    values: list[list[Any]],
    account: str | None = None,
    value_input: str = "USER_ENTERED",
) -> dict:
    """Write a 2D array to a range. Overwrites.

    Args:
        value_input: 'USER_ENTERED' (parses formulas + dates) | 'RAW' (literal strings).
    """
    svc = service("sheets", "v4", account=account)
    resp = svc.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=range_a1,
        valueInputOption=value_input,
        body={"values": values},
    ).execute()
    return {
        "spreadsheet_id": spreadsheet_id,
        "range": resp.get("updatedRange"),
        "updated_rows": resp.get("updatedRows"),
        "updated_cols": resp.get("updatedColumns"),
        "updated_cells": resp.get("updatedCells"),
    }


def append_rows(
    spreadsheet_id: str,
    range_a1: str,
    values: list[list[Any]],
    account: str | None = None,
    value_input: str = "USER_ENTERED",
    insert_data: str = "INSERT_ROWS",
) -> dict:
    """Append rows below the existing data in a range.

    Args:
        range_a1: A table range or a sheet name (e.g. 'Sheet1!A:D' or 'Sheet1').
        insert_data: 'INSERT_ROWS' (shifts down) | 'OVERWRITE'.
    """
    svc = service("sheets", "v4", account=account)
    resp = svc.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=range_a1,
        valueInputOption=value_input,
        insertDataOption=insert_data,
        body={"values": values},
    ).execute()
    updates = resp.get("updates", {})
    return {
        "spreadsheet_id": spreadsheet_id,
        "range": updates.get("updatedRange"),
        "appended_rows": updates.get("updatedRows"),
        "appended_cells": updates.get("updatedCells"),
    }


def clear_range(
    spreadsheet_id: str,
    range_a1: str,
    account: str | None = None,
) -> dict:
    """Clear cell values in a range. Leaves formatting intact."""
    svc = service("sheets", "v4", account=account)
    resp = svc.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id,
        range=range_a1,
    ).execute()
    return {"spreadsheet_id": spreadsheet_id, "cleared_range": resp.get("clearedRange")}


def batch_read(
    spreadsheet_id: str,
    ranges: list[str],
    account: str | None = None,
    value_render: str = "FORMATTED_VALUE",
) -> dict:
    """Read multiple ranges in one call. More efficient than N read_range calls."""
    svc = service("sheets", "v4", account=account)
    resp = svc.spreadsheets().values().batchGet(
        spreadsheetId=spreadsheet_id,
        ranges=ranges,
        valueRenderOption=value_render,
    ).execute()
    return {
        "spreadsheet_id": spreadsheet_id,
        "ranges": [
            {"range": r.get("range"), "values": r.get("values", [])}
            for r in resp.get("valueRanges", [])
        ],
    }


def named_ranges_list(
    spreadsheet_id: str,
    account: str | None = None,
) -> list[dict]:
    """List all named ranges in a workbook."""
    svc = service("sheets", "v4", account=account)
    resp = svc.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="namedRanges",
    ).execute()
    return resp.get("namedRanges", [])


def named_range_add(
    spreadsheet_id: str,
    name: str,
    sheet_id: int,
    start_row: int,
    end_row: int,
    start_col: int,
    end_col: int,
    account: str | None = None,
) -> dict:
    """Create a named range. Row/col indices are zero-based, end-exclusive."""
    svc = service("sheets", "v4", account=account)
    resp = svc.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={
            "requests": [
                {
                    "addNamedRange": {
                        "namedRange": {
                            "name": name,
                            "range": {
                                "sheetId": sheet_id,
                                "startRowIndex": start_row,
                                "endRowIndex": end_row,
                                "startColumnIndex": start_col,
                                "endColumnIndex": end_col,
                            },
                        }
                    }
                }
            ]
        },
    ).execute()
    nr = resp["replies"][0]["addNamedRange"]["namedRange"]
    return {"spreadsheet_id": spreadsheet_id, "named_range_id": nr["namedRangeId"], "name": name}


def named_range_delete(
    spreadsheet_id: str,
    named_range_id: str,
    account: str | None = None,
) -> dict:
    """Delete a named range by id."""
    svc = service("sheets", "v4", account=account)
    svc.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"deleteNamedRange": {"namedRangeId": named_range_id}}]},
    ).execute()
    return {"spreadsheet_id": spreadsheet_id, "named_range_id": named_range_id, "status": "deleted"}


def conditional_format_add(
    spreadsheet_id: str,
    sheet_id: int,
    start_row: int,
    end_row: int,
    start_col: int,
    end_col: int,
    condition_type: str,
    condition_values: list[str],
    bg_color: dict | None = None,
    text_color: dict | None = None,
    bold: bool = False,
    account: str | None = None,
) -> dict:
    """Add a conditional formatting rule.

    Args:
        condition_type: e.g. 'NUMBER_GREATER', 'NUMBER_LESS', 'TEXT_CONTAINS',
            'TEXT_EQ', 'DATE_AFTER', 'CUSTOM_FORMULA', 'BLANK', 'NOT_BLANK'.
        condition_values: Values for the condition (e.g. ['100'] for NUMBER_GREATER;
            ['=A1>B1'] for CUSTOM_FORMULA; [] for BLANK/NOT_BLANK).
        bg_color / text_color: {'red': 0-1, 'green': 0-1, 'blue': 0-1}. Optional.
    """
    svc = service("sheets", "v4", account=account)
    text_format: dict[str, Any] = {}
    if text_color:
        text_format["foregroundColor"] = text_color
    if bold:
        text_format["bold"] = True

    fmt: dict[str, Any] = {}
    if bg_color:
        fmt["backgroundColor"] = bg_color
    if text_format:
        fmt["textFormat"] = text_format

    rule = {
        "ranges": [{
            "sheetId": sheet_id,
            "startRowIndex": start_row,
            "endRowIndex": end_row,
            "startColumnIndex": start_col,
            "endColumnIndex": end_col,
        }],
        "booleanRule": {
            "condition": {
                "type": condition_type,
                "values": [{"userEnteredValue": v} for v in condition_values],
            },
            "format": fmt,
        },
    }
    resp = svc.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={
            "requests": [{"addConditionalFormatRule": {"rule": rule, "index": 0}}]
        },
    ).execute()
    return {"spreadsheet_id": spreadsheet_id, "applied": True, "reply_count": len(resp.get("replies", []))}


def data_validation_add(
    spreadsheet_id: str,
    sheet_id: int,
    start_row: int,
    end_row: int,
    start_col: int,
    end_col: int,
    condition_type: str,
    condition_values: list[str],
    strict: bool = True,
    show_dropdown: bool = True,
    help_text: str | None = None,
    account: str | None = None,
) -> dict:
    """Set data validation on a range.

    Args:
        condition_type: 'ONE_OF_LIST' (dropdown), 'NUMBER_GREATER', 'NUMBER_BETWEEN',
            'DATE_IS_VALID', 'TEXT_IS_EMAIL', 'TEXT_IS_URL', 'CUSTOM_FORMULA', etc.
        condition_values: Values for the condition (e.g. ['yes','no','maybe'] for ONE_OF_LIST).
        strict: If True, reject invalid input. If False, show warning only.
    """
    svc = service("sheets", "v4", account=account)
    rule: dict[str, Any] = {
        "condition": {
            "type": condition_type,
            "values": [{"userEnteredValue": v} for v in condition_values],
        },
        "strict": strict,
        "showCustomUi": show_dropdown,
    }
    if help_text:
        rule["inputMessage"] = help_text

    resp = svc.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={
            "requests": [{
                "setDataValidation": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": start_row,
                        "endRowIndex": end_row,
                        "startColumnIndex": start_col,
                        "endColumnIndex": end_col,
                    },
                    "rule": rule,
                }
            }]
        },
    ).execute()
    return {"spreadsheet_id": spreadsheet_id, "applied": True, "reply_count": len(resp.get("replies", []))}


def batch_write(
    spreadsheet_id: str,
    updates: list[dict],
    account: str | None = None,
    value_input: str = "USER_ENTERED",
) -> dict:
    """Write multiple ranges in one call.

    Args:
        updates: List of {range: 'Sheet1!A1:B2', values: [[...], [...]]}.
    """
    svc = service("sheets", "v4", account=account)
    data = [
        {"range": u["range"], "values": u["values"]}
        for u in updates
    ]
    resp = svc.spreadsheets().values().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"valueInputOption": value_input, "data": data},
    ).execute()
    return {
        "spreadsheet_id": spreadsheet_id,
        "total_updated_cells": resp.get("totalUpdatedCells"),
        "total_updated_rows": resp.get("totalUpdatedRows"),
    }
