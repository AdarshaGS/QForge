"""Build a read-only row-level data diff between two database connections
(issue #203, design note ai/data-compare-design.md).

Sibling to services/schema_diff.py (structural diff) and
services/query_verifier.py (row diff, but hard-wired to a single db_service).

Row matching is key-based when the caller supplies key column(s). Without a
key, it falls back to whole-row matching — every common column together
stands in as the key, so two rows compare equal only when every value
matches. This is NOT positional (row N of one side vs row N of the other):
whole-row matching is a multiset comparison (via collections.Counter), the
same symmetric-diff approach services/query_verifier.py already uses for its
own ORDER-BY-agnostic comparison, so result-set ordering never affects the
outcome. A "modified" row is meaningless in this mode — if every column has
to match for two rows to be the same key, nothing can differ between two
rows sharing a key — so the fallback only ever reports added/removed/
unchanged, correctly counting exact duplicate rows via their multiset counts
on each side.

Each side gets its own dedicated, throwaway DbService connection (same
pattern as schema_diff.py._fetch_side), so this can run on a background
thread without touching a connection either caller might be using
concurrently. No changes are ever made to either database — this is a
read-only comparison.
"""
from collections import Counter
from dataclasses import dataclass, field

from services.db_service import DbService
from utils.df_export import _quote_identifier
from utils.logger import get_logger

logger = get_logger()

_DISPLAY_CAP = 500


def table_select_sql(table_name: str, db_type: str) -> str:
    """Table mode's "resolve to SELECT * FROM <table>" step (see module
    docstring / ai/data-compare-design.md) — a separate, callable step so
    the caller (ui/data_compare_dialog.py) can build this before the table
    name reaches build_data_diff, which otherwise only ever deals in
    already-valid SELECT strings for both table and query mode alike."""
    dialect = "mysql" if db_type == "mysql" else "postgresql"
    return f"SELECT * FROM {_quote_identifier(table_name, dialect)}"


@dataclass
class RowDiff:
    key: str  # display string, e.g. "42" or "42|acme"
    change: str  # "added" | "removed" | "modified"
    source_row: dict = None
    target_row: dict = None
    field_changes: dict = field(default_factory=dict)  # col -> (source_val, target_val)


@dataclass
class DataDiff:
    columns: list = field(default_factory=list)
    rows_added: list = field(default_factory=list)      # list[RowDiff], capped at _DISPLAY_CAP
    rows_removed: list = field(default_factory=list)    # list[RowDiff], capped at _DISPLAY_CAP
    rows_modified: list = field(default_factory=list)   # list[RowDiff], capped at _DISPLAY_CAP
    rows_added_total: int = 0
    rows_removed_total: int = 0
    rows_modified_total: int = 0
    rows_unchanged: int = 0
    truncated_source: bool = False
    truncated_target: bool = False


def build_data_diff(source_config: dict, target_config: dict,
                     source_sql: str, target_sql: str,
                     key_columns: list = None, row_limit: int = 50000) -> DataDiff:
    """Connect to *source_config* then *target_config* (each its own
    dedicated connection), run *source_sql*/*target_sql* on each side capped
    at *row_limit* rows (0/None means no cap), then diff.

    *key_columns* is optional — an empty/None value compares whole rows (see
    module docstring). When given, rows are matched by those column(s) only,
    so a match with any other column differing shows as "modified".

    *source_sql*/*target_sql* are already-valid SELECT statements — either
    built by table_select_sql() (table mode) or an arbitrary user-typed
    SELECT (query mode). This function only ever sees the final SQL string,
    not which mode produced it.
    """
    key_columns = [c.strip() for c in (key_columns or []) if c and c.strip()]

    src_records, src_cols, truncated_source = _fetch_side(source_config, source_sql, key_columns, row_limit)
    tgt_records, tgt_cols, truncated_target = _fetch_side(target_config, target_sql, key_columns, row_limit)

    common_cols = [c for c in src_cols if c in set(tgt_cols)]
    columns = common_cols or src_cols or tgt_cols

    if not key_columns:
        return _diff_whole_rows(src_records, tgt_records, common_cols, columns,
                                 truncated_source, truncated_target)

    missing = [c for c in key_columns if c not in common_cols]
    if missing:
        raise ValueError(
            f"Key column(s) {', '.join(missing)} not found on both sides — "
            f"common columns: {', '.join(common_cols)}")

    return _diff_by_key(src_records, tgt_records, key_columns, common_cols, columns,
                         truncated_source, truncated_target)


def _diff_by_key(src_records: list, tgt_records: list, key_columns: list, common_cols: list,
                  columns: list, truncated_source: bool, truncated_target: bool) -> DataDiff:
    src_rows = _index_by_key(src_records, key_columns)
    tgt_rows = _index_by_key(tgt_records, key_columns)

    diff = DataDiff(columns=columns, truncated_source=truncated_source, truncated_target=truncated_target)

    for key in sorted(set(src_rows) | set(tgt_rows), key=str):
        in_source = key in src_rows
        in_target = key in tgt_rows
        key_label = "|".join(str(k) for k in key) if isinstance(key, tuple) else str(key)
        if in_source and not in_target:
            _append_capped(diff.rows_removed, RowDiff(key=key_label, change="removed", source_row=src_rows[key]))
            diff.rows_removed_total += 1
        elif in_target and not in_source:
            _append_capped(diff.rows_added, RowDiff(key=key_label, change="added", target_row=tgt_rows[key]))
            diff.rows_added_total += 1
        else:
            s, t = src_rows[key], tgt_rows[key]
            changes = {c: (s.get(c), t.get(c)) for c in common_cols if s.get(c) != t.get(c)}
            if changes:
                _append_capped(diff.rows_modified,
                               RowDiff(key=key_label, change="modified", source_row=s, target_row=t,
                                       field_changes=changes))
                diff.rows_modified_total += 1
            else:
                diff.rows_unchanged += 1

    return diff


def _diff_whole_rows(src_records: list, tgt_records: list, common_cols: list, columns: list,
                      truncated_source: bool, truncated_target: bool) -> DataDiff:
    """No key given — every common column together is the key, so two rows
    only ever match when identical. Counter-based multiset diff (same
    symmetric-diff idea QueryVerifier.verify uses via value_counts) so exact
    duplicate rows are counted correctly rather than collapsed."""
    def _row_key(row_dict):
        return tuple(row_dict.get(c) for c in common_cols)

    src_counts = Counter(_row_key(r) for r in src_records)
    tgt_counts = Counter(_row_key(r) for r in tgt_records)

    diff = DataDiff(columns=columns, truncated_source=truncated_source, truncated_target=truncated_target)

    for key in sorted(set(src_counts) | set(tgt_counts), key=str):
        s_count, t_count = src_counts.get(key, 0), tgt_counts.get(key, 0)
        matched = min(s_count, t_count)
        diff.rows_unchanged += matched

        row_dict = dict(zip(common_cols, key))
        key_label = "|".join(str(v) for v in key)
        for _ in range(s_count - matched):
            _append_capped(diff.rows_removed, RowDiff(key=key_label, change="removed", source_row=row_dict))
            diff.rows_removed_total += 1
        for _ in range(t_count - matched):
            _append_capped(diff.rows_added, RowDiff(key=key_label, change="added", target_row=row_dict))
            diff.rows_added_total += 1

    return diff


def _append_capped(bucket: list, row_diff: RowDiff):
    if len(bucket) < _DISPLAY_CAP:
        bucket.append(row_diff)


def _index_by_key(records: list, key_columns: list) -> dict:
    rows = {}
    for row_dict in records:
        if len(key_columns) == 1:
            key = row_dict.get(key_columns[0])
        else:
            key = tuple(row_dict.get(k) for k in key_columns)
        rows[key] = row_dict
    return rows


def _fetch_side(config: dict, sql: str, order_columns: list, row_limit: int):
    """Returns (list[row_dict], columns, truncated)."""
    db = DbService()
    db.connect(config)
    try:
        wrapped = _wrap_query(sql, order_columns, db.db_type)
        max_rows = row_limit if row_limit and row_limit > 0 else None
        df = db.execute_query(wrapped, max_rows=max_rows)
        truncated = bool(df.attrs.get("truncated"))

        columns = list(df.columns)
        str_df = df.astype(str)
        records = [r.to_dict() for _, r in str_df.iterrows()]

        return records, columns, truncated
    finally:
        db.disconnect()


def _wrap_query(sql: str, order_columns: list, db_type: str) -> str:
    """Wrap *sql* to sort by *order_columns* (the chosen key columns), the
    same wrapping style QueryVerifier._maybe_limit uses for its own LIMIT
    subquery. Ordering makes a capped fetch take a deterministic (lowest-key)
    slice instead of whatever order the database happens to return. No
    ordering is applied in whole-row mode (no key columns) — matching there
    is a multiset comparison and doesn't depend on order, and there's no
    single set of columns known to be safe/available to sort by until both
    sides have already been fetched.

    Deliberately no SQL-level LIMIT here — the row cap is applied by
    DbService.execute_query's max_rows (cursor.fetchmany), which peeks one
    extra row to set df.attrs['truncated'] correctly. A LIMIT baked into the
    SQL itself would make the driver return at most that many rows
    regardless of the underlying result size, so the truncation flag would
    never fire even when there's more data than was compared."""
    clean = sql.rstrip().rstrip(";").rstrip()
    if not order_columns:
        return f"SELECT * FROM ({clean}) _dc"  # nosec B608
    dialect = "mysql" if db_type == "mysql" else "postgresql"
    order_by = ", ".join(_quote_identifier(c, dialect) for c in order_columns)
    # clean is the user's own table/query selection (SECURITY.md: executing
    # whatever SQL a user chooses to write/select is the product's job) —
    # order_columns are quoted above via _quote_identifier.
    return f"SELECT * FROM ({clean}) _dc ORDER BY {order_by}"  # nosec B608
