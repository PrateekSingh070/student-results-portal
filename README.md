# Student Results Portal

Import a spreadsheet export, review rejected rows by CSV line number, and open an individual student’s report. Search the directory by name or ID. The directory and rejection list show 25 records per page.

## Run with Docker

```sh
docker build -t student-results-portal .
docker run --name results-portal -p 127.0.0.1:8080:8080 -v results-data:/data student-results-portal
```

Open [http://localhost:8080](http://localhost:8080). The named volume keeps records across container replacements. To restart the same container after stopping it, use `docker start results-portal`.

## Run locally and test

Use Python 3.12 or later. Create a virtual environment with `python -m venv .venv`. Activate it with `source .venv/bin/activate` on macOS/Linux or `.\.venv\Scripts\Activate.ps1` in Windows PowerShell.

```sh
python -m pip install -r requirements.txt
python -m portal
```

The app listens at [http://localhost:8080](http://localhost:8080) and stores results in `data/results.sqlite3`. Set `RESULTS_DB` to use another database file. Run the tests after installing the dependencies.

```sh
python -m unittest discover -s tests -v
```

## Generate demo data

```sh
python seed.py --rows 5000 --output data/demo.csv
```

Upload the generated file through the browser. To generate and import in one command, add `--load`. It prints a link path to the import summary. To load demo data into the running Docker instance, use `docker exec results-portal python seed.py --output /data/demo.csv --load`.

The generator includes quoted names, Unicode, whitespace, mixed date formats and intentionally damaged records. It uses the same importer as the app.

## CSV format and grading

Use a comma-separated UTF-8 file with these required columns. The order and case of the column names can vary, and extra columns are ignored.

```csv
student_id,full_name,email,date_of_birth,grade_level,section,maths,science,english,social_studies,second_language
DEMO-90001,"Iyer, Meera",meera@school.example,14/03/2014,07,B,83.5,77,88,91,76
```

The maximum is 5 MiB and 20,000 nonblank data rows. Blank records do not count. When correcting rejected records, upload the corrected rows in a new CSV. IDs in the directory are preserved. Re-uploading identical file bytes opens the original summary.

The five subjects are each out of 100, for a maximum of 500. Percentage is the total divided by five. Pass requires at least 35 in **each** subject. F applies whenever a subject is failed. For a passing report, the percentage bands are A+ ≥90, A ≥80, B ≥70, C ≥60, D ≥50 and E ≥35. The exact decimal values determine the grade.

## HTTP API

| Method and path | Response |
|---|---|
| `POST /api/imports` | Send multipart field `file`. Returns a summary, first page of rejections and a `Location` header. New imports return 201, exact replays return 200, and invalid files return 400 or 413. |
| `GET /api/imports/{id}?page=2` | Import counts and paginated rejected rows. Each issue includes a code, field and message. |
| `GET /api/students?q=meera&page=1` | Paginated directory with its total count. |
| `GET /api/students/{student_id}` | Student details, marks and calculated report. Marks, total and percentage are decimal strings. |

The portal is designed for local use with a single administrator. See [NOTES.md](NOTES.md) for the data and retry decisions.
