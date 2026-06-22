"""
QueryVerifier
─────────────
Runs two SQL queries (original vs optimised) and compares results.

Checks:
  1. Row count — must match exactly.
  2. Column names — same set (case-insensitive).
  3. Symmetric diff — ORDER-BY agnostic (sorts before comparing).
  4. Column-level diff — for matched rows, which specific cells differ.
  5. Aggregate comparison — SUM / MIN / MAX per numeric / date column.
  6. EXPLAIN plan — index used, rows scanned, access type (MySQL / PostgreSQL).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from utils.logger import get_logger

logger = get_logger()


# ─── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class AggRow:
    column: str
    sum_orig: str
    sum_opt: str
    min_orig: str
    min_opt: str
    max_orig: str
    max_opt: str
    match: bool


@dataclass
class ExplainRow:
    """One row from EXPLAIN output — key fields only."""
    id: str
    select_type: str
    table: str
    access_type: str       # 'type' column in MySQL
    possible_keys: str
    key: str
    key_len: str
    rows: str
    filtered: str
    extra: str


@dataclass
class ColDiffRow:
    """A row index where original and optimised differ, with per-column details."""
    row_idx: int
    diffs: dict            # col_name -> (orig_val, opt_val)


@dataclass
class VerifyResult:
    # Row counts
    count_original: int = 0
    count_optimised: int = 0
    count_match: bool = False

    # Columns
    cols_original: list = field(default_factory=list)
    cols_optimised: list = field(default_factory=list)
    cols_match: bool = False
    cols_only_in_original: list = field(default_factory=list)
    cols_only_in_optimised: list = field(default_factory=list)

    # Symmetric diff
    rows_only_in_original: int = 0
    rows_only_in_optimised: int = 0
    diff_sample_original: Optional[pd.DataFrame] = None
    diff_sample_optimised: Optional[pd.DataFrame] = None

    # Column-level diff (populated when row counts match)
    col_diff_rows: list = field(default_factory=list)   # list[ColDiffRow]

    # Aggregates
    agg_rows: list = field(default_factory=list)

    # EXPLAIN plans
    explain_original: list = field(default_factory=list)    # list[ExplainRow]
    explain_optimised: list = field(default_factory=list)

    # Timings
    elapsed_original: float = 0.0
    elapsed_optimised: float = 0.0

    # Overall
    passed: bool = False
    error: Optional[str] = None

    # Internal — cols used for diff
    _diff_cols: list = field(default_factory=list)


# ─── Verifier ─────────────────────────────────────────────────────────────────

class QueryVerifier:
    """Compare two SQL queries using an existing DbService connection."""

    def __init__(self, db_service):
        self._db = db_service

    def verify(self, original_query: str, optimised_query: str,
               row_limit: int = 0) -> VerifyResult:
        result = VerifyResult()

        try:
            # ── Optionally wrap in a LIMIT subquery ───────────────────────────
            orig_sql = self._maybe_limit(original_query, row_limit)
            opt_sql  = self._maybe_limit(optimised_query, row_limit)

            # ── Run EXPLAIN (best-effort, non-fatal) ──────────────────────────
            result.explain_original  = self._run_explain(original_query)
            result.explain_optimised = self._run_explain(optimised_query)

            # ── Run both queries ──────────────────────────────────────────────
            logger.info("QueryVerifier: running original query…")
            t0 = time.time()
            df_orig = self._db.execute_query(orig_sql)
            result.elapsed_original = time.time() - t0
            logger.info(f"QueryVerifier: original → {len(df_orig)} rows in {result.elapsed_original:.2f}s")

            logger.info("QueryVerifier: running optimised query…")
            t0 = time.time()
            df_opt = self._db.execute_query(opt_sql)
            result.elapsed_optimised = time.time() - t0
            logger.info(f"QueryVerifier: optimised → {len(df_opt)} rows in {result.elapsed_optimised:.2f}s")

            # ── Row counts ────────────────────────────────────────────────────
            result.count_original = len(df_orig)
            result.count_optimised = len(df_opt)
            result.count_match = (result.count_original == result.count_optimised)

            # ── Column check (case-insensitive) ───────────────────────────────
            result.cols_original  = list(df_orig.columns)
            result.cols_optimised = list(df_opt.columns)

            orig_lower = {c.lower(): c for c in result.cols_original}
            opt_lower  = {c.lower(): c for c in result.cols_optimised}

            rename_map = {real_opt: orig_lower[lo]
                          for lo, real_opt in opt_lower.items()
                          if lo in orig_lower and real_opt != orig_lower[lo]}
            if rename_map:
                df_opt = df_opt.rename(columns=rename_map)
                result.cols_optimised = list(df_opt.columns)

            orig_set = set(c.lower() for c in result.cols_original)
            opt_set  = set(c.lower() for c in result.cols_optimised)
            result.cols_only_in_original  = sorted(
                c for c in result.cols_original  if c.lower() not in opt_set)
            result.cols_only_in_optimised = sorted(
                c for c in result.cols_optimised if c.lower() not in orig_set)
            result.cols_match = (orig_set == opt_set)

            # ── Symmetric diff (ORDER-BY agnostic) ────────────────────────────
            common_cols = [c for c in result.cols_original if c in set(result.cols_optimised)]
            result._diff_cols = common_cols

            if common_cols and (len(df_orig) > 0 or len(df_opt) > 0):
                str_orig = df_orig[common_cols].astype(str).copy()
                str_opt  = df_opt[common_cols].astype(str).copy()

                # Sort both frames identically → ORDER-BY agnostic
                str_orig_s = str_orig.sort_values(common_cols).reset_index(drop=True)
                str_opt_s  = str_opt.sort_values(common_cols).reset_index(drop=True)

                sep = "\x00|||\x00"
                str_orig_s["_key"] = str_orig_s[common_cols].apply(
                    lambda r: sep.join(r.values), axis=1)
                str_opt_s["_key"] = str_opt_s[common_cols].apply(
                    lambda r: sep.join(r.values), axis=1)

                orig_counts = str_orig_s["_key"].value_counts()
                opt_counts  = str_opt_s["_key"].value_counts()

                all_keys = orig_counts.index.union(opt_counts.index)
                oc = orig_counts.reindex(all_keys, fill_value=0)
                pc = opt_counts.reindex(all_keys, fill_value=0)

                extra_orig = (oc - pc).clip(lower=0)
                extra_opt  = (pc - oc).clip(lower=0)

                result.rows_only_in_original  = int(extra_orig.sum())
                result.rows_only_in_optimised = int(extra_opt.sum())

                if result.rows_only_in_original > 0:
                    bad_keys = extra_orig[extra_orig > 0].index[:10]
                    mask = str_orig_s["_key"].isin(bad_keys)
                    result.diff_sample_original = (
                        str_orig_s.loc[mask, common_cols].head(10).reset_index(drop=True)
                    )

                if result.rows_only_in_optimised > 0:
                    bad_keys = extra_opt[extra_opt > 0].index[:10]
                    mask = str_opt_s["_key"].isin(bad_keys)
                    result.diff_sample_optimised = (
                        str_opt_s.loc[mask, common_cols].head(10).reset_index(drop=True)
                    )

                # ── Column-level diff (only when row counts match) ─────────────
                if result.count_match and result.rows_only_in_original == 0:
                    col_diffs = []
                    n = min(len(str_orig_s), len(str_opt_s))
                    for i in range(n):
                        diffs = {}
                        for col in common_cols:
                            ov = str_orig_s.at[i, col]
                            pv = str_opt_s.at[i, col]
                            if ov != pv:
                                diffs[col] = (ov, pv)
                        if diffs:
                            col_diffs.append(ColDiffRow(row_idx=i + 1, diffs=diffs))
                        if len(col_diffs) >= 50:
                            break
                    result.col_diff_rows = col_diffs

            # ── Aggregate comparison ──────────────────────────────────────────
            numeric_cols  = [c for c in df_orig.columns
                             if pd.api.types.is_numeric_dtype(df_orig[c])
                             and c in df_opt.columns]
            datetime_cols = [c for c in df_orig.columns
                             if pd.api.types.is_datetime64_any_dtype(df_orig[c])
                             and c in df_opt.columns]

            for col in (numeric_cols + datetime_cols)[:12]:
                try:
                    is_num = pd.api.types.is_numeric_dtype(df_orig[col])
                    o_min = str(df_orig[col].min())
                    p_min = str(df_opt[col].min())
                    o_max = str(df_orig[col].max())
                    p_max = str(df_opt[col].max())

                    if is_num:
                        o_sum_val = float(df_orig[col].sum())
                        p_sum_val = float(df_opt[col].sum())
                        o_sum = f"{o_sum_val:,.4g}"
                        p_sum = f"{p_sum_val:,.4g}"
                        tol   = max(abs(o_sum_val) * 1e-9, 1e-9)
                        match = abs(o_sum_val - p_sum_val) <= tol
                    else:
                        o_sum = "—"
                        p_sum = "—"
                        match = (o_min == p_min and o_max == p_max)

                    result.agg_rows.append(AggRow(
                        column=col,
                        sum_orig=o_sum, sum_opt=p_sum,
                        min_orig=o_min, min_opt=p_min,
                        max_orig=o_max, max_opt=p_max,
                        match=match,
                    ))
                except Exception as col_ex:
                    logger.debug(f"QueryVerifier: agg skip {col}: {col_ex}")

            # ── Overall verdict ───────────────────────────────────────────────
            result.passed = (
                result.count_match
                and result.cols_match
                and result.rows_only_in_original  == 0
                and result.rows_only_in_optimised == 0
                and len(result.col_diff_rows)     == 0
                and all(a.match for a in result.agg_rows)
            )

        except Exception as ex:
            result.error = str(ex)
            logger.error(f"QueryVerifier error: {ex}")

        return result

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _maybe_limit(sql: str, limit: int) -> str:
        if limit <= 0:
            return sql
        sql = sql.rstrip().rstrip(";").rstrip()
        return f"SELECT * FROM ({sql}) _qlimit LIMIT {limit}"

    def _run_explain(self, sql: str) -> list:
        """Run EXPLAIN and return list[ExplainRow]. Non-fatal on any error."""
        try:
            db_type = getattr(self._db, "db_type", "")
            if db_type not in ("mysql", "postgresql"):
                return []
            clean = sql.rstrip().rstrip(";").rstrip()
            df = self._db.execute_query(f"EXPLAIN {clean}")
            rows = []
            for _, r in df.iterrows():
                def g(*names, _r=r, _df=df):
                    for n in names:
                        if n in _df.columns:
                            v = _r.get(n)
                            return str(v) if v is not None and str(v) != "None" else ""
                    return ""
                rows.append(ExplainRow(
                    id=g("id"),
                    select_type=g("select_type", "QUERY PLAN"),
                    table=g("table"),
                    access_type=g("type"),
                    possible_keys=g("possible_keys"),
                    key=g("key"),
                    key_len=g("key_len"),
                    rows=g("rows"),
                    filtered=g("filtered"),
                    extra=g("Extra", "extra"),
                ))
            return rows
        except Exception as ex:
            logger.debug(f"QueryVerifier: EXPLAIN failed: {ex}")
            return []
