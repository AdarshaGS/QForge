# QForge — AI Flush Context

Use this file to leave an accurate handoff when work on QForge pauses or ends.
It is deliberately a living record: replace the template sections with concrete
facts from the current task. Do not record passwords, access tokens, private
hostnames, customer data, or unredacted sensitive SQL.

## Current handoff

**Status:** Write entry points fully mapped across QForge. Detailed implementation plan for Slice 2 (Read-Only Mode & Dangerous Query Guards) created.

**Last updated:** 2026-07-22

**Goal:** Add production safety controls to QForge in small, safe slices.

**Next recommended slice:** Implement `QueryClassifier` and enforce client-side + native read-only guards and production dangerous-query confirmation dialogs.

## Known implementation context

- QForge is a PySide6 desktop database client for MySQL, PostgreSQL, and SQLite.
- Connection metadata is managed by `ui/connection_dialog.py`.
- SQL is dispatched from `ui/connection_panel.py`; low-level execution is in `services/db_service.py`.
- Complete inventory of write entry points mapped in `implementation_plan.md` (Editor execution, Multi-script, Table Grid commits, Create/Alter table dialogs, CSV import).
- Connection passwords are stored separately through `utils/credential_manager.py`; never copy them into profile JSON or logs.
- The worktree may contain user edits unrelated to this feature. Preserve them.

## Write Entry Point Inventory

| Entry Point | Location | Mechanism | Operation Type |
| --- | --- | --- | --- |
| **SQL Editor Single Query** | `ui/connection_panel.py:77` | `QueryWorker` -> `DbService.execute_query()` | Ad-hoc DML/DDL/DQL |
| **SQL Editor Multi-Script** | `ui/connection_panel.py:70` | `QueryWorker` -> `DbService.execute_multi_query()` | Multi-statement DML/DDL |
| **Direct Update Helper** | `services/db_service.py:449` | `DbService.execute_update()` | DML / DDL |
| **Result Grid Edits (Save)** | `ui/table_view_widget.py:774` | `TableViewWidget.commit_changes()` -> `execute_update()` | Inline UPDATE, INSERT, DELETE |
| **Result Grid Edits (Panel)** | `ui/connection_panel.py:742` | `ConnectionPanel._execute_commit_sql()` -> `execute_update()` | Inline UPDATE, INSERT, DELETE |
| **Create Table Dialog** | `ui/connection_panel.py:1144` | `StructureEditorDialog` -> `execute_update()` | DDL (`CREATE TABLE`) |
| **Alter Table Dialog** | `ui/connection_panel.py:1285` | `AlterTableDialog` -> `execute_update()` | DDL (`ALTER TABLE`) |
| **CSV Data Import** | `ui/connection_panel.py:1364` | `_import_csv_into_table()` -> `cursor.executemany()` | Batch `INSERT` |

## Exact next step

Obtain user review on [`implementation_plan.md`](file:///Users/adarsh/.gemini/antigravity-ide/brain/9d7c21cc-aa9f-46a5-b0ff-92908837138e/implementation_plan.md), then implement `services/query_classifier.py`, profile read-only toggles, and safety guards in `services/db_service.py` and `ui/connection_panel.py`.
