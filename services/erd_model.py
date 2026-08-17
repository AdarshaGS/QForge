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

        fk_by_table = {}
        for name in names:
            try:
                columns = db.get_columns(name)
            except Exception as ex:
                logger.debug(f"ERD: failed to read columns for {name}: {ex}")
                continue

            try:
                pk_cols = set(db.get_primary_keys(name))
            except Exception as ex:
                logger.debug(f"ERD: failed to read primary keys for {name}: {ex}")
                pk_cols = set()

            try:
                fks = db.get_foreign_keys(name)
            except Exception as ex:
                logger.debug(f"ERD: failed to read foreign keys for {name}: {ex}")
                fks = []
            fk_by_table[name] = fks
            fk_cols = {fk["column"] for fk in fks}

            table = ErdTable(name=name)
            for col in columns:
                col_name = col.get("Field", "")
                table.columns.append(ErdColumn(
                    name=col_name,
                    data_type=str(col.get("Type", "") or ""),
                    is_primary_key=col_name in pk_cols,
                    is_foreign_key=col_name in fk_cols,
                ))
            graph.tables[name] = table

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
