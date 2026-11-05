"""
Data Analysis Script using DuckDB.

Analyzes Excel (.xlsx) and CSV files using DuckDB's in-process SQL engine.
Supports schema inspection, SQL queries, statistical summaries, and result export.
"""

import argparse
import csv
import datetime as dt
import hashlib
import json
import logging
import os
import re
import sys
import tempfile
import uuid

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

try:
    import duckdb
except ImportError as exc:
    raise SystemExit(
        "DEPENDENCY_MISSING: duckdb must be provided by the sandbox image; no runtime install attempted."
    ) from exc

try:
    import openpyxl  # noqa: F401
except ImportError as exc:
    raise SystemExit(
        "DEPENDENCY_MISSING: openpyxl must be provided by the sandbox image; no runtime install attempted."
    ) from exc

# Cache directory for persistent DuckDB databases
CACHE_DIR = os.path.join(
    "/mnt/user-data/workspace"
    if os.path.isdir("/mnt/user-data/workspace")
    else tempfile.gettempdir(),
    ".data-analysis-cache",
)
TABLE_MAP_SUFFIX = ".table_map.json"


def compute_files_hash(files: list[str]) -> str:
    """Compute a combined SHA256 hash of all input files for cache key."""
    hasher = hashlib.sha256(b"offline-loader-v2\0")
    for file_path in files:
        # Names and order determine table names, even when contents are identical.
        hasher.update(
            json.dumps(
                [os.path.abspath(file_path), os.path.getsize(file_path)],
                ensure_ascii=False,
            ).encode()
        )
        with open(file_path, "rb") as f:
            digest = (
                hashlib.file_digest(f, "sha256").digest()
                if hasattr(hashlib, "file_digest")
                else None
            )
            if digest is None:
                part = hashlib.sha256()
                while chunk := f.read(65536):
                    part.update(chunk)
                digest = part.digest()
            hasher.update(digest)
    return hasher.hexdigest()


def get_cache_db_path(files_hash: str) -> str:
    """Get the path to the cached DuckDB database file."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f"{files_hash}.duckdb")


def get_table_map_path(files_hash: str) -> str:
    """Get the path to the cached table map JSON file."""
    return os.path.join(CACHE_DIR, f"{files_hash}{TABLE_MAP_SUFFIX}")


def save_table_map(files_hash: str, table_map: dict[str, str]) -> None:
    """Save table map to a JSON file alongside the cached DB."""
    path = get_table_map_path(files_hash)
    temporary = path + "." + uuid.uuid4().hex + ".tmp"
    try:
        with open(temporary, "w", encoding="utf-8") as f:
            json.dump(table_map, f, ensure_ascii=False)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def load_table_map(files_hash: str) -> dict[str, str] | None:
    """Load table map from cache. Returns None if not found."""
    path = get_table_map_path(files_hash)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def sanitize_table_name(name: str) -> str:
    """Sanitize a sheet/file name into a valid SQL table name."""
    sanitized = re.sub(r"[^\w]", "_", name)
    if sanitized and sanitized[0].isdigit():
        sanitized = f"t_{sanitized}"
    return sanitized or "data"


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _table_names(name, table_map):
    table = sanitize_table_name(name)
    base, counter = table, 1
    while table.casefold() in {v.casefold() for v in table_map.values()}:
        table = f"{base}_{counter}"
        counter += 1
    key = name if name not in table_map else table
    while key in table_map:
        key += "_"
    return key, table


def load_files(con: duckdb.DuckDBPyConnection, files: list[str]) -> dict[str, str]:
    """
    Load Excel/CSV files into DuckDB tables.

    Returns a mapping of original_name -> sanitized_table_name.
    """
    table_map: dict[str, str] = {}

    # Fail before importing any subset; an absent input must not become a valid cache.
    for file_path in files:
        if not os.path.isfile(file_path):
            raise FileNotFoundError(file_path)

    for file_path in files:
        ext = os.path.splitext(file_path)[1].lower()

        if ext == ".xlsx":
            _load_excel(con, file_path, table_map)
        elif ext == ".xls":
            raise ValueError(
                "Legacy .xls requires conversion to .xlsx or CSV; the source file was not changed."
            )
        elif ext == ".csv":
            _load_csv(con, file_path, table_map)
        else:
            raise ValueError(f"Unsupported file format: {ext}")

    return table_map


def _load_excel(
    con: duckdb.DuckDBPyConnection, file_path: str, table_map: dict[str, str]
) -> None:
    """Stream XLSX through installed openpyxl and DuckDB's built-in CSV reader.

    Infer types over every row, not only a sample. This preserves late text,
    string identifiers (including leading zeroes), dates, and empty cells without
    holding the workbook in memory or downloading GDAL/spatial extensions.
    """
    wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
    try:
        for sheet in wb:
            sheet.reset_dimensions()
            rows = sheet.iter_rows(values_only=True)
            headers = list(next(rows, ()))
            types = [None] * len(headers)
            key, table = _table_names(sheet.title, table_map)
            with tempfile.TemporaryDirectory(prefix="data-analysis-xlsx-") as folder:
                path = os.path.join(folder, "rows.csv")
                null = "__NULL_" + uuid.uuid4().hex + "__"
                count = 0
                with open(path, "w", encoding="utf-8", newline="") as stream:
                    writer = csv.writer(stream)
                    for row in rows:
                        if not any(value is not None for value in row):
                            continue
                        while len(types) < len(row):
                            headers.append(None)
                            types.append(None)
                        for i, value in enumerate(row):
                            types[i] = _merge_type(types[i], _value_type(value))
                        writer.writerow(
                            [null if value is None else str(value) for value in row]
                        )
                        count += 1
                if not headers:
                    continue
                names = []
                for i, header in enumerate(headers):
                    name = (
                        str(header)
                        if header is not None and str(header)
                        else f"column_{i + 1}"
                    )
                    base, suffix = name, 1
                    while name.casefold() in {n.casefold() for n in names}:
                        name = f"{base}_{suffix}"
                        suffix += 1
                    names.append(name)
                columns = {name: typ or "VARCHAR" for name, typ in zip(names, types)}
                if count:
                    con.execute(
                        f"CREATE TABLE {quote_identifier(table)} AS SELECT * FROM read_csv(?, header=false, auto_detect=false, delim=',', quote='\"', escape='\"', columns=?, nullstr=?, null_padding=true, parallel=false)",
                        [path, columns, null],
                    )
                else:
                    schema = ", ".join(
                        f"{quote_identifier(n)} {t}" for n, t in columns.items()
                    )
                    con.execute(f"CREATE TABLE {quote_identifier(table)} ({schema})")
                table_map[key] = table
                logger.info(
                    "  Loaded sheet %r -> table %r (%d rows)", sheet.title, table, count
                )
    finally:
        wb.close()


def _value_type(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, int):
        return "BIGINT" if -(2**63) <= value < 2**63 else "VARCHAR"
    if isinstance(value, float):
        return "DOUBLE"
    if isinstance(value, dt.datetime):
        return "TIMESTAMP"
    if isinstance(value, dt.date):
        return "DATE"
    if isinstance(value, dt.time):
        return "TIME"
    return "VARCHAR"


def _merge_type(left, right):
    if left is None:
        return right
    if right is None or left == right:
        return left
    if {left, right} <= {"BIGINT", "DOUBLE"}:
        return "DOUBLE"
    if {left, right} <= {"DATE", "TIMESTAMP"}:
        return "TIMESTAMP"
    return "VARCHAR"


def _load_csv(
    con: duckdb.DuckDBPyConnection, file_path: str, table_map: dict[str, str]
) -> None:
    """Load a CSV file into a DuckDB table."""
    base_name = os.path.splitext(os.path.basename(file_path))[0]
    key, table_name = _table_names(base_name, table_map)
    con.execute(
        f"CREATE TABLE {quote_identifier(table_name)} AS SELECT * FROM read_csv_auto(?)",
        [file_path],
    )
    table_map[key] = table_name
    row_count = con.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
    logger.info(
        "  Loaded CSV %r -> table %r (%d rows)", base_name, table_name, row_count
    )


def action_inspect(con: duckdb.DuckDBPyConnection, table_map: dict[str, str]) -> str:
    """Inspect the schema of all loaded tables."""
    output_parts = []

    for original_name, table_name in table_map.items():
        output_parts.append(f"\n{'=' * 60}")
        output_parts.append(f'Table: {original_name} (SQL name: "{table_name}")')
        output_parts.append(f"{'=' * 60}")

        # Get row count
        row_count = con.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
        output_parts.append(f"Rows: {row_count}")

        # Get column info
        columns = con.execute(f'DESCRIBE "{table_name}"').fetchall()
        output_parts.append(f"\nColumns ({len(columns)}):")
        output_parts.append(f"{'Name':<30} {'Type':<15} {'Nullable'}")
        output_parts.append(f"{'-' * 30} {'-' * 15} {'-' * 8}")
        for col in columns:
            col_name, col_type, nullable = col[0], col[1], col[2]
            output_parts.append(f"{col_name:<30} {col_type:<15} {nullable}")

        # Get non-null counts per column
        col_names = [col[0] for col in columns]
        non_null_parts = []
        for c in col_names:
            non_null_parts.append(
                f"COUNT({quote_identifier(c)}) as {quote_identifier(c)}"
            )
        non_null_sql = f'SELECT {", ".join(non_null_parts)} FROM "{table_name}"'
        try:
            non_null_counts = con.execute(non_null_sql).fetchone()
            output_parts.append("\nNon-null counts:")
            for i, c in enumerate(col_names):
                output_parts.append(f"  {c}: {non_null_counts[i]} / {row_count}")
        except Exception:
            pass

        # Sample data (first 5 rows)
        output_parts.append("\nSample data (first 5 rows):")
        try:
            sample = con.execute(f'SELECT * FROM "{table_name}" LIMIT 5').fetchdf()
            output_parts.append(sample.to_string(index=False))
        except Exception:
            sample = con.execute(f'SELECT * FROM "{table_name}" LIMIT 5').fetchall()
            header = [col[0] for col in columns]
            output_parts.append("  " + " | ".join(header))
            for row in sample:
                output_parts.append("  " + " | ".join(str(v) for v in row))

    result = "\n".join(output_parts)
    print(result)
    return result


def action_query(
    con: duckdb.DuckDBPyConnection,
    sql: str,
    table_map: dict[str, str],
    output_file: str | None = None,
) -> str:
    """Execute a SQL query and return/export results."""
    try:
        # Use the SQL names printed by inspect. Regex rewriting can corrupt
        # quoted identifiers and literal data values in filters.
        result = con.execute(sql)
        columns = [desc[0] for desc in result.description]
        rows = result.fetchall()
    except Exception as e:
        error_msg = f"SQL Error: {e}\n\nAvailable tables:\n"
        for orig, tbl in table_map.items():
            cols = con.execute(f'DESCRIBE "{tbl}"').fetchall()
            col_names = [c[0] for c in cols]
            error_msg += f'  "{tbl}" ({orig}): {", ".join(col_names)}\n'
        print(error_msg)
        raise ValueError(error_msg) from e

    # Format output
    if output_file:
        return _export_results(columns, rows, output_file)

    # Print as table
    return _format_table(columns, rows)


def _format_table(columns: list[str], rows: list[tuple]) -> str:
    """Format query results as a readable table."""
    if not rows:
        msg = "Query returned 0 rows."
        print(msg)
        return msg

    # Calculate column widths
    col_widths = [len(str(c)) for c in columns]
    for row in rows:
        for i, val in enumerate(row):
            col_widths[i] = max(col_widths[i], len(str(val)))

    # Cap column width
    max_width = 40
    col_widths = [min(w, max_width) for w in col_widths]

    # Build table
    parts = []
    header = " | ".join(str(c).ljust(col_widths[i]) for i, c in enumerate(columns))
    separator = "-+-".join("-" * col_widths[i] for i in range(len(columns)))
    parts.append(header)
    parts.append(separator)
    for row in rows:
        row_str = " | ".join(
            str(v)[:max_width].ljust(col_widths[i]) for i, v in enumerate(row)
        )
        parts.append(row_str)

    parts.append(f"\n({len(rows)} rows)")
    result = "\n".join(parts)
    print(result)
    return result


def _export_results(columns: list[str], rows: list[tuple], output_file: str) -> str:
    """Export query results to a file (CSV, JSON, or Markdown)."""
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    ext = os.path.splitext(output_file)[1].lower()

    if ext == ".csv":
        import csv

        with open(output_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(columns)
            writer.writerows(rows)

    elif ext == ".json":
        records = []
        for row in rows:
            record = {}
            for i, col in enumerate(columns):
                val = row[i]
                # Handle non-JSON-serializable types
                if hasattr(val, "isoformat"):
                    val = val.isoformat()
                elif isinstance(val, (bytes, bytearray)):
                    val = val.hex()
                record[col] = val
            records.append(record)
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(records, f, indent=2, ensure_ascii=False, default=str)

    elif ext == ".md":
        with open(output_file, "w", encoding="utf-8") as f:
            # Header
            f.write("| " + " | ".join(columns) + " |\n")
            f.write("| " + " | ".join("---" for _ in columns) + " |\n")
            # Rows
            for row in rows:
                f.write(
                    "| " + " | ".join(str(v).replace("|", "\\|") for v in row) + " |\n"
                )
    else:
        msg = f"Unsupported output format: {ext}. Use .csv, .json, or .md"
        print(msg)
        return msg

    msg = f"Results exported to {output_file} ({len(rows)} rows)"
    print(msg)
    return msg


def action_summary(
    con: duckdb.DuckDBPyConnection,
    table_name: str,
    table_map: dict[str, str],
) -> str:
    """Generate statistical summary for a table."""
    # Resolve table name
    resolved = table_map.get(table_name, table_name)

    try:
        columns = con.execute(f'DESCRIBE "{resolved}"').fetchall()
    except Exception:
        available = ", ".join(f'"{t}" ({o})' for o, t in table_map.items())
        msg = f"Table '{table_name}' not found. Available tables: {available}"
        print(msg)
        return msg

    row_count = con.execute(f'SELECT COUNT(*) FROM "{resolved}"').fetchone()[0]

    output_parts = []
    output_parts.append(f"\nStatistical Summary: {table_name}")
    output_parts.append(f"Total rows: {row_count}")
    output_parts.append(f"{'=' * 70}")

    numeric_types = {
        "BIGINT",
        "INTEGER",
        "SMALLINT",
        "TINYINT",
        "DOUBLE",
        "FLOAT",
        "DECIMAL",
        "HUGEINT",
        "REAL",
        "NUMERIC",
    }

    for col in columns:
        col_name, col_type = col[0], col[1].upper()
        output_parts.append(f"\n--- {col_name} ({col[1]}) ---")

        # Check base type (strip parameterized parts)
        base_type = re.sub(r"\(.*\)", "", col_type).strip()

        if base_type in numeric_types:
            try:
                stats = con.execute(f"""
                    SELECT
                        COUNT("{col_name}") as count,
                        AVG("{col_name}")::DOUBLE as mean,
                        STDDEV("{col_name}")::DOUBLE as std,
                        MIN("{col_name}") as min,
                        QUANTILE_CONT("{col_name}", 0.25) as q25,
                        MEDIAN("{col_name}") as median,
                        QUANTILE_CONT("{col_name}", 0.75) as q75,
                        MAX("{col_name}") as max,
                        COUNT(*) - COUNT("{col_name}") as null_count
                    FROM "{resolved}"
                """).fetchone()
                labels = [
                    "count",
                    "mean",
                    "std",
                    "min",
                    "25%",
                    "50%",
                    "75%",
                    "max",
                    "nulls",
                ]
                for label, val in zip(labels, stats):
                    if isinstance(val, float):
                        output_parts.append(f"  {label:<8}: {val:,.4f}")
                    else:
                        output_parts.append(f"  {label:<8}: {val}")
            except Exception as e:
                output_parts.append(f"  Error computing stats: {e}")
        else:
            try:
                stats = con.execute(f"""
                    SELECT
                        COUNT("{col_name}") as count,
                        COUNT(DISTINCT "{col_name}") as unique_count,
                        MODE("{col_name}") as mode_val,
                        COUNT(*) - COUNT("{col_name}") as null_count
                    FROM "{resolved}"
                """).fetchone()
                output_parts.append(f"  count   : {stats[0]}")
                output_parts.append(f"  unique  : {stats[1]}")
                output_parts.append(f"  top     : {stats[2]}")
                output_parts.append(f"  nulls   : {stats[3]}")

                # Show top 5 values
                top_vals = con.execute(f"""
                    SELECT "{col_name}", COUNT(*) as freq
                    FROM "{resolved}"
                    WHERE "{col_name}" IS NOT NULL
                    GROUP BY "{col_name}"
                    ORDER BY freq DESC
                    LIMIT 5
                """).fetchall()
                if top_vals:
                    output_parts.append("  top values:")
                    for val, freq in top_vals:
                        pct = (freq / row_count * 100) if row_count > 0 else 0
                        output_parts.append(f"    {val}: {freq} ({pct:.1f}%)")
            except Exception as e:
                output_parts.append(f"  Error computing stats: {e}")

    result = "\n".join(output_parts)
    print(result)
    return result


def main():
    parser = argparse.ArgumentParser(description="Analyze Excel/CSV files using DuckDB")
    parser.add_argument(
        "--files",
        nargs="+",
        required=True,
        help="Paths to Excel (.xlsx) or CSV files; convert legacy .xls first",
    )
    parser.add_argument(
        "--action",
        required=True,
        choices=["inspect", "query", "summary"],
        help="Action to perform: inspect, query, or summary",
    )
    parser.add_argument(
        "--sql",
        type=str,
        default=None,
        help="SQL query to execute (required for 'query' action)",
    )
    parser.add_argument(
        "--table",
        type=str,
        default=None,
        help="Table name for summary (required for 'summary' action)",
    )
    parser.add_argument(
        "--output-file",
        type=str,
        default=None,
        help="Path to export results (CSV/JSON/MD)",
    )
    args = parser.parse_args()

    # Validate arguments
    if args.action == "query" and not args.sql:
        parser.error("--sql is required for 'query' action")
    if args.action == "summary" and not args.table:
        parser.error("--table is required for 'summary' action")

    # Compute file hash for caching
    files_hash = compute_files_hash(args.files)
    db_path = get_cache_db_path(files_hash)
    cached_table_map = load_table_map(files_hash)

    if cached_table_map and os.path.exists(db_path):
        # Cache hit: connect to existing DB
        logger.info(f"Cache hit! Using cached database: {db_path}")
        con = duckdb.connect(db_path, read_only=True)
        table_map = cached_table_map
        logger.info(
            f"Loaded {len(table_map)} table(s) from cache: {', '.join(table_map.keys())}"
        )
    else:
        # Cache miss: load files and persist to DB
        logger.info("Loading files (first time, will cache for future use)...")
        temporary = db_path + "." + uuid.uuid4().hex + ".tmp.duckdb"
        con = duckdb.connect(temporary)
        try:
            table_map = load_files(con, args.files)
            if not table_map:
                raise ValueError("No tables were loaded. Check file paths and formats.")
            con.close()
            os.replace(temporary, db_path)
            save_table_map(files_hash, table_map)
        finally:
            con.close()
            for owned_path in (temporary, temporary + ".wal"):
                if os.path.exists(owned_path):
                    os.remove(owned_path)
        con = duckdb.connect(db_path, read_only=True)
        logger.info(
            f"\nLoaded {len(table_map)} table(s): {', '.join(table_map.keys())}"
        )
        logger.info(f"Cached database saved to: {db_path}")

    # Perform action
    try:
        if args.action == "inspect":
            action_inspect(con, table_map)
        elif args.action == "query":
            action_query(con, args.sql, table_map, args.output_file)
        elif args.action == "summary":
            action_summary(con, args.table, table_map)
    finally:
        con.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logger.error("DATA_ANALYSIS_FAILED [%s]: %s", type(exc).__name__, exc)
        sys.exit(1)
