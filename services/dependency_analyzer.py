"""Schema/query impact analysis (issue #236) — "what else in this database
depends on this table or column, so I know what breaks if I change it."

Pure logic, no Qt. Unlike services/erd_model.py, this takes an
already-connected DbService rather than a config: callers need it to run
synchronously and cheaply (before a DROP proceeds, or from a context-menu
click), the same convention ui/connection_panel.py already uses for
get_columns()/get_foreign_keys() elsewhere — no dedicated background
connection needed for read-only catalog metadata.

Foreign-key dependents are exact (read straight from DbService.
get_all_foreign_keys()). View/function/procedure/trigger dependents are a
best-effort text search over each object's real definition, fetched from
the database's own catalog (information_schema/pg_catalog) — never a
text-search over locally saved queries, per the issue's own distinction.
"""
import re
from dataclasses import dataclass, field

SUPPORTED_DB_TYPES = ("mysql", "postgresql")

# Canonical (report attribute, ref kind, display title) for every
# dependency category, in the fixed order the UI presents them. Single
# source of truth shared by the report model, the pre-DROP warning text,
# and ui/dependency_dialog.py's tabs — "group by type, only show
# categories that have results" (or, in the tab bar, show every category
# but disable the empty ones) all key off this same list.
CATEGORIES = (
    ("foreign_keys", "foreign_key", "Foreign Keys"),
    ("views", "view", "Views"),
    ("functions", "function", "Functions"),
    ("triggers", "trigger", "Triggers"),
    ("procedures", "procedure", "Procedures"),
)


@dataclass
class DependencyRef:
    kind: str          # "foreign_key" | "view" | "function" | "procedure" | "trigger"
    name: str          # source table name (FK) or view/routine/trigger name
    detail: str = ""   # FK: "table.col → table.col" join description. Others: the object's real definition text (for an on-demand "View Definition" popup).
    column: str = ""       # FK only: the referencing column
    ref_column: str = ""   # FK only: the referenced column


@dataclass
class DependencyReport:
    table: str
    column: str | None = None
    supported: bool = True
    foreign_keys: list = field(default_factory=list)   # list[DependencyRef]
    views: list = field(default_factory=list)           # list[DependencyRef]
    functions: list = field(default_factory=list)       # list[DependencyRef]
    procedures: list = field(default_factory=list)      # list[DependencyRef]
    triggers: list = field(default_factory=list)        # list[DependencyRef]

    def by_attr(self, attr: str) -> list:
        return getattr(self, attr)

    def is_empty(self) -> bool:
        return self.total() == 0

    def total(self) -> int:
        return sum(len(self.by_attr(attr)) for attr, _kind, _title in CATEGORIES)

    @property
    def target_label(self) -> str:
        return f"{self.table}.{self.column}" if self.column else self.table


_word_re_cache: dict = {}


def _word_re(word: str):
    pattern = _word_re_cache.get(word)
    if pattern is None:
        pattern = re.compile(r'(?<![A-Za-z0-9_])' + re.escape(word) + r'(?![A-Za-z0-9_])', re.IGNORECASE)
        _word_re_cache[word] = pattern
    return pattern


def _mentions(definition: str, table_name: str, column_name: str = None) -> bool:
    """True if *definition* (a real view/routine/trigger definition pulled
    from the catalog) appears to reference *table_name* (and
    *column_name*, when given) — whole-word, case-insensitive. A
    heuristic, not a SQL parser: can miss references hidden behind dynamic
    SQL, and a column name can false-positive if it coincidentally appears
    elsewhere in the text."""
    if not definition:
        return False
    if not _word_re(table_name).search(definition):
        return False
    if column_name and not _word_re(column_name).search(definition):
        return False
    return True


def find_table_dependents(db, table_name: str) -> DependencyReport:
    """Everything in the schema that references *table_name*: incoming
    foreign keys from other tables, plus views/functions/procedures/
    triggers whose definition mentions it."""
    return _find_dependents(db, table_name, None)


def find_column_dependents(db, table_name: str, column_name: str) -> DependencyReport:
    """Same as find_table_dependents(), narrowed to *column_name* — an FK
    only counts if it targets this exact column; a view/routine/trigger
    only counts if its definition mentions both the table and the
    column."""
    return _find_dependents(db, table_name, column_name)


def find_database_dependents(db) -> list:
    """One DependencyReport per table in the schema (issue #236's
    "Database" Impact Analysis option) — every table's FKs, views,
    functions, procedures, and triggers, from a single shared fetch of
    each bulk catalog dataset rather than N separate per-table
    round-trips. [] for an unsupported db_type or a schema with no
    tables."""
    if getattr(db, "db_type", None) not in SUPPORTED_DB_TYPES:
        return []
    try:
        tables = db.get_tables()
    except Exception:
        tables = []
    bulk = _fetch_bulk(db)
    return [_build_report(bulk, table_name, None) for table_name in tables]


def _fetch_bulk(db) -> dict:
    """The five whole-database catalog reads _build_report() matches
    against — fetched once and shared across every table when scanning the
    whole database, instead of once per table."""
    def _safe(getter):
        try:
            return getter()
        except Exception:
            return {}
    return {
        "foreign_keys": _safe(db.get_all_foreign_keys),
        "views": _safe(db.get_view_definitions),
        "functions": _safe(db.get_function_definitions),
        "procedures": _safe(db.get_procedure_definitions),
        "triggers": _safe(db.get_trigger_definitions),
    }


def _match_definitions(definitions: dict, kind: str, table_name: str, column_name: str | None) -> list:
    return [
        DependencyRef(kind=kind, name=name, detail=definition)
        for name, definition in definitions.items()
        if _mentions(definition, table_name, column_name)
    ]


def _build_report(bulk: dict, table_name: str, column_name: str | None) -> DependencyReport:
    """Build one table's DependencyReport by matching it against already-
    fetched bulk catalog data (see _fetch_bulk()) — no database access of
    its own, so it's cheap to call once per table in find_database_dependents()."""
    report = DependencyReport(table=table_name, column=column_name)

    for source_table, fks in bulk["foreign_keys"].items():
        for fk in fks:
            if fk.get("ref_table") != table_name:
                continue
            if column_name and fk.get("ref_column") != column_name:
                continue
            report.foreign_keys.append(DependencyRef(
                kind="foreign_key",
                name=source_table,
                detail=f"{source_table}.{fk.get('column')} → {table_name}.{fk.get('ref_column')}",
                column=fk.get("column", ""),
                ref_column=fk.get("ref_column", ""),
            ))

    report.views = _match_definitions(bulk["views"], "view", table_name, column_name)
    report.functions = _match_definitions(bulk["functions"], "function", table_name, column_name)
    report.procedures = _match_definitions(bulk["procedures"], "procedure", table_name, column_name)
    report.triggers = _match_definitions(bulk["triggers"], "trigger", table_name, column_name)

    return report


def _find_dependents(db, table_name: str, column_name: str | None) -> DependencyReport:
    if getattr(db, "db_type", None) not in SUPPORTED_DB_TYPES:
        report = DependencyReport(table=table_name, column=column_name)
        report.supported = False
        return report

    return _build_report(_fetch_bulk(db), table_name, column_name)
