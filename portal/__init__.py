"""Student Results Portal application and HTTP routes."""

import csv
import io
import os
import sqlite3
from pathlib import Path

from flask import Flask, Response, abort, jsonify, redirect, render_template, request, url_for
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge

from . import database
from .importer import COLUMNS, MAX_BYTES, FileProblem
from .report import GRADE_BANDS, PASS_MARK, calculate_report

PAGE_SIZE = 25


def create_app(config=None):
    app = Flask(__name__)
    app.config.from_mapping(
        DATABASE=os.environ.get("RESULTS_DB", str(Path("data") / "results.sqlite3")),
        MAX_CONTENT_LENGTH=MAX_BYTES + 64 * 1024,
        MAX_FORM_PARTS=8,
    )
    if config:
        app.config.update(config)
    db = app.config["DATABASE"]
    database.initialize(db)

    def page_number():
        raw = request.args.get("page", "1")
        return max(1, int(raw)) if raw.isascii() and raw.isdecimal() and len(raw) <= 10 else 1

    def upload():
        try:
            file = request.files.get("file")
        except RequestEntityTooLarge as error:
            raise FileProblem("The CSV exceeds 5 MB. Split it into smaller files.", 413) from error
        except BadRequest as error:
            raise FileProblem("The upload could not be read. Choose the CSV and try again.") from error
        if file is None or not file.filename:
            raise FileProblem("Choose a CSV file before importing.")
        name = file.filename.replace("\\", "/").rsplit("/", 1)[-1]
        if not name.lower().endswith(".csv"):
            raise FileProblem("Choose a .csv file exported from your spreadsheet.")
        payload = file.stream.read(MAX_BYTES + 1)
        if len(payload) > MAX_BYTES:
            raise FileProblem("The CSV exceeds 5 MB. Split it into smaller files.", 413)
        return database.import_results(db, payload, name[:250])

    def read_import(import_id):
        batch = database.get_import(db, import_id, page_number(), PAGE_SIZE)
        if batch is None:
            abort(404)
        return batch

    def search():
        query = " ".join(request.args.get("q", "")[:100].split())
        return query, database.list_students(db, query, page_number(), PAGE_SIZE)

    def read_student(student_id):
        if len(student_id) > 20:
            abort(404)
        student = database.get_student(db, student_id)
        if student is None:
            abort(404)
        return student, calculate_report(student)

    @app.template_filter("number")
    def display_number(value):
        whole, dot, fraction = str(value).partition(".")
        suffix = fraction.rstrip("0")
        return f"{int(whole):,}" + (dot + suffix if suffix else "")

    @app.get("/")
    def home():
        return redirect(url_for("import_page"))

    @app.route("/import", methods=["GET", "POST"])
    def import_page():
        if request.method == "POST":
            try:
                import_id, replay = upload()
            except FileProblem as error:
                return render_template("import.html", columns=COLUMNS, upload_error=str(error)), error.status
            return redirect(url_for("import_detail", import_id=import_id, replay=1 if replay else None), 303)
        return render_template("import.html", columns=COLUMNS)

    @app.get("/import/<int:import_id>")
    def import_detail(import_id):
        return render_template(
            "import.html", columns=COLUMNS, batch=read_import(import_id),
            replay=request.args.get("replay") == "1",
        )

    @app.get("/import/<int:import_id>/errors.csv")
    def export_import_errors(import_id):
        read_import(import_id)

        def lines():
            buffer = io.StringIO(newline="")
            writer = csv.writer(buffer)
            writer.writerow(("Line", "Student ID", "Field", "Code", "What to fix"))
            yield "\ufeff" + buffer.getvalue()
            for line, student_id, issues in database.stream_rejections(db, import_id):
                student_id = student_id or ""
                if student_id.startswith("-"):
                    student_id = "'" + student_id
                buffer.seek(0)
                buffer.truncate()
                for issue in issues:
                    writer.writerow((line, student_id, issue["label"], issue["code"], issue["message"]))
                yield buffer.getvalue()

        return Response(lines(), mimetype="text/csv", headers={
            "Content-Disposition": f'attachment; filename="import-{import_id}-errors.csv"',
        })

    @app.get("/students")
    def students_page():
        query, result = search()
        return render_template("students.html", query=query, **result)

    @app.get("/students/<student_id>")
    def student_page(student_id):
        student, report = read_student(student_id)
        return render_template(
            "report.html", student=student, report=report, pass_mark=PASS_MARK, bands=GRADE_BANDS,
            return_query=request.args.get("q", "")[:100], return_page=page_number(),
        )

    @app.post("/api/imports")
    def create_import_api():
        try:
            import_id, replayed = upload()
        except FileProblem as error:
            return jsonify(error=str(error)), error.status
        result = database.get_import(db, import_id, 1, PAGE_SIZE)
        return jsonify(result | {"replayed": replayed}), 200 if replayed else 201, {
            "Location": url_for("import_api", import_id=import_id),
        }

    @app.get("/api/imports/<int:import_id>")
    def import_api(import_id):
        return jsonify(read_import(import_id))

    @app.get("/api/students")
    def students_api():
        _, result = search()
        return jsonify(result)

    @app.get("/api/students/<student_id>")
    def student_api(student_id):
        student, report = read_student(student_id)
        return jsonify(student=student, report=report)

    @app.errorhandler(404)
    def not_found(_error):
        if request.path.startswith("/api/"):
            return jsonify(error="The requested record could not be found."), 404
        if request.path.startswith("/students/"):
            return render_template(
                "error.html", title="Student not found",
                message="There is no student with this ID in the portal.",
                invalid_student=True,
            ), 404
        return render_template(
            "error.html", title="Page not found", message="This address does not match a portal page.",
        ), 404

    @app.errorhandler(sqlite3.Error)
    def database_error(error):
        app.logger.exception("Database request failed")
        busy = getattr(error, "sqlite_errorcode", 0) & 0xFF in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
        status = 503 if busy else 500
        message = "The database is busy. Try again in a moment." if busy else "The request could not be saved or loaded. Try again."
        if request.path.startswith("/api/"):
            return jsonify(error=message), status
        if request.path == "/import":
            return render_template("import.html", columns=COLUMNS, upload_error=message), status
        return render_template("error.html", title="Something went wrong", message=message), status

    @app.after_request
    def response_headers(response):
        if request.endpoint != "static":
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    return app
