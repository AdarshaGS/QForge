"""Synthetic INSERT data generation from schema metadata (issue #77).

Pure Python — no Qt, no DB access, no external services. `ui/mock_data_dialog.py`
is the UI layer on top of this; `ui/connection_panel.py` owns the
environment/read-only safety gate (`ui/query_guard_dialog.py`'s
`mock_data_generation_allowed`) and the actual execution.

Reuses `utils/df_export.py`'s `_to_sql_inserts` for the DataFrame -> INSERT
SQL step rather than reimplementing dialect quoting/escaping — that module
is already the single source of truth for it.

Name/address/company/etc. values come from the `faker` package — still
fully offline (no network calls, no external service), just a much larger
and more realistic pool of values than a hand-rolled word list. Numeric,
UUID, and custom-pattern generation stays on the stdlib (`random`/`uuid`)
since Faker offers nothing more "real" there.
"""
import json
import random
import re
import string
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable

import pandas as pd
from faker import Faker

from utils.df_export import _to_sql_inserts

_fake = Faker()

# Curated subset of Faker's supported locales (issue #218) — not every one
# it ships, just a reasonable spread of what a real connected database's
# audience is likely to be. (display name, Faker locale code).
SUPPORTED_LOCALES = [
    ("English (US)", "en_US"),
    ("English (UK)", "en_GB"),
    ("English (India)", "en_IN"),
    ("English (Australia)", "en_AU"),
    ("English (Canada)", "en_CA"),
    ("German", "de_DE"),
    ("French", "fr_FR"),
    ("Spanish (Spain)", "es_ES"),
    ("Spanish (Mexico)", "es_MX"),
    ("Italian", "it_IT"),
    ("Portuguese (Brazil)", "pt_BR"),
    ("Portuguese (Portugal)", "pt_PT"),
    ("Dutch", "nl_NL"),
    ("Japanese", "ja_JP"),
    ("Chinese (Simplified)", "zh_CN"),
    ("Korean", "ko_KR"),
    ("Russian", "ru_RU"),
    ("Polish", "pl_PL"),
]


def configure(locale: str | None = None, seed: int | None = None) -> None:
    """(Re)configure the module's shared Faker instance (issues #216, #218).
    *locale*=None keeps the default English (US) provider; an unsupported
    locale string falls back to it too rather than raising. *seed*=None
    leaves the random state alone — both are opt-in, so existing "just
    generate something plausible" behavior is unchanged unless a caller
    actually asks for a seed/locale. Faker keeps its own `Random` instance
    separate from the stdlib `random` module, so reproducibility needs
    both seeded — global `random.seed` alone isn't enough."""
    global _fake
    try:
        _fake = Faker(locale) if locale else Faker()
    except Exception:
        _fake = Faker()
    if seed is not None:
        random.seed(seed)
        _fake.seed_instance(seed)


GENERATOR_LABELS = {
    "integer": "Integer",
    "float": "Float",
    "string": "Random String",
    "uuid": "UUID",
    "boolean": "Boolean",
    "date": "Date",
    "datetime": "Date & Time",
    "email": "Email",
    "first_name": "First Name",
    "last_name": "Last Name",
    "full_name": "Full Name",
    "phone": "Phone Number",
    "address": "Street Address",
    "city": "City",
    "company": "Company Name",
    "job": "Job Title",
    "lorem_text": "Lorem Text",
    "null": "Always NULL",
    "foreign_key": "Foreign Key Reference",
    "custom_pattern": "Custom Pattern",
    "value_list": "Value List (Weighted)",
    "json_object": "JSON Object",
    "array": "Array",
    "geometry": "Geometry (Point)",
    "omit": "Omit (let database assign)",
}
GENERATORS = tuple(GENERATOR_LABELS.keys())

_TOKEN_RE = re.compile(r"\{(seq|seq0|uuid|random_int)(?::(-?\d+)-(-?\d+))?\}")


def _type_bucket(sql_type: str) -> str:
    """Normalize a raw `get_columns()` `Type` string (MySQL/Postgres
    spellings differ) into a small set of buckets `infer_generator` and
    the numeric/date generators key off. Order matters: datetime/timestamp
    must be checked before the bare "date" prefix check."""
    t = (sql_type or "").lower()
    if "uuid" in t:
        return "uuid"
    if t.startswith("tinyint(1)") or t in ("bool", "boolean"):
        return "boolean"
    if "int" in t or "serial" in t:
        return "integer"
    if any(k in t for k in ("float", "double", "decimal", "numeric", "real")):
        return "float"
    if "timestamp" in t or "datetime" in t:
        return "datetime"
    if t.startswith("date"):
        return "date"
    return "string"


def infer_generator(column: dict, is_pk: bool = False, is_fk: bool = False,
                     allowed_values: list | None = None) -> str:
    """Default generator for a column, from its name and `get_columns()`
    type string. A foreign-key column always wins (referential integrity
    matters more than name/type guessing); a lone integer primary key
    defaults to "omit" — inserting an explicit value for what's almost
    always an auto-increment column is more likely to error than help.
    *allowed_values* (issue #211) — a real ENUM or simple `IN (...)` CHECK
    value set introspected for this column — wins over every name/type
    guess below it: picking from the schema's own allowed set is always
    more correct than a heuristic."""
    if is_fk:
        return "foreign_key"
    bucket = _type_bucket(column.get("Type", ""))
    if is_pk and bucket == "integer":
        return "omit"
    if allowed_values:
        return "value_list"

    # Postgres jsonb/array/geometry and MySQL/Postgres json (issue #214) —
    # "Type" alone can't distinguish these ("ARRAY"/"USER-DEFINED" are
    # generic information_schema labels), so column also carries `udt_name`
    # (see db_service._fetch_columns) for the real element/type name.
    type_lower = (column.get("Type") or "").lower()
    if type_lower.startswith("json"):
        return "json_object"
    if type_lower == "array":
        return "array"
    if type_lower == "user-defined" and (column.get("udt_name") or "") in ("geometry", "geography"):
        return "geometry"

    name = (column.get("Field") or "").lower()

    # Structural name/type signals (boolean prefixes, "_at"/"_date" suffixes,
    # the DB type itself) are checked before any content-word match below —
    # a suffix like "_at" describes what the column *is* and should win over
    # a coincidental substring match elsewhere in the name (issue: a column
    # like "email_verified_at" was matching the "email" content check before
    # ever reaching the "_at" -> datetime check, producing an email address
    # in a timestamp column).
    if name.startswith("is_") or name.startswith("has_") or name.endswith("_flag") or bucket == "boolean":
        return "boolean"
    if name.endswith("_at") or "timestamp" in name or bucket == "datetime":
        return "datetime"
    if name.endswith("_date") or name == "date" or bucket == "date":
        return "date"

    if "email" in name:
        return "email"
    if "phone" in name or "mobile" in name:
        return "phone"
    if "uuid" in name or "guid" in name or bucket == "uuid":
        return "uuid"
    if name in ("first_name", "fname", "given_name"):
        return "first_name"
    if name in ("last_name", "lname", "surname", "family_name"):
        return "last_name"
    if "company" in name or "employer" in name or "organization" in name:
        return "company"
    if "job" in name or name in ("title", "position", "occupation"):
        return "job"
    if "address" in name or "street" in name:
        return "address"
    if "city" in name or "town" in name:
        return "city"
    if "name" in name:
        return "full_name"
    if any(k in name for k in ("bio", "description", "notes", "comment", "summary")) and bucket == "string":
        return "lorem_text"

    if bucket == "integer":
        return "integer"
    if bucket == "float":
        return "float"
    return "string"


@dataclass
class ColumnSpec:
    generator: str
    include: bool = True
    null_rate: float = 0.0
    options: dict = field(default_factory=dict)


def _render_pattern(pattern: str, seq: int) -> str:
    """Whitelisted token substitution for the "custom_pattern" generator —
    deliberately not `str.format`/`eval` against user input (this app has
    already had one sandbox-escapable `eval()` finding — see ai/flush-
    context.md #161-163 — custom patterns are exactly the kind of
    DB-adjacent, user-authored text that class of bug comes from)."""
    def repl(m: re.Match) -> str:
        token, lo, hi = m.group(1), m.group(2), m.group(3)
        if token == "seq":  # nosec B105 -- token name, not a secret
            return str(seq + 1)
        if token == "seq0":  # nosec B105 -- token name, not a secret
            return str(seq)
        if token == "uuid":  # nosec B105 -- token name, not a secret
            return str(uuid.uuid4())
        if token == "random_int":  # nosec B105 -- token name, not a secret
            lo_v = int(lo) if lo is not None else 1
            hi_v = int(hi) if hi is not None else 1000
            return str(random.randint(min(lo_v, hi_v), max(lo_v, hi_v)))  # nosec B311 -- mock/sample data, not security-sensitive
        return m.group(0)
    return _TOKEN_RE.sub(repl, pattern)


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return date.today()


def _gen_integer(spec: ColumnSpec, seq: int):
    lo = spec.options.get("min", 1)
    hi = spec.options.get("max", 100_000)
    return random.randint(min(lo, hi), max(lo, hi))  # nosec B311 -- mock/sample data, not security-sensitive


def _gen_float(spec: ColumnSpec, seq: int):
    lo = spec.options.get("min", 0.0)
    hi = spec.options.get("max", 1000.0)
    decimals = spec.options.get("decimals", 2)
    return round(random.uniform(min(lo, hi), max(lo, hi)), decimals)  # nosec B311 -- mock/sample data, not security-sensitive


def _gen_string(spec: ColumnSpec, seq: int):
    length = spec.options.get("length", 10)
    alphabet = string.ascii_lowercase + string.digits
    return "".join(random.choices(alphabet, k=max(length, 1)))  # nosec B311 -- mock/sample data, not security-sensitive


def _gen_uuid(spec: ColumnSpec, seq: int):
    return str(uuid.uuid4())


def _gen_boolean(spec: ColumnSpec, seq: int):
    return random.choice([True, False])  # nosec B311 -- mock/sample data, not security-sensitive


def _gen_date(spec: ColumnSpec, seq: int):
    start = _parse_date(spec.options.get("start", "2020-01-01"))
    end = _parse_date(spec.options.get("end", date.today().isoformat()))
    delta = max((end - start).days, 0)
    d = start + timedelta(days=random.randint(0, delta))  # nosec B311 -- mock/sample data, not security-sensitive
    return d.isoformat()  # returned as str so _sql_value_literal quotes it


def _gen_datetime(spec: ColumnSpec, seq: int):
    start = _parse_date(spec.options.get("start", "2020-01-01"))
    end = _parse_date(spec.options.get("end", date.today().isoformat()))
    delta = max((end - start).days, 0)
    d = start + timedelta(days=random.randint(0, delta))  # nosec B311 -- mock/sample data, not security-sensitive
    dt = datetime.combine(d, datetime.min.time()) + timedelta(seconds=random.randint(0, 86_399))  # nosec B311 -- mock/sample data, not security-sensitive
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _gen_email(spec: ColumnSpec, seq: int):
    return _fake.email()


def _gen_phone(spec: ColumnSpec, seq: int):
    return _fake.phone_number()


def _gen_address(spec: ColumnSpec, seq: int, context: dict | None = None):
    """Issue #212: when this table also has a "city"-generator column,
    *context["city"]* carries this row's already-generated city so the two
    columns agree, built from street_address() + that city rather than
    Faker's own address() (which bundles its own independently-random
    city and can't be reliably parsed back apart across locales).
    Faker's address() embeds a newline before city/state/zip when there's
    no shared city to correlate with; a mock SQL value should stay on one
    line either way."""
    if context and "city" in context:
        return f"{_fake.street_address()}, {context['city']}"
    return _fake.address().replace("\n", ", ")


def _gen_city(spec: ColumnSpec, seq: int, context: dict | None = None):
    if context and "city" in context:
        return context["city"]
    return _fake.city()


def _gen_lorem_text(spec: ColumnSpec, seq: int):
    return _fake.sentence()


def array_element_bucket(column: dict) -> str:
    """Type bucket for a Postgres array column's *element* type (issue
    #214), from `udt_name` (e.g. "_int4" -> "int4", "_text" -> "text") —
    callers pass this into a "array"-generator ColumnSpec's
    `options["element_bucket"]` right after `infer_generator` returns
    "array", since infer_generator itself only returns the generator id."""
    return _type_bucket((column.get("udt_name") or "").lstrip("_") or "text")


def _gen_json_object(spec: ColumnSpec, seq: int):
    """A small, realistic-shaped JSON object (issue #214) — not an attempt
    to model any real schema, just plausible-looking jsonb/json content."""
    return json.dumps({
        "id": seq + 1,
        "name": _fake.word(),
        "value": random.randint(1, 100),  # nosec B311 -- mock/sample data, not security-sensitive
    })


_ARRAY_ELEMENT_GENERATORS = {
    "integer": _gen_integer, "float": _gen_float, "string": _gen_string,
    "uuid": _gen_uuid, "boolean": _gen_boolean, "date": _gen_date, "datetime": _gen_datetime,
}


def _array_literal_item(value) -> str:
    if isinstance(value, bool):
        return "t" if value else "f"
    if isinstance(value, (int, float)):
        return str(value)
    return '"' + str(value).replace('"', '\\"') + '"'


def _gen_array(spec: ColumnSpec, seq: int):
    """Postgres array literal text (issue #214), e.g. "{1,2,3}" or
    '{"a","b"}' — a plain string, so it flows through _sql_value_literal's
    existing string-quoting path with no dedicated array support needed
    there. Element type comes from `options["element_bucket"]`
    (array_element_bucket()), falling back to "string" if unset (e.g. the
    user manually picked "array" for a column that wasn't inferred as
    one)."""
    bucket = spec.options.get("element_bucket", "string")
    elem_fn = _ARRAY_ELEMENT_GENERATORS.get(bucket, _gen_string)
    count = random.randint(2, 5)  # nosec B311 -- mock/sample data, not security-sensitive
    items = [elem_fn(ColumnSpec(generator=bucket, options=spec.options), seq) for _ in range(count)]
    return "{" + ",".join(_array_literal_item(v) for v in items) + "}"


def _gen_geometry(spec: ColumnSpec, seq: int):
    """Basic PostGIS point as WKT text (issue #214) — Postgres/PostGIS
    accept a plain quoted WKT string for a geometry/geography column via
    an assignment cast, so no dedicated SQL-literal handling is needed."""
    lon = round(random.uniform(-180, 180), 6)  # nosec B311 -- mock/sample data, not security-sensitive
    lat = round(random.uniform(-90, 90), 6)  # nosec B311 -- mock/sample data, not security-sensitive
    return f"POINT({lon} {lat})"


def _gen_value_list(spec: ColumnSpec, seq: int):
    """Categorical picker (issue #213) — a status/type-shaped column with a
    user-entered value list, and optional parallel weights via
    `options["weights"]`. Falls back to uniform choice when weights are
    absent or don't line up 1:1 with values (a malformed weights list
    shouldn't crash generation, just lose the weighting)."""
    values = spec.options.get("values") or []
    if not values:
        return None
    weights = spec.options.get("weights")
    if weights and len(weights) == len(values):
        return random.choices(values, weights=weights, k=1)[0]  # nosec B311 -- mock/sample data, not security-sensitive
    return random.choice(values)  # nosec B311 -- mock/sample data, not security-sensitive


_SIMPLE_GENERATORS = {
    "integer": _gen_integer,
    "float": _gen_float,
    "string": _gen_string,
    "uuid": _gen_uuid,
    "boolean": _gen_boolean,
    "date": _gen_date,
    "datetime": _gen_datetime,
    "email": _gen_email,
    "first_name": lambda spec, seq: _fake.first_name(),
    "last_name": lambda spec, seq: _fake.last_name(),
    "full_name": lambda spec, seq: _fake.name(),
    "phone": _gen_phone,
    "company": lambda spec, seq: _fake.company(),
    "job": lambda spec, seq: _fake.job(),
    "lorem_text": _gen_lorem_text,
    "null": lambda spec, seq: None,
    "value_list": _gen_value_list,
    "json_object": _gen_json_object,
    "array": _gen_array,
    "geometry": _gen_geometry,
}


def _generate_value(spec: ColumnSpec, seq: int, pool: list, context: dict | None = None):
    if spec.generator == "foreign_key":
        return random.choice(pool) if pool else None  # nosec B311 -- mock/sample data, not security-sensitive
    if spec.generator == "custom_pattern":
        return _render_pattern(spec.options.get("pattern", "{seq}"), seq)
    if spec.generator == "address":
        return _gen_address(spec, seq, context)
    if spec.generator == "city":
        return _gen_city(spec, seq, context)
    fn = _SIMPLE_GENERATORS.get(spec.generator)
    return fn(spec, seq) if fn else None


# Generators drawn from a fixed/constrained pool (a live FK sample, an
# enum/CHECK/value-list, a coin flip) can't be "disambiguated" by mutating
# the value without breaking the constraint that pool represents — a
# UNIQUE + FK column just accepts an eventual duplicate once the pool is
# exhausted, same as a UNIQUE + boolean column always would.
_UNDISAMBIGUATABLE_GENERATORS = {"foreign_key", "value_list", "boolean", "null"}
_UNIQUE_RETRY_ATTEMPTS = 20


def _dedupe_unique(spec: ColumnSpec, seq: int, pool: list, seen: set, value, context: dict | None = None):
    """Re-roll *value* against *seen* (issue #210) for a UNIQUE-constrained
    column, then fall back to a deterministic disambiguation for
    freeform generators (append "-{seq}" to a string, offset a number by
    {seq}) — but only for generators where mutating the value can't
    violate some other constraint the value is drawn from; see
    _UNDISAMBIGUATABLE_GENERATORS."""
    if value is None or value not in seen:
        return value
    for _ in range(_UNIQUE_RETRY_ATTEMPTS):
        value = _generate_value(spec, seq, pool, context)
        if value is None or value not in seen:
            return value
    if spec.generator in _UNDISAMBIGUATABLE_GENERATORS:
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        bumped = value + seq + 1
        while bumped in seen:
            bumped += 1
        return bumped
    disambiguated = f"{value}-{seq}"
    while disambiguated in seen:
        disambiguated = f"{disambiguated}-{seq}"
    return disambiguated


def generate_dataframe(columns: list[dict], row_count: int,
                        specs: dict[str, ColumnSpec],
                        fk_pools: dict[str, list] | None = None,
                        unique_columns: set | None = None) -> pd.DataFrame:
    """Build a synthetic DataFrame for *columns* (as returned by
    `DbService.get_columns`). Only columns whose spec has `include=True`
    are produced — callers exclude generated columns and "omit"-generator
    columns (e.g. auto-increment PKs) by never including them in *specs*
    with `include=True`. `fk_pools[column]` should hold real values sampled
    from the referenced table/column (a read, so this works even on
    read-only connections) for any column using the "foreign_key"
    generator; an empty pool yields NULL. `unique_columns` (issue #210) —
    names of single-column UNIQUE-constrained columns — get dedup-tracked
    across the whole run so a nontrivial row count doesn't produce a
    UNIQUE-violating INSERT; see _dedupe_unique."""
    fk_pools = fk_pools or {}
    active = [c["Field"] for c in columns if specs.get(c["Field"], ColumnSpec("omit", include=False)).include]
    seen: dict[str, set] = {name: set() for name in active if name in (unique_columns or ())}
    # Issue #212: an "address" column and a "city" column on the same table
    # get correlated via one shared per-row city rather than two
    # independently-random Faker calls.
    correlate_city = (any(specs[n].generator == "address" for n in active)
                       and any(specs[n].generator == "city" for n in active))

    data: dict[str, list] = {name: [] for name in active}
    for seq in range(row_count):
        context = {"city": _fake.city()} if correlate_city else None
        for name in active:
            spec = specs[name]
            if spec.generator != "null" and spec.null_rate > 0 and random.random() < spec.null_rate:  # nosec B311 -- mock/sample data, not security-sensitive
                data[name].append(None)
                continue
            pool = fk_pools.get(name, [])
            value = _generate_value(spec, seq, pool, context)
            if name in seen:
                value = _dedupe_unique(spec, seq, pool, seen[name], value, context)
                if value is not None:
                    seen[name].add(value)
            data[name].append(value)
    return pd.DataFrame(data, columns=active)


def build_insert_sql(df: pd.DataFrame, table_name: str, dialect: str = "mysql",
                      batch_kib: float | None = None) -> str:
    if df.empty:
        return ""
    return _to_sql_inserts(df, table_name, dialect=dialect, batch_kib=batch_kib)


# ===========================================================================
# Dependency-ordered multi-table generation (issue #215)
# ===========================================================================
# Still pure Python — the caller (ui/connection_panel.py) does every DB read
# (bulk FK map, per-table schema, existing row counts, PK offsets) up front
# and hands it in already-fetched; this module only ever reasons about the
# in-memory shape of that data. `external_pool_fn` is deliberately the same
# two-arg (ref_table, ref_column) -> list[value] shape as the existing
# ConnectionPanel._sample_fk_values, so a caller can pass that method straight
# through with no wrapping.

@dataclass
class DependencyChain:
    tables: list                # ancestors-first, root last
    external_edges: dict = field(default_factory=dict)  # table -> set of its own FK column names that must fall back to live sampling (self-ref or a broken cycle)
    truncated: bool = False     # hit max_tables during discovery


def build_dependency_chain(all_fks: dict, root: str, max_tables: int = 25) -> DependencyChain:
    """Walk *all_fks* (the shape `DbService.get_all_foreign_keys()` already
    returns: {table: [{"column","ref_table","ref_column"}, ...]}) from
    *root* to find every ancestor table it (transitively) depends on via
    FK, then order them parents-first.

    A self-referencing FK (ref_table == table) always falls back to live
    sampling — it can never be resolved purely by ordering. A genuine
    multi-table cycle is broken deterministically (the edge whose
    dependent table name sorts last loses) rather than raising, so this
    always returns a usable order."""
    # ---- BFS discovery -----------------------------------------------------
    discovered = {root}
    frontier = [root]
    while frontier and len(discovered) < max_tables:
        table = frontier.pop(0)
        for fk in all_fks.get(table, []):
            ref = fk.get("ref_table")
            if not ref or ref == table or ref in discovered:
                continue
            if len(discovered) >= max_tables:
                break
            discovered.add(ref)
            frontier.append(ref)
    truncated = bool(frontier) and len(discovered) >= max_tables

    # ---- Classify edges: internal (both ends discovered) vs external ------
    external_edges: dict = {t: set() for t in discovered}
    edges = []  # (dependent_table, ref_table, column) — ref_table must precede dependent_table
    for table in discovered:
        for fk in all_fks.get(table, []):
            ref, column = fk.get("ref_table"), fk.get("column")
            if not ref or not column:
                continue
            if ref == table or ref not in discovered:
                external_edges[table].add(column)
            else:
                edges.append((table, ref, column))

    # ---- Kahn's algorithm, breaking any remaining cycle deterministically -
    remaining = set(discovered)
    indegree = {t: 0 for t in discovered}
    for dependent, ref, _col in edges:
        indegree[dependent] += 1

    ordered = []
    ready = sorted(t for t in remaining if indegree[t] == 0)
    live_edges = list(edges)
    while remaining:
        if not ready:
            # Genuine cycle among what's left — drop the edge whose
            # dependent table name sorts last, demote that column to
            # external sampling, and retry.
            cyclic_edges = [e for e in live_edges if e[0] in remaining and e[1] in remaining]
            dependent, ref, column = max(cyclic_edges, key=lambda e: e[0])
            live_edges.remove((dependent, ref, column))
            external_edges[dependent].add(column)
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready = [dependent]
            continue
        table = ready.pop(0)
        ordered.append(table)
        remaining.discard(table)
        for dependent, ref, _col in live_edges:
            if ref == table and dependent in remaining:
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    ready.append(dependent)
        ready.sort()

    return DependencyChain(tables=ordered, external_edges=external_edges, truncated=truncated)


@dataclass
class TablePlan:
    table: str
    columns: list
    primary_keys: list
    foreign_keys: list
    generated_columns: list = field(default_factory=list)
    row_count: int = 0          # 0 means "reuse existing rows, don't generate"
    pk_offset: int = None       # MAX(existing pk) + 1, pre-fetched by the caller
    unique_columns: set = field(default_factory=set)  # single-column UNIQUE indexes (issue #210)
    enum_values: dict = field(default_factory=dict)   # column -> real ENUM/CHECK value set (issue #211)


def _internal_target_columns(chain: DependencyChain, plans: dict) -> dict:
    """For every table in the chain, which of its own columns are the
    *target* of another (generated) table's internal FK — i.e. must have
    an explicit, known-in-memory value rather than being "omit"-ted."""
    targets: dict = {t: set() for t in chain.tables}
    for table in chain.tables:
        plan = plans.get(table)
        if not plan or plan.row_count <= 0:
            continue
        ext_cols = chain.external_edges.get(table, set())
        for fk in plan.foreign_keys:
            if fk.get("column") in ext_cols:
                continue
            ref = fk.get("ref_table")
            if ref in targets:
                targets[ref].add(fk.get("ref_column"))
    return targets


def generate_chain_dataframes(chain: DependencyChain, plans: dict,
                               external_pool_fn: Callable) -> dict:
    """Build one DataFrame per table in *chain.tables* that has
    `plans[table].row_count > 0`, ancestors first, wiring each parent's
    freshly-generated (not-yet-inserted) key values forward as the pool
    for any child FK column that targets it. A table with `row_count == 0`
    is skipped entirely (its dependents fall back to `external_pool_fn`,
    exactly today's `_sample_fk_values`-style live sampling)."""
    internal_targets = _internal_target_columns(chain, plans)
    dataframes: dict = {}

    for table in chain.tables:
        plan = plans.get(table)
        if not plan or plan.row_count <= 0:
            continue

        lone_pk = plan.primary_keys[0] if len(plan.primary_keys) == 1 else None
        fk_map = {fk["column"]: fk for fk in plan.foreign_keys}
        ext_cols = chain.external_edges.get(table, set())
        generated_set = set(plan.generated_columns)
        my_targets = internal_targets.get(table, set())

        specs: dict = {}
        for col in plan.columns:
            name = col["Field"]
            if name in generated_set:
                continue
            is_pk = name == lone_pk
            is_fk = name in fk_map
            allowed_values = plan.enum_values.get(name)
            generator = infer_generator(col, is_pk=is_pk, is_fk=is_fk, allowed_values=allowed_values)
            include = generator != "omit"
            if generator == "value_list" and allowed_values:
                options = {"values": allowed_values}
            elif generator == "array":
                options = {"element_bucket": array_element_bucket(col)}
            else:
                options = {}
            if name in my_targets and generator == "omit":
                # A lone integer PK some descendant needs to reference —
                # force it into memory instead of leaving it to the DB.
                offset = plan.pk_offset if plan.pk_offset is not None else 1
                generator, include = "integer", True
                options = {"min": offset, "max": max(offset, offset + plan.row_count - 1)}
            specs[name] = ColumnSpec(generator=generator, include=include, options=options)

        fk_pools: dict = {}
        for name, fk in fk_map.items():
            spec = specs.get(name)
            if not spec or not spec.include or spec.generator != "foreign_key":
                continue
            ref_table, ref_column = fk["ref_table"], fk["ref_column"]
            if name in ext_cols:
                fk_pools[name] = external_pool_fn(ref_table, ref_column)
                continue
            parent_df = dataframes.get(ref_table)
            if parent_df is not None and ref_column in parent_df.columns:
                fk_pools[name] = parent_df[ref_column].tolist()
            else:
                # Parent was a "reuse" table (never generated) — sample it live.
                fk_pools[name] = external_pool_fn(ref_table, ref_column)

        dataframes[table] = generate_dataframe(plan.columns, plan.row_count, specs, fk_pools,
                                                unique_columns=plan.unique_columns)

    return dataframes
