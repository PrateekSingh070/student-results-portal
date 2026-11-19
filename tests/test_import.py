import csv
import io
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from portal import create_app, database
from portal.importer import COLUMNS, MAX_BYTES, FileProblem, parse_csv

TODAY = date(2026, 11, 19)
VALID = ["s-101", " Müller   O'Neil, Ana ", " ANA@EXAMPLE.EDU ", "19/11/2014",
         "09", " b ", "76.2", "65", "90", "82.0", "72"]


def csv_bytes(*rows, header=COLUMNS, bom=False, delimiter=","):
    stream = io.StringIO(newline="")
    csv.writer(stream, delimiter=delimiter).writerows([header, *rows])
    return stream.getvalue().encode("utf-8-sig" if bom else "utf-8")


def changed(values, **changes):
    return [changes.get(field, value) for field, value in zip(COLUMNS, values)]


class ParserTests(unittest.TestCase):
    def test_normalizes_reordered_headers_bom_and_quoted_unicode(self):
        header = [f" {col.upper()} " for col in reversed(COLUMNS)] + ["Extra"]
        rows = parse_csv(csv_bytes([*reversed(VALID), "ignored"], header=header, bom=True), TODAY)
        self.assertEqual(rows[0].issues, [])
        self.assertEqual(rows[0].values, {
            "student_id": "S-101", "full_name": "Müller O'Neil, Ana", "email": "ana@example.edu",
            "date_of_birth": "2014-11-19", "grade_level": 9, "section": "B",
            "maths": "76.2", "science": "65", "english": "90",
            "social_studies": "82.0", "second_language": "72",
        })

    def test_semicolon_and_tab_exports_keep_quoted_values_and_extra_columns(self):
        header = (*COLUMNS, "teacher_note")
        for delimiter in (";", "\t"):
            with self.subTest(delimiter=delimiter):
                row = [*VALID, f"term{delimiter}comment"]
                payload = csv_bytes([], row, header=header, bom=True, delimiter=delimiter)
                parsed, = parse_csv(payload, TODAY)
                self.assertEqual(parsed.line, 3)
                self.assertEqual(parsed.issues, [])
                self.assertEqual(parsed.values["full_name"], "Müller O'Neil, Ana")
                self.assertNotIn("teacher_note", parsed.values)

    def test_physical_line_numbers_count_blank_and_multiline_records(self):
        multiline = changed(VALID, full_name="Müller,\r\nAna")
        rows = parse_csv(csv_bytes([], [" "] * 11, multiline, ["broken", "row"]), TODAY)
        self.assertEqual([(row.line, len(row.issues)) for row in rows], [(4, 0), (6, 1)])
        self.assertEqual(rows[0].values["full_name"], "Müller, Ana")
        self.assertEqual(rows[1].issues[0].code, "WRONG_FIELD_COUNT")

    def test_malformed_quotes_do_not_hide_following_row(self):
        payload = csv_bytes() + b'S-100,"unfinished\r\n' + csv_bytes(VALID).split(b"\r\n", 1)[1]
        rows = parse_csv(payload, TODAY)
        self.assertEqual([row.line for row in rows], [2, 3])
        self.assertEqual(rows[0].issues[0].code, "INVALID_FORMAT")
        self.assertEqual(rows[1].issues, [])

    def test_first_id_claims_identity_even_when_row_has_bad_marks(self):
        first = changed(VALID, maths="-1")
        retry = changed(VALID, student_id=" S-101 ")
        rows = parse_csv(csv_bytes(first, retry), TODAY)
        self.assertEqual([row.issues[0].code for row in rows], ["OUT_OF_RANGE", "DUPLICATE_ID"])
        self.assertIn("line 2", rows[1].issues[0].message)

    def test_reports_multiple_errors_and_rejects_nonfinite_marks(self):
        values = changed(VALID, email="ana@example", grade_level="12.0", maths="NaN",
                         science="Infinity", english="100.001", full_name="")
        row, = parse_csv(csv_bytes(values), TODAY)
        self.assertEqual({issue.field: issue.code for issue in row.issues}, {
            "full_name": "MISSING_REQUIRED", "email": "INVALID_FORMAT",
            "grade_level": "INVALID_FORMAT", "maths": "INVALID_FORMAT",
            "science": "INVALID_FORMAT", "english": "OUT_OF_RANGE",
        })

    def test_age_boundaries_real_dates_and_grade_range(self):
        dates = ["2021-11-19", "2021-11-20", "2000-11-20", "2000-11-19", "29/02/2012", "29/02/2011"]
        values = [changed(VALID, student_id=f"EX-{i}", date_of_birth=day) for i, day in enumerate(dates)]
        values.append(changed(VALID, student_id="EX-7", grade_level="013"))
        results = [row.issues[0].code if row.issues else None for row in parse_csv(csv_bytes(*values), TODAY)]
        self.assertEqual(results, [None, "OUT_OF_RANGE", None, "OUT_OF_RANGE", None, "INVALID_FORMAT", "OUT_OF_RANGE"])

    def test_rejects_bad_file_headers_encoding_and_limits(self):
        for invalid in (b"", b"\xff", b"\x00", csv_bytes(), csv_bytes(VALID, header=COLUMNS[:-1]),
                        csv_bytes(VALID, header=(*COLUMNS, " STUDENT_ID ")), b'"wrong\n'):
            with self.subTest(invalid=invalid[:20]), self.assertRaises(FileProblem):
                parse_csv(invalid, TODAY)
        with self.assertRaises(FileProblem) as limit:
            parse_csv(b"x" * (MAX_BYTES + 1), TODAY)
        self.assertEqual(limit.exception.status, 413)
        with patch("portal.importer.MAX_ROWS", 1), self.assertRaises(FileProblem) as limit:
            parse_csv(csv_bytes(VALID, VALID), TODAY)
        self.assertEqual(limit.exception.status, 413)


class PortalTests(unittest.TestCase):
    def setUp(self):
        temp = self.enterContext(tempfile.TemporaryDirectory())
        self.db = Path(temp) / "results.sqlite3"
        self.app = create_app({"TESTING": True, "DATABASE": self.db})
        self.client = self.app.test_client()
        self.row = changed(VALID, date_of_birth=(date.today() - timedelta(days=365 * 12)).isoformat())

    def upload(self, payload, filename="grades.csv", route="/api/imports"):
        response = self.client.post(route, data={"file": (io.BytesIO(payload), filename)}, buffered=True)
        # Werkzeug spools large simulated request bodies to temporary files.
        response.request.input_stream.close()
        return response

    def test_import_persists_valid_rows_and_replay_returns_original_summary(self):
        payload = csv_bytes(self.row, changed(self.row, student_id="S-102", section="Z"))
        initial = self.upload(payload)
        self.assertEqual(initial.status_code, 201)
        self.assertEqual((initial.json["rows_read"], initial.json["imported"], initial.json["rejected"]), (2, 1, 1))
        self.assertEqual(initial.json["errors"][0]["line"], 3)
        replay = self.upload(payload, "renamed.csv")
        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.json["replayed"])
        self.assertEqual(initial.json["id"], replay.json["id"])
        self.assertEqual(self.client.get(initial.headers["Location"]).json["imported"], 1)
        self.assertEqual(self.client.get("/api/students").json["total"], 1)

    def test_different_upload_preserves_existing_record_and_allows_corrections(self):
        self.upload(csv_bytes(self.row, changed(self.row, student_id="S-102", section="Z")))
        fixed = csv_bytes(changed(self.row, full_name="Overwrite Attempt"), changed(self.row, student_id="S-102"))
        response = self.upload(fixed)
        self.assertEqual(response.json["imported"], 1)
        self.assertEqual(response.json["errors"][0]["issues"][0]["code"], "DUPLICATE_ID")
        original = self.client.get("/api/students/s-101").json["student"]
        self.assertEqual(original["full_name"], "Müller O'Neil, Ana")
        self.assertEqual(self.client.get("/api/students").json["total"], 2)

    def test_invalid_upload_leaves_database_empty_and_returns_readable_errors(self):
        self.assertEqual(self.client.post("/api/imports").status_code, 400)
        self.assertEqual(self.upload(b"junk", "grades.xlsx").status_code, 400)
        self.assertIn("Missing columns", self.upload(b"junk", route="/import").get_data(as_text=True))
        self.assertEqual(self.upload(b"x" * (MAX_BYTES + 1)).status_code, 413)
        self.assertEqual(self.client.get("/api/students").json["total"], 0)
        self.assertEqual(self.client.get("/api/imports/1").status_code, 404)

    def test_search_pagination_and_decimal_grade_boundary(self):
        rows = [changed(self.row, student_id=f"ST-{i:03}", full_name="Álvaro Row") for i in range(30)]
        rows.append(changed(self.row, maths="34.9999999999999999999999999999999", science="100",
                            english="100", social_studies="100", second_language="100"))
        self.upload(csv_bytes(*rows))
        page = self.client.get("/api/students?q=ÁLVARO&page=2").json
        self.assertEqual((page["total"], page["page"]), (30, 2))
        self.assertEqual([row["student_id"] for row in page["students"]],
                         [f"ST-{i:03}" for i in range(25, 30)])
        report = self.client.get("/api/students/S-101").json["report"]
        self.assertEqual(report["grade"], "F")
        self.assertFalse(report["passed"])
        self.assertTrue(report["total"].startswith("434.9999"))
        self.assertEqual(self.client.get("/students/missing").status_code, 404)

    def test_rejection_pagination_and_all_invalid_batch(self):
        failures = [changed(self.row, student_id=f"BAD-{i:03}", maths="") for i in range(30)]
        response = self.upload(csv_bytes(*failures))
        self.assertEqual(response.status_code, 201)
        self.assertEqual((response.json["imported"], response.json["rejected"]), (0, 30))
        page = self.client.get(response.headers["Location"] + "?page=2").json
        self.assertEqual([row["line"] for row in page["errors"]], list(range(27, 32)))

    def test_error_export_includes_every_page_and_one_record_per_issue(self):
        failures = [changed(self.row, student_id=f"BAD-{i:03}", maths="") for i in range(26)]
        failures.append(changed(self.row, student_id="-1-2", full_name="", email="bad"))
        result = self.upload(csv_bytes(*failures))
        response = self.client.get(f"/import/{result.json['id']}/errors.csv")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/csv")
        self.assertEqual(response.headers["Content-Disposition"], 'attachment; filename="import-1-errors.csv"')
        rows = list(csv.DictReader(io.StringIO(response.data.decode("utf-8-sig"))))
        self.assertEqual(len(rows), 28)
        self.assertEqual([row["Line"] for row in rows], [str(i) for i in range(2, 29)] + ["28"])
        self.assertEqual([row["Student ID"] for row in rows[-2:]], ["'-1-2", "'-1-2"])
        self.assertEqual([row["Code"] for row in rows[-2:]], ["MISSING_REQUIRED", "INVALID_FORMAT"])
        self.assertEqual([row["Field"] for row in rows[-2:]], ["Full name", "Email"])
        self.assertEqual(self.client.get("/import/999/errors.csv").status_code, 404)

    def test_concurrent_retries_commit_once(self):
        payload = csv_bytes(self.row)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: database.import_results(self.db, payload, "same.csv"), range(2)))
        self.assertEqual(sorted(results), [(1, False), (1, True)])
        self.assertEqual(self.client.get("/api/students").json["total"], 1)

    def test_failed_database_write_rolls_back_students_and_import(self):
        with closing(database.connect(self.db)) as connection:
            connection.executescript("""
                CREATE TRIGGER stop_import BEFORE INSERT ON students
                WHEN NEW.student_id = 'S-102'
                BEGIN SELECT RAISE(ABORT, 'test write failure'); END;
            """)
        with self.assertLogs(self.app.logger, level="ERROR"):
            response = self.upload(csv_bytes(self.row, changed(self.row, student_id="S-102")))
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.client.get("/api/students").json["total"], 0)
        self.assertEqual(self.client.get("/api/imports/1").status_code, 404)

    def test_browser_form_redirects_to_bookmarkable_results(self):
        response = self.upload(csv_bytes(self.row), route="/import")
        self.assertEqual(response.status_code, 303)
        self.assertIn("Import complete", self.client.get(response.headers["Location"]).get_data(as_text=True))
        directory = self.client.get("/students?q=MÜLLER").get_data(as_text=True)
        self.assertIn("View report", directory)
        report = self.client.get("/students/S-101").get_data(as_text=True)
        self.assertIn("Grading scheme", report)
        self.assertIn("Passed", report)
