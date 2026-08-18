"""Graphviz `digraph` schema/FK diagram export (issue #159) — matches
Sequel Ace's Dot export: nodes are tables (with their columns), edges are
foreign keys. Not a row-data export, so it doesn't go through
db_service.stream_table_rows()/SqlInsertStreamWriter at all."""


def _escape(text: str) -> str:
    return str(text).replace('"', '\\"')


def _column_names(db_service, table_name: str) -> list[str]:
    return [str(c.get("Field", c.get("name", ""))) for c in db_service.get_columns(table_name)]


def build_dot_graph(db_service, tables: list[str]) -> str:
    """A Graphviz digraph of *tables*' columns and foreign keys. FKs
    pointing at a table outside *tables* are skipped rather than pulling in
    nodes the caller didn't select."""
    table_set = set(tables)
    lines = ["digraph schema {", "  rankdir=LR;", "  node [shape=record];"]

    for table in tables:
        cols = _column_names(db_service, table)
        cols_label = "".join(f"{_escape(c)}\\l" for c in cols)
        lines.append(f'  "{_escape(table)}" [label="{{{_escape(table)}|{cols_label}}}"];')

    for table in tables:
        for fk in db_service.get_foreign_keys(table):
            if fk["ref_table"] not in table_set:
                continue
            lines.append(
                f'  "{_escape(table)}" -> "{_escape(fk["ref_table"])}" '
                f'[label="{_escape(fk["column"])} -> {_escape(fk["ref_column"])}"];'
            )

    lines.append("}")
    return "\n".join(lines)
