"""Shared CSV/JSON/Excel/SQL export helper for pandas DataFrames."""
import pandas as pd

_FILTERS = "CSV Files (*.csv);;JSON Files (*.json);;Excel Files (*.xlsx);;SQL Insert (*.sql)"


def _sql_value_literal(val) -> str:
    if pd.isna(val):
        return "NULL"
    if isinstance(val, str):
        return f"'{val.replace(chr(39), chr(39) * 2)}'"
    return str(val)


def _to_sql_inserts(df, table_name: str) -> str:
    cols = ", ".join(f"`{c}`" for c in df.columns)
    lines = []
    for _, row in df.iterrows():
        values = ", ".join(_sql_value_literal(v) for v in row)
        lines.append(f"INSERT INTO `{table_name}` ({cols}) VALUES ({values});")
    return "\n".join(lines)


def export_dataframe(parent, df, default_name: str, table_name: str = "table"):
    """Prompt for a save file and export `df` as CSV/JSON/Excel/SQL.

    Shows an information/error dialog on the given `parent` widget.
    """
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    if df is None or df.empty:
        QMessageBox.information(parent, "Export", "No data to export.")
        return

    file_name, _ = QFileDialog.getSaveFileName(parent, "Export Data", default_name, _FILTERS)
    if not file_name:
        return

    try:
        if file_name.endswith(".json"):
            df.to_json(file_name, orient="records", indent=2, force_ascii=False)
        elif file_name.endswith(".xlsx"):
            df.to_excel(file_name, index=False, engine="openpyxl")
        elif file_name.endswith(".sql"):
            with open(file_name, "w", encoding="utf-8") as fh:
                fh.write(_to_sql_inserts(df, table_name))
        else:
            df.to_csv(file_name, index=False)
        QMessageBox.information(parent, "Export", f"Exported {len(df)} rows to:\n{file_name}")
    except Exception as ex:
        QMessageBox.critical(parent, "Export Error", str(ex))
