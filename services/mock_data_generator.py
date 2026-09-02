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
import random
import re
import string
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import pandas as pd
from faker import Faker

from utils.df_export import _to_sql_inserts

_fake = Faker()

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


def infer_generator(column: dict, is_pk: bool = False, is_fk: bool = False) -> str:
    """Default generator for a column, from its name and `get_columns()`
    type string. A foreign-key column always wins (referential integrity
    matters more than name/type guessing); a lone integer primary key
    defaults to "omit" — inserting an explicit value for what's almost
    always an auto-increment column is more likely to error than help."""
    if is_fk:
        return "foreign_key"
    bucket = _type_bucket(column.get("Type", ""))
    if is_pk and bucket == "integer":
        return "omit"

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


def _gen_address(spec: ColumnSpec, seq: int):
    # Faker's address() embeds a newline before city/state/zip; a mock SQL
    # value should stay on one line.
    return _fake.address().replace("\n", ", ")


def _gen_lorem_text(spec: ColumnSpec, seq: int):
    return _fake.sentence()


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
    "address": _gen_address,
    "city": lambda spec, seq: _fake.city(),
    "company": lambda spec, seq: _fake.company(),
    "job": lambda spec, seq: _fake.job(),
    "lorem_text": _gen_lorem_text,
    "null": lambda spec, seq: None,
}


def _generate_value(spec: ColumnSpec, seq: int, pool: list):
    if spec.generator == "foreign_key":
        return random.choice(pool) if pool else None  # nosec B311 -- mock/sample data, not security-sensitive
    if spec.generator == "custom_pattern":
        return _render_pattern(spec.options.get("pattern", "{seq}"), seq)
    fn = _SIMPLE_GENERATORS.get(spec.generator)
    return fn(spec, seq) if fn else None


def generate_dataframe(columns: list[dict], row_count: int,
                        specs: dict[str, ColumnSpec],
                        fk_pools: dict[str, list] | None = None) -> pd.DataFrame:
    """Build a synthetic DataFrame for *columns* (as returned by
    `DbService.get_columns`). Only columns whose spec has `include=True`
    are produced — callers exclude generated columns and "omit"-generator
    columns (e.g. auto-increment PKs) by never including them in *specs*
    with `include=True`. `fk_pools[column]` should hold real values sampled
    from the referenced table/column (a read, so this works even on
    read-only connections) for any column using the "foreign_key"
    generator; an empty pool yields NULL."""
    fk_pools = fk_pools or {}
    active = [c["Field"] for c in columns if specs.get(c["Field"], ColumnSpec("omit", include=False)).include]

    data: dict[str, list] = {name: [] for name in active}
    for seq in range(row_count):
        for name in active:
            spec = specs[name]
            if spec.generator != "null" and spec.null_rate > 0 and random.random() < spec.null_rate:  # nosec B311 -- mock/sample data, not security-sensitive
                data[name].append(None)
                continue
            data[name].append(_generate_value(spec, seq, fk_pools.get(name, [])))
    return pd.DataFrame(data, columns=active)


def build_insert_sql(df: pd.DataFrame, table_name: str, dialect: str = "mysql",
                      batch_kib: float | None = None) -> str:
    if df.empty:
        return ""
    return _to_sql_inserts(df, table_name, dialect=dialect, batch_kib=batch_kib)
