"""Build a read-only structural diff between two database schemas (issue #68).

Independent of any UI rendering — see ui/schema_compare_dialog.py for the
dialog that renders this. Each side gets its own dedicated, throwaway
DbService connection (same pattern and rationale as services/erd_model.py
and services/schema_snapshot.py) rather than a caller-supplied shared one,
so this can safely run on a background thread without corrupting a
connection either caller might be using concurrently.
"""
from dataclasses import dataclass, field

from services.db_service import DbService
from utils.logger import get_logger

logger = get_logger()

_COLUMN_FIELDS = ("data_type", "nullable", "default", "is_primary_key")
_INDEX_FIELDS = ("columns", "unique", "type")


@dataclass
class ColumnInfo:
    name: str
    data_type: str = ""
    nullable: bool = True
    default: object = None
    is_primary_key: bool = False


@dataclass
class ColumnDiff:
    name: str
    change: str  # "added" | "removed" | "modified"
    source: ColumnInfo = None
    target: ColumnInfo = None
    field_changes: dict = field(default_factory=dict)  # field -> (source_val, target_val)


@dataclass
class IndexDiff:
    name: str
    change: str
    source: dict = None
    target: dict = None
    field_changes: dict = field(default_factory=dict)


@dataclass
class ForeignKeyDiff:
    label: str  # "column -> ref_table.ref_column"
    change: str  # "added" | "removed"


@dataclass
class TableDiff:
    name: str
    columns: list = field(default_factory=list)        # list[ColumnDiff]
    indexes: list = field(default_factory=list)         # list[IndexDiff]
    foreign_keys: list = field(default_factory=list)    # list[ForeignKeyDiff]

    @property
    def has_changes(self) -> bool:
        return bool(self.columns or self.indexes or self.foreign_keys)


@dataclass
class SchemaDiff:
    tables_added: list = field(default_factory=list)      # list[str]
    tables_removed: list = field(default_factory=list)    # list[str]
    tables_modified: list = field(default_factory=list)   # list[TableDiff]
    tables_unchanged_names: list = field(default_factory=list)  # list[str]

    @property
    def tables_unchanged(self) -> int:
        """Count only — kept for existing callers/tests. The names
        themselves are what the UI needs to actually list them, hence
        tables_unchanged_names above."""
        return len(self.tables_unchanged_names)


def build_schema_diff(source_config: dict, target_config: dict, table_names: list = None) -> SchemaDiff:
    """Connect to *source_config* then *target_config* (each its own
    dedicated connection — see module docstring), gather table/column/
    index/FK metadata, then diff. A single table's metadata failing to load
    on either side doesn't abort the whole comparison — it's just skipped
    for that side (mirrors build_erd_graph's handling of missing/incomplete
    metadata). No changes are ever applied to either database — this is a
    read-only comparison."""
    source = _fetch_side(source_config, table_names)
    target = _fetch_side(target_config, table_names)

    diff = SchemaDiff()
    for name in sorted(set(source) | set(target)):
        in_source = name in source
        in_target = name in target
        if in_source and not in_target:
            diff.tables_removed.append(name)
        elif in_target and not in_source:
            diff.tables_added.append(name)
        else:
            table_diff = _diff_table(name, source[name], target[name])
            if table_diff.has_changes:
                diff.tables_modified.append(table_diff)
            else:
                diff.tables_unchanged_names.append(name)

    return diff


def _fetch_side(config: dict, table_names: list = None) -> dict:
    """Returns {table_name: (columns_by_name, indexes, foreign_keys)}."""
    db = DbService()
    db.connect(config)
    try:
        if table_names is not None:
            names = table_names
        else:
            try:
                names = db.get_tables()
            except Exception as ex:
                logger.debug(f"Schema diff: failed to list tables: {ex}")
                return {}

        # Bulk, single-round-trip metadata (same calls services/erd_model.py
        # and services/schema_snapshot.py already use) instead of a
        # get_columns()/get_primary_keys()/get_foreign_keys() loop per
        # table — that was up to 3 round trips PER TABLE PER SIDE, the
        # slowest part of opening Schema Compare on a non-trivial schema.
        # get_indexes() has no bulk equivalent, so it stays per-table below.
        try:
            column_details = db.get_all_column_details()
        except Exception as ex:
            logger.debug(f"Schema diff: failed to read column details: {ex}")
            column_details = {}

        try:
            fks_by_table = db.get_all_foreign_keys()
        except Exception as ex:
            logger.debug(f"Schema diff: failed to read foreign keys: {ex}")
            fks_by_table = {}

        result = {}
        for name in names:
            cols = column_details.get(name)
            if cols is None:
                logger.debug(f"Schema diff: no column details for {name}")
                continue

            columns = {
                col["name"]: ColumnInfo(
                    name=col["name"],
                    data_type=str(col.get("type", "") or ""),
                    nullable=bool(col.get("nullable", True)),
                    default=col.get("default"),
                    is_primary_key=col.get("key") == "PRI",
                )
                for col in cols
            }

            try:
                indexes = db.get_indexes(name)
            except Exception as ex:
                logger.debug(f"Schema diff: failed to read indexes for {name}: {ex}")
                indexes = []

            result[name] = (columns, indexes, fks_by_table.get(name, []))

        return result
    finally:
        db.disconnect()


def _diff_table(name: str, source: tuple, target: tuple) -> TableDiff:
    source_cols, source_idx, source_fks = source
    target_cols, target_idx, target_fks = target

    return TableDiff(
        name=name,
        columns=_diff_columns(source_cols, target_cols),
        indexes=_diff_indexes(source_idx, target_idx),
        foreign_keys=_diff_foreign_keys(source_fks, target_fks),
    )


def _diff_columns(source_cols: dict, target_cols: dict) -> list:
    diffs = []
    for col_name in sorted(set(source_cols) | set(target_cols)):
        in_source = col_name in source_cols
        in_target = col_name in target_cols
        if in_source and not in_target:
            diffs.append(ColumnDiff(name=col_name, change="removed", source=source_cols[col_name]))
        elif in_target and not in_source:
            diffs.append(ColumnDiff(name=col_name, change="added", target=target_cols[col_name]))
        else:
            s, t = source_cols[col_name], target_cols[col_name]
            changes = {
                f: (getattr(s, f), getattr(t, f))
                for f in _COLUMN_FIELDS
                if getattr(s, f) != getattr(t, f)
            }
            if changes:
                diffs.append(ColumnDiff(name=col_name, change="modified", source=s, target=t,
                                         field_changes=changes))
    return diffs


def _diff_indexes(source_idx: list, target_idx: list) -> list:
    source_by_name = {i["name"]: i for i in source_idx if i.get("name")}
    target_by_name = {i["name"]: i for i in target_idx if i.get("name")}
    diffs = []
    for idx_name in sorted(set(source_by_name) | set(target_by_name)):
        in_source = idx_name in source_by_name
        in_target = idx_name in target_by_name
        if in_source and not in_target:
            diffs.append(IndexDiff(name=idx_name, change="removed", source=source_by_name[idx_name]))
        elif in_target and not in_source:
            diffs.append(IndexDiff(name=idx_name, change="added", target=target_by_name[idx_name]))
        else:
            s, t = source_by_name[idx_name], target_by_name[idx_name]
            changes = {f: (s.get(f), t.get(f)) for f in _INDEX_FIELDS if s.get(f) != t.get(f)}
            if changes:
                diffs.append(IndexDiff(name=idx_name, change="modified", source=s, target=t,
                                        field_changes=changes))
    return diffs


def _diff_foreign_keys(source_fks: list, target_fks: list) -> list:
    # No constraint-name column is fetched by db_service.get_foreign_keys —
    # identity is the (column, ref_table, ref_column) triple, so a changed
    # FK shows as a remove + add rather than a "modified" entry.
    def _key(fk):
        return (fk.get("column", ""), fk.get("ref_table", ""), fk.get("ref_column", ""))

    def _label(fk):
        return f"{fk.get('column', '')} → {fk.get('ref_table', '')}.{fk.get('ref_column', '')}"

    source_keys = {_key(fk): fk for fk in source_fks}
    target_keys = {_key(fk): fk for fk in target_fks}
    diffs = []
    for key in sorted(set(source_keys) | set(target_keys)):
        if key in source_keys and key not in target_keys:
            diffs.append(ForeignKeyDiff(label=_label(source_keys[key]), change="removed"))
        elif key in target_keys and key not in source_keys:
            diffs.append(ForeignKeyDiff(label=_label(target_keys[key]), change="added"))
    return diffs
