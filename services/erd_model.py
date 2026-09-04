"""Build a read-only ER-diagram graph model from database metadata (issue #63).

Independent of any UI rendering — see ui/erd_dialog.py for the QGraphicsView
renderer. Uses its own dedicated, throwaway DbService connection (same
pattern and rationale as services/schema_snapshot.py) rather than a
caller-supplied shared one, so it can safely run on a background thread
without corrupting a connection the main thread may be using concurrently.
"""
from dataclasses import dataclass, field

from services.db_service import DbService
from utils.logger import get_logger

logger = get_logger()


@dataclass
class ErdColumn:
    name: str
    data_type: str = ""
    is_primary_key: bool = False
    is_foreign_key: bool = False


@dataclass
class ErdTable:
    name: str
    columns: list = field(default_factory=list)  # list[ErdColumn]


@dataclass
class ErdRelationship:
    source_table: str
    source_column: str
    target_table: str
    target_column: str
    is_one_to_one: bool = False


@dataclass
class ErdGraph:
    tables: dict = field(default_factory=dict)          # name -> ErdTable
    relationships: list = field(default_factory=list)   # list[ErdRelationship]
    # How many tables the database actually has, before any max_tables cap
    # was applied — equal to len(tables) unless the diagram was truncated.
    total_tables_available: int = 0


def build_erd_graph(config: dict, table_names: list = None, max_tables: int = None) -> ErdGraph:
    """Connect using *config*, gather table/column/PK/FK metadata for
    *table_names* (every table when None), then disconnect. Never touches
    a caller-held live connection.

    *max_tables*, when given and the database has more tables than that,
    keeps only the first *max_tables* (alphabetically) — used to cap a Free
    ER diagram (services/entitlements.py Limit.ER_DIAGRAM_TABLES). Ignored
    when *table_names* is explicitly given (an already-deliberate subset,
    e.g. a single-table focus view). graph.total_tables_available always
    reports the true count so the caller can tell the user a diagram was
    truncated.

    A single table's metadata failing to load doesn't abort diagram
    generation — that table is just omitted (acceptance criterion:
    "Missing/incomplete metadata does not crash diagram generation").
    Relationships whose target table isn't part of the built graph (a
    dangling FK, the target was excluded from *table_names*, or dropped by
    *max_tables*) are dropped rather than left pointing at nothing.
    """
    db = DbService()
    db.connect(config)
    try:
        graph = ErdGraph()

        if table_names is not None:
            names = table_names
        else:
            try:
                names = db.get_tables()
            except Exception as ex:
                logger.debug(f"ERD: failed to list tables: {ex}")
                return graph
            graph.total_tables_available = len(names)
            if max_tables is not None and len(names) > max_tables:
                names = sorted(names)[:max_tables]

        if not graph.total_tables_available:
            graph.total_tables_available = len(names)

        # Bulk, single-round-trip metadata (same calls services/schema_snapshot.py
        # already uses for the schema browser/autocomplete) instead of a
        # get_columns()/get_primary_keys()/get_foreign_keys() loop per table —
        # that was up to 3 round trips PER TABLE, which is what made opening
        # a diagram feel like a hang on anything but a tiny schema.
        try:
            column_details = db.get_all_column_details()
        except Exception as ex:
            logger.debug(f"ERD: failed to read column details: {ex}")
            column_details = {}

        try:
            fks_by_table = db.get_all_foreign_keys()
        except Exception as ex:
            logger.debug(f"ERD: failed to read foreign keys: {ex}")
            fks_by_table = {}

        graph.tables, fk_by_table = _tables_from_metadata(names, column_details, fks_by_table)

        # Cardinality (1:1 vs 1:N) needs each FK source table's unique
        # indexes. Scoped to tables that actually have an outgoing FK (a
        # small fraction of a large schema) so this stays cheap enough to
        # run eagerly — cardinality marks render immediately, not on
        # demand, unlike the full per-table index list in
        # fetch_table_indexes() below.
        indexes_by_table = {}
        for source_table, fks in fk_by_table.items():
            if not fks:
                continue
            try:
                indexes_by_table[source_table] = db.get_indexes(source_table)
            except Exception as ex:
                logger.debug(f"ERD: failed to read indexes for {source_table}: {ex}")
                indexes_by_table[source_table] = []

        for source_table, fks in fk_by_table.items():
            for fk in fks:
                target_table = fk.get("ref_table")
                if target_table in graph.tables:
                    source_column = fk.get("column", "")
                    graph.relationships.append(ErdRelationship(
                        source_table=source_table,
                        source_column=source_column,
                        target_table=target_table,
                        target_column=fk.get("ref_column", ""),
                        is_one_to_one=_is_unique_single_column(
                            indexes_by_table.get(source_table, []), source_column),
                    ))

        return graph
    finally:
        db.disconnect()


def _tables_from_metadata(names: list, column_details: dict, fks_by_table: dict) -> tuple:
    """Shared by build_erd_graph and build_erd_graph_from_snapshot: turn
    {table: [{name, type, key}, ...]} column details + {table: [{column,
    ref_table, ref_column}, ...]} foreign keys into {table: ErdTable}.
    Returns (tables, fk_by_table) — the latter still keyed by *names* only,
    for the caller's cardinality/relationship pass.
    """
    tables = {}
    fk_by_table = {}
    for name in names:
        columns = column_details.get(name)
        if columns is None:
            logger.debug(f"ERD: no column details for {name}")
            continue

        fks = fks_by_table.get(name, [])
        fk_by_table[name] = fks
        fk_cols = {fk["column"] for fk in fks}

        table = ErdTable(name=name)
        for col in columns:
            table.columns.append(ErdColumn(
                name=col["name"],
                data_type=str(col.get("type", "") or ""),
                is_primary_key=col.get("key") == "PRI",
                is_foreign_key=col["name"] in fk_cols,
            ))
        tables[name] = table
    return tables, fk_by_table


def build_erd_graph_from_snapshot(snapshot: dict, max_tables: int = None) -> ErdGraph:
    """Build an ErdGraph from an already-fetched schema snapshot (the shape
    utils.schema_cache stores/loads: tables/column_details/foreign_keys) —
    no database round-trip at all. This is the instant-paint half of
    ErdDialog's cache-then-refresh (see ui/erd_dialog.py._reload), the same
    pattern ConnectionPanel._apply_cached_schema already uses for the schema
    browser: a connection/database the user already has open elsewhere
    reopens the diagram from disk instantly instead of re-querying metadata
    it just fetched.

    ponytail: relationships always render as one-to-many here — cardinality
    needs each source table's unique indexes, which aren't part of the
    cached snapshot. build_erd_graph()'s live refresh (always run right
    after this) redraws with the correct marks moments later; cache indexes
    too if that brief flicker ever becomes annoying.
    """
    graph = ErdGraph()
    names = list(snapshot.get("tables") or [])
    graph.total_tables_available = len(names)
    if max_tables is not None and len(names) > max_tables:
        names = sorted(names)[:max_tables]

    column_details = snapshot.get("column_details") or {}
    fks_by_table = snapshot.get("foreign_keys") or {}
    graph.tables, fk_by_table = _tables_from_metadata(names, column_details, fks_by_table)

    for source_table, fks in fk_by_table.items():
        for fk in fks:
            target_table = fk.get("ref_table")
            if target_table in graph.tables:
                graph.relationships.append(ErdRelationship(
                    source_table=source_table,
                    source_column=fk.get("column", ""),
                    target_table=target_table,
                    target_column=fk.get("ref_column", ""),
                    is_one_to_one=False,
                ))
    return graph


def _is_unique_single_column(indexes: list, column: str) -> bool:
    for idx in indexes:
        cols = [c.strip() for c in idx.get("columns", "").split(",") if c.strip()]
        if idx.get("unique") and cols == [column]:
            return True
    return False


def fetch_table_indexes(config: dict, table_name: str) -> list:
    """Short-lived dedicated connection to fetch *table_name*'s full index
    list on demand (used by the ERD inspector panel — see build_erd_graph's
    docstring for why a throwaway connection rather than a caller-supplied
    one)."""
    db = DbService()
    db.connect(config)
    try:
        return db.get_indexes(table_name)
    finally:
        db.disconnect()
