import sqlparse

from services import mock_data_generator as gen
from services.query_classifier import classify, split_statements


def _col(field, sql_type, nullable="YES"):
    return {"Field": field, "Type": sql_type, "Null": nullable, "Default": None}


def test_infer_generator_uses_column_name_heuristics():
    assert gen.infer_generator(_col("email", "varchar(255)")) == "email"
    assert gen.infer_generator(_col("phone_number", "varchar(20)")) == "phone"
    assert gen.infer_generator(_col("first_name", "varchar(50)")) == "first_name"
    assert gen.infer_generator(_col("last_name", "varchar(50)")) == "last_name"
    assert gen.infer_generator(_col("full_name", "varchar(100)")) == "full_name"
    assert gen.infer_generator(_col("is_active", "tinyint(1)")) == "boolean"
    assert gen.infer_generator(_col("created_at", "datetime")) == "datetime"
    assert gen.infer_generator(_col("birth_date", "date")) == "date"
    assert gen.infer_generator(_col("company_name", "varchar(100)")) == "company"
    assert gen.infer_generator(_col("job_title", "varchar(100)")) == "job"
    assert gen.infer_generator(_col("shipping_address", "varchar(255)")) == "address"
    assert gen.infer_generator(_col("city", "varchar(100)")) == "city"
    assert gen.infer_generator(_col("bio", "text")) == "lorem_text"


def test_infer_generator_suffix_wins_over_coincidental_content_word():
    """A "_at"/"_date" suffix (or a boolean prefix) describes what the
    column *is* and must win over a content word that merely appears
    somewhere in the name — regression for email_verified_at being
    generated as an email address instead of a timestamp."""
    assert gen.infer_generator(_col("email_verified_at", "timestamp")) == "datetime"
    assert gen.infer_generator(_col("phone_confirmed_at", "timestamp")) == "datetime"
    assert gen.infer_generator(_col("company_founded_date", "date")) == "date"
    assert gen.infer_generator(_col("has_company_email", "tinyint(1)")) == "boolean"
    # No structural suffix/prefix present — the content word still applies.
    assert gen.infer_generator(_col("email", "varchar(255)")) == "email"
    assert gen.infer_generator(_col("verified_email", "varchar(255)")) == "email"


def test_infer_generator_falls_back_to_type_bucket():
    assert gen.infer_generator(_col("count", "int(11)")) == "integer"
    assert gen.infer_generator(_col("price", "decimal(10,2)")) == "float"
    assert gen.infer_generator(_col("payload", "text")) == "string"


def test_infer_generator_lone_integer_pk_defaults_to_omit():
    assert gen.infer_generator(_col("id", "int(11)"), is_pk=True) == "omit"


def test_infer_generator_foreign_key_wins_over_everything_else():
    assert gen.infer_generator(_col("id", "int(11)"), is_pk=True, is_fk=True) == "foreign_key"
    assert gen.infer_generator(_col("email", "varchar(255)"), is_fk=True) == "foreign_key"


def test_faker_backed_generators_produce_nonempty_strings():
    columns = [
        _col("full_name", "varchar(100)"), _col("email", "varchar(255)"),
        _col("phone", "varchar(20)"), _col("address", "varchar(255)"),
        _col("city", "varchar(100)"), _col("company", "varchar(100)"),
        _col("job", "varchar(100)"), _col("bio", "text"),
    ]
    specs = {
        "full_name": gen.ColumnSpec(generator="full_name"),
        "email": gen.ColumnSpec(generator="email"),
        "phone": gen.ColumnSpec(generator="phone"),
        "address": gen.ColumnSpec(generator="address"),
        "city": gen.ColumnSpec(generator="city"),
        "company": gen.ColumnSpec(generator="company"),
        "job": gen.ColumnSpec(generator="job"),
        "bio": gen.ColumnSpec(generator="lorem_text"),
    }
    df = gen.generate_dataframe(columns, 5, specs)
    for column in df.columns:
        assert df[column].map(lambda v: isinstance(v, str) and len(v) > 0).all()
    assert "@" in df["email"].iloc[0]
    assert "\n" not in df["address"].iloc[0]


def test_generate_dataframe_respects_row_count_and_include():
    columns = [_col("id", "int(11)"), _col("name", "varchar(50)")]
    specs = {
        "id": gen.ColumnSpec(generator="omit", include=False),
        "name": gen.ColumnSpec(generator="full_name", include=True),
    }
    df = gen.generate_dataframe(columns, 5, specs)
    assert list(df.columns) == ["name"]
    assert len(df) == 5


def test_generate_dataframe_null_rate_zero_never_emits_null():
    columns = [_col("name", "varchar(50)", nullable="NO")]
    specs = {"name": gen.ColumnSpec(generator="full_name", include=True, null_rate=0.0)}
    df = gen.generate_dataframe(columns, 50, specs)
    assert df["name"].notna().all()


def test_generate_dataframe_foreign_key_only_uses_pool_values():
    columns = [_col("customer_id", "int(11)")]
    specs = {"customer_id": gen.ColumnSpec(generator="foreign_key", include=True)}
    pool = [1, 2, 3]
    df = gen.generate_dataframe(columns, 20, specs, fk_pools={"customer_id": pool})
    assert set(df["customer_id"].tolist()) <= set(pool)


def test_generate_dataframe_foreign_key_empty_pool_yields_null():
    columns = [_col("customer_id", "int(11)")]
    specs = {"customer_id": gen.ColumnSpec(generator="foreign_key", include=True)}
    df = gen.generate_dataframe(columns, 5, specs, fk_pools={"customer_id": []})
    assert df["customer_id"].isna().all()


def test_custom_pattern_seq_token_is_sequential_and_one_based():
    columns = [_col("code", "varchar(20)")]
    specs = {"code": gen.ColumnSpec(generator="custom_pattern", include=True,
                                     options={"pattern": "ITEM-{seq}"})}
    df = gen.generate_dataframe(columns, 3, specs)
    assert df["code"].tolist() == ["ITEM-1", "ITEM-2", "ITEM-3"]


def test_custom_pattern_ignores_unknown_tokens():
    columns = [_col("code", "varchar(20)")]
    specs = {"code": gen.ColumnSpec(generator="custom_pattern", include=True,
                                     options={"pattern": "{not_a_real_token}"})}
    df = gen.generate_dataframe(columns, 1, specs)
    assert df["code"].tolist() == ["{not_a_real_token}"]


def test_build_insert_sql_produces_valid_insert_statements_per_dialect():
    columns = [_col("id", "int(11)"), _col("name", "varchar(50)")]
    specs = {
        "id": gen.ColumnSpec(generator="integer", include=True, options={"min": 1, "max": 1}),
        "name": gen.ColumnSpec(generator="full_name", include=True),
    }
    df = gen.generate_dataframe(columns, 3, specs)

    for dialect in ("mysql", "postgresql"):
        sql = gen.build_insert_sql(df, "users", dialect=dialect)
        statements = split_statements(sql)
        assert len(statements) == 3
        for stmt in statements:
            classification = classify(stmt)
            assert classification.kind == "INSERT"
            assert sqlparse.parse(stmt)


def test_build_insert_sql_empty_dataframe_returns_empty_string():
    columns = [_col("id", "int(11)")]
    specs = {"id": gen.ColumnSpec(generator="omit", include=False)}
    df = gen.generate_dataframe(columns, 5, specs)
    assert gen.build_insert_sql(df, "users") == ""


# ─── Dependency chain (issue #215) ─────────────────────────────────────────

def _fk(column, ref_table, ref_column="id"):
    return {"column": column, "ref_table": ref_table, "ref_column": ref_column}


def test_build_dependency_chain_orders_parents_before_children():
    all_fks = {
        "orders": [_fk("customer_id", "customers")],
        "customers": [],
    }
    chain = gen.build_dependency_chain(all_fks, "orders")
    assert chain.tables == ["customers", "orders"]
    assert chain.external_edges == {"customers": set(), "orders": set()}
    assert not chain.truncated


def test_build_dependency_chain_diamond_shared_grandparent_appears_once():
    all_fks = {
        "line_items": [_fk("order_id", "orders"), _fk("product_id", "products")],
        "orders": [_fk("customer_id", "customers")],
        "products": [_fk("supplier_id", "customers")],  # shares "customers" as a common ancestor
        "customers": [],
    }
    chain = gen.build_dependency_chain(all_fks, "line_items")
    assert chain.tables.count("customers") == 1
    assert chain.tables.index("customers") < chain.tables.index("orders")
    assert chain.tables.index("customers") < chain.tables.index("products")
    assert chain.tables.index("orders") < chain.tables.index("line_items")
    assert chain.tables.index("products") < chain.tables.index("line_items")


def test_build_dependency_chain_self_reference_falls_back_to_external():
    all_fks = {"employees": [_fk("manager_id", "employees")]}
    chain = gen.build_dependency_chain(all_fks, "employees")
    assert chain.tables == ["employees"]
    assert chain.external_edges["employees"] == {"manager_id"}


def test_build_dependency_chain_breaks_genuine_cycle_deterministically():
    all_fks = {
        "a": [_fk("b_id", "b")],
        "b": [_fk("a_id", "a")],
    }
    chain = gen.build_dependency_chain(all_fks, "a")
    assert set(chain.tables) == {"a", "b"}
    # exactly one edge must have been demoted to external to break the cycle
    total_external = sum(len(cols) for cols in chain.external_edges.values())
    assert total_external == 1


def test_build_dependency_chain_truncates_at_max_tables():
    all_fks = {f"t{i}": [_fk("parent_id", f"t{i+1}")] for i in range(10)}
    all_fks["t10"] = []
    chain = gen.build_dependency_chain(all_fks, "t0", max_tables=3)
    assert len(chain.tables) <= 3
    assert chain.truncated


def test_build_dependency_chain_no_ancestors_returns_root_only():
    chain = gen.build_dependency_chain({"standalone": []}, "standalone")
    assert chain.tables == ["standalone"]
    assert not chain.truncated


# ─── Multi-table generation (issue #215) ───────────────────────────────────

def _plan(table, columns, pk=None, fks=None, row_count=0, pk_offset=None):
    return gen.TablePlan(
        table=table, columns=columns,
        primary_keys=[pk] if pk else [],
        foreign_keys=fks or [], generated_columns=[],
        row_count=row_count, pk_offset=pk_offset,
    )


def test_generate_chain_dataframes_child_pool_is_subset_of_parent_keys():
    all_fks = {"orders": [_fk("customer_id", "customers")], "customers": []}
    chain = gen.build_dependency_chain(all_fks, "orders")
    plans = {
        "customers": _plan("customers", [_col("id", "int(11)"), _col("name", "varchar(50)")],
                            pk="id", row_count=5, pk_offset=1),
        "orders": _plan("orders", [_col("id", "int(11)"), _col("customer_id", "int(11)")],
                         pk="id", fks=[_fk("customer_id", "customers")],
                         row_count=10, pk_offset=1),
    }
    dataframes = gen.generate_chain_dataframes(chain, plans, external_pool_fn=lambda t, c: [])
    assert len(dataframes["customers"]) == 5
    assert len(dataframes["orders"]) == 10
    parent_ids = set(dataframes["customers"]["id"].tolist())
    child_fk_values = set(dataframes["orders"]["customer_id"].dropna().tolist())
    assert child_fk_values <= parent_ids


def test_generate_chain_dataframes_reused_table_skipped_falls_back_to_external():
    all_fks = {"orders": [_fk("customer_id", "customers")], "customers": []}
    chain = gen.build_dependency_chain(all_fks, "orders")
    plans = {
        "customers": _plan("customers", [_col("id", "int(11)")], pk="id", row_count=0),  # reuse — has real data
        "orders": _plan("orders", [_col("id", "int(11)"), _col("customer_id", "int(11)")],
                         pk="id", fks=[_fk("customer_id", "customers")],
                         row_count=10, pk_offset=1),
    }
    calls = []

    def external_pool_fn(ref_table, ref_column):
        calls.append((ref_table, ref_column))
        return [101, 102]

    dataframes = gen.generate_chain_dataframes(chain, plans, external_pool_fn=external_pool_fn)
    assert "customers" not in dataframes
    assert calls == [("customers", "id")]
    assert set(dataframes["orders"]["customer_id"].tolist()) <= {101, 102}


# ─── Uniqueness constraint tracking (issue #210) ───────────────────────────

def test_generate_dataframe_dedupes_unique_column():
    columns = [_col("id", "int(11)"), _col("email", "varchar(255)")]
    specs = {
        "id": gen.ColumnSpec(generator="integer", options={"min": 1, "max": 5}),
        "email": gen.ColumnSpec(generator="string", options={"length": 3}),
    }
    df = gen.generate_dataframe(columns, 50, specs, unique_columns={"email"})
    assert df["email"].nunique() == len(df)


# ─── Categorical / weighted value-list generator (issue #213) ─────────────

def test_value_list_generator_only_produces_allowed_values():
    columns = [_col("status", "varchar(20)")]
    allowed = ["pending", "shipped", "delivered"]
    specs = {"status": gen.ColumnSpec(generator="value_list",
                                       options={"values": allowed, "weights": [1, 5, 1]})}
    df = gen.generate_dataframe(columns, 30, specs)
    assert set(df["status"].tolist()) <= set(allowed)


# ─── Enum / CHECK-constraint awareness (issue #211) ────────────────────────

def test_infer_generator_prefers_allowed_values_over_string_bucket():
    column = _col("status", "varchar(20)")
    generator = gen.infer_generator(column, allowed_values=["active", "inactive"])
    assert generator == "value_list"
