"""Real offline XLSX/CSV imports and settlement aggregations."""

import datetime as dt
import hashlib
import importlib.util
from pathlib import Path

import duckdb
import pytest
from openpyxl import Workbook

SCRIPT = Path(__file__).resolve().parents[2] / "skills/public/data-analysis/scripts/analyze.py"


@pytest.fixture
def analysis():
    spec = importlib.util.spec_from_file_location("data_analysis_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def offline_db():
    con = duckdb.connect(config={"allow_unsigned_extensions": False, "autoinstall_known_extensions": False, "autoload_known_extensions": False})

    class Offline:
        def execute(self, sql, *args, **kwargs):
            assert not any(word in sql.upper() for word in ("INSTALL ", "LOAD SPATIAL", "ST_READ(")), "analysis must not require network extensions"
            return con.execute(sql, *args, **kwargs)

    yield Offline()
    con.close()


def workbook(path, rows, name="签收明细"):
    wb = Workbook()
    ws = wb.active
    ws.title = name
    for row in rows:
        ws.append(row)
    wb.save(path)
    wb.close()


def test_offline_xlsx_settlement_dates_and_leading_zero_ids(analysis, offline_db, tmp_path):
    path = tmp_path / "运单(O'Brien).xlsx"
    workbook(path, [["用户签收地", "日期", "结算量", "单号"], ["天津", dt.datetime(2026, 11, 1), 12.5, "001"], ["天津", dt.datetime(2026, 11, 1), 7.5, "002"], ["山东", dt.datetime(2026, 11, 2), None, "003"]])
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    tables = analysis.load_files(offline_db, [str(path)])
    assert tables == {"签收明细": "签收明细"}
    assert offline_db.execute('SELECT "用户签收地", CAST("日期" AS DATE), SUM("结算量") FROM "签收明细" GROUP BY 1,2 ORDER BY 1').fetchall() == [("天津", dt.date(2026, 11, 1), 20.0), ("山东", dt.date(2026, 11, 2), None)]
    assert offline_db.execute('SELECT "单号" FROM "签收明细" ORDER BY "单号"').fetchall()[0] == ("001",)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_csv_does_not_install_spatial_and_quotes_path(analysis, offline_db, tmp_path):
    path = tmp_path / "客户's.csv"
    path.write_text("region,volume\nA,12\nA,8\n", encoding="utf-8")
    tables = analysis.load_files(offline_db, [str(path)])
    name = next(iter(tables.values()))
    assert offline_db.execute(f'SELECT sum(volume) FROM "{name}"').fetchone() == (20,)


def test_all_rows_define_mixed_column_type_and_headers(analysis, offline_db, tmp_path):
    path = tmp_path / "mixed.xlsx"
    workbook(path, [["label", "label", None], *[[i, i, None] for i in range(2050)], ["late-text", 3, "tail"]])
    analysis.load_files(offline_db, [str(path)])
    assert offline_db.execute('SELECT count(*) FROM "签收明细"').fetchone() == (2051,)
    assert offline_db.execute("SELECT label FROM \"签收明细\" WHERE label='late-text'").fetchone() == ("late-text",)
    assert len(offline_db.execute('DESCRIBE "签收明细"').fetchall()) == 3


def test_duplicate_sheet_names_across_files_stay_addressable(analysis, offline_db, tmp_path):
    a, b = tmp_path / "a.xlsx", tmp_path / "b.xlsx"
    workbook(a, [["amount"], [1]], "Sheet1")
    workbook(b, [["amount"], [2]], "Sheet1")
    tables = analysis.load_files(offline_db, [str(a), str(b)])
    assert len(tables) == 2
    assert set(tables.values()) == {"Sheet1", "Sheet1_1"}


def test_missing_input_does_not_silently_analyze_partial_files(analysis, offline_db, tmp_path):
    path = tmp_path / "good.csv"
    path.write_text("a\n1\n")
    with pytest.raises(FileNotFoundError):
        analysis.load_files(offline_db, [str(path), str(tmp_path / "missing.csv")])


def test_cache_key_includes_input_names(analysis, tmp_path):
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    a.write_text("x\n1\n")
    b.write_bytes(a.read_bytes())
    assert analysis.compute_files_hash([str(a)]) != analysis.compute_files_hash([str(b)])


def test_sql_failure_propagates_to_owning_command(analysis, offline_db, tmp_path):
    path = tmp_path / "a.csv"
    path.write_text("x\n1\n")
    tables = analysis.load_files(offline_db, [str(path)])
    with pytest.raises(Exception):
        analysis.action_query(offline_db, "SELECT missing FROM a", tables)


def test_empty_sheet_and_quoted_headers(analysis, offline_db, tmp_path):
    path = tmp_path / "quoted.xlsx"
    workbook(path, [['a"b', "c"], ["x", 1]])
    tables = analysis.load_files(offline_db, [str(path)])
    assert 'a"b: 1 / 1' in analysis.action_inspect(offline_db, tables)


def test_literal_values_are_not_rewritten_to_table_names(analysis, offline_db, tmp_path):
    path = tmp_path / "with spaces.csv"
    path.write_text("value\nwith spaces\n")
    tables = analysis.load_files(offline_db, [str(path)])
    assert "with spaces" in analysis.action_query(offline_db, "SELECT value FROM \"with_spaces\" WHERE value='with spaces'", tables)


def test_cli_cache_round_trip_and_failed_import_cleanup(analysis, monkeypatch, tmp_path):
    import sys

    monkeypatch.setattr(analysis, "CACHE_DIR", str(tmp_path / "cache"))
    file = tmp_path / "source.xlsx"
    workbook(file, [["amount"], [20]])
    monkeypatch.setattr(sys, "argv", ["analyze", "--files", str(file), "--action", "inspect"])
    analysis.main()
    analysis.main()
    assert len(list((tmp_path / "cache").glob("*.table_map.json"))) == 1
    broken = tmp_path / "bad.xlsx"
    broken.write_bytes(b"not an xlsx")
    monkeypatch.setattr(sys, "argv", ["analyze", "--files", str(broken), "--action", "inspect"])
    with pytest.raises(Exception):
        analysis.main()
    assert not list((tmp_path / "cache").glob("*.tmp*"))
    assert len(list((tmp_path / "cache").glob("*.table_map.json"))) == 1
