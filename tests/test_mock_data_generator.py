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

    for dialect in ("mysql", "postgresql", "sqlite"):
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
