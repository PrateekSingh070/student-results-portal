"""Parse and validate a results file without touching the database."""

import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from itertools import islice

SUBJECTS = {
    "maths": "Maths",
    "science": "Science",
    "english": "English",
    "social_studies": "Social Studies",
    "second_language": "Second Language",
}
COLUMNS = (
    "student_id", "full_name", "email", "date_of_birth", "grade_level", "section",
    *SUBJECTS,
)
LABELS = dict(zip(COLUMNS, (
    "Student ID", "Full name", "Email", "Date of birth", "Grade level", "Section",
    *SUBJECTS.values(),
)))
MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 20_000
MAX_RECORD_LINES = 32
ID_PATTERN = re.compile(r"[A-Za-z0-9-]{3,20}")
MARK_PATTERN = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)")
EMAIL_LOCAL = re.compile(r"[a-z0-9!#$%&'*+/=?^_" + chr(96) + r"{|}~.-]+")
DOMAIN_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


class FileProblem(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Issue:
    field: str | None
    code: str
    message: str

    @property
    def label(self):
        return LABELS.get(self.field, "Entire row")


@dataclass
class ParsedRow:
    line: int
    student_id: str | None
    values: dict
    issues: list[Issue]


def valid_email(value):
    local, sep, domain = value.rpartition("@")
    if not sep or len(value) > 254 or len(local) > 64:
        return False
    if not EMAIL_LOCAL.fullmatch(local) or local.startswith(".") or local.endswith(".") or ".." in local:
        return False
    try:
        labels = domain.encode("idna").decode("ascii").lower().split(".")
    except UnicodeError:
        return False
    return (
        len(labels) > 1
        and all(DOMAIN_LABEL.fullmatch(label) for label in labels)
        and not labels[-1].isdigit()
    )


def invalid(field, message):
    return Issue(field, "INVALID_FORMAT", message)


def out_of_range(field, message):
    return Issue(field, "OUT_OF_RANGE", message)


def validate_value(field, value, today):
    if not value:
        return None, Issue(field, "MISSING_REQUIRED", f"{LABELS[field]} is required.")

    if field == "student_id":
        if not ID_PATTERN.fullmatch(value):
            return None, invalid(field, "Use 3–20 letters, digits or hyphens for the student ID.")
        return value.upper(), None

    if field == "full_name":
        if not 2 <= len(value) <= 100:
            return None, out_of_range(field, "Use 2–100 characters for the full name.")
        normalized = " ".join(value.split())
        if not normalized.isprintable():
            return None, invalid(field, "Remove control characters from the full name.")
        return normalized, None

    if field == "email":
        value = value.lower()
        if not valid_email(value):
            return None, invalid(field, "Enter an email address like name@school.edu.")
        return value, None

    if field == "date_of_birth":
        try:
            if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
                birth_date = date.fromisoformat(value)
            elif re.fullmatch(r"[0-9]{2}/[0-9]{2}/[0-9]{4}", value):
                birth_date = datetime.strptime(value, "%d/%m/%Y").date()
            else:
                raise ValueError
        except ValueError:
            return None, invalid(field, "Enter a real date in YYYY-MM-DD or DD/MM/YYYY format.")
        birthday = (birth_date.month, birth_date.day)
        age = today.year - birth_date.year - (birthday > (today.month, today.day))
        if not 5 <= age <= 25:
            return None, out_of_range(field, "The student must be 5–25 years old.")
        return birth_date.isoformat(), None

    if field == "grade_level":
        if not re.fullmatch(r"[0-9]+", value):
            return None, invalid(field, "Enter a whole number for the grade level.")
        significant = value.lstrip("0") or "0"
        if len(significant) > 2 or not 1 <= int(significant) <= 12:
            return None, out_of_range(field, "Grade level must be between 1 and 12.")
        return int(significant), None

    if field == "section":
        if not re.fullmatch(r"[A-Fa-f]", value):
            return None, invalid(field, "Enter a section from A to F.")
        return value.upper(), None

    if len(value) > 128 or not MARK_PATTERN.fullmatch(value):
        return None, invalid(field, "Enter a number such as 75 or 75.5.")
    mark = Decimal(value)
    if not 0 <= mark <= 100:
        return None, out_of_range(field, "Marks must be between 0 and 100.")
    return format(mark if mark else Decimal(0), "f"), None


def parse_csv(payload, today=None):
    if len(payload) > MAX_BYTES:
        raise FileProblem("The CSV exceeds 5 MB. Split it into smaller files.", 413)
    try:
        content = payload.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise FileProblem("Save the file as CSV UTF-8 and upload it again.") from error
    if "\0" in content:
        raise FileProblem("The file contains binary data. Export it as CSV UTF-8.")

    today = today or date.today()
    stream = io.StringIO(content, newline="")
    header, delimiter = read_header(stream.readline())
    if not any(header):
        raise FileProblem("The CSV is empty or has no header. Add the column names on line 1.")
    missing = [field for field in COLUMNS if field not in header]
    if missing:
        raise FileProblem(f"Missing columns in the header: {', '.join(missing)}.")
    if any(header.count(field) > 1 for field in COLUMNS):
        raise FileProblem("A required column appears twice in the header. Keep each required column once.")
    positions = [(field, header.index(field)) for field in COLUMNS]

    seen = {}
    rows = []
    next_line = 2
    while True:
        line = next_line
        start = stream.tell()
        reader = csv.reader(islice(stream, MAX_RECORD_LINES), delimiter=delimiter, strict=True, skipinitialspace=True)
        try:
            record = next(reader, None)
        except csv.Error:
            # Once quoting is broken, the next physical line is the best
            # available restart point. A bounded lookahead limits recovery work.
            stream.seek(start)
            stream.readline()
            next_line += 1
            issue = invalid(None, "This row could not be read. Check its quotes and cell lengths.")
            rows.append(ParsedRow(line, None, {}, [issue]))
        else:
            if record is None:
                break
            next_line += reader.line_num
            if not any(value.strip() for value in record):
                continue
            rows.append(validate_record(record, len(header), positions, line, seen, today))
        if len(rows) > MAX_ROWS:
            raise FileProblem("The CSV exceeds 20,000 data rows. Split it into smaller files.", 413)
    if not rows:
        raise FileProblem("The CSV contains a header but no student rows.")
    return rows


def read_header(line):
    candidates = []
    required = set(COLUMNS)
    for delimiter in (",", ";", "\t"):
        try:
            record = next(csv.reader([line], delimiter=delimiter, strict=True, skipinitialspace=True))
        except csv.Error:
            continue
        header = [field.strip().lower() for field in record]
        matched = len(required.intersection(header))
        if matched == len(required):
            return header, delimiter
        candidates.append((matched, header, delimiter))
    if not candidates:
        raise FileProblem("The header on line 1 could not be read. Check its quotes and column names.")
    _, header, delimiter = max(candidates, key=lambda candidate: candidate[0])
    return header, delimiter


def validate_record(record, column_count, positions, line, seen, today):
    id_index = positions[0][1]
    raw_id = record[id_index].strip() if len(record) > id_index else ""
    student_id = raw_id.upper() if ID_PATTERN.fullmatch(raw_id) else None
    first_line = seen.setdefault(student_id, line) if student_id else line
    if len(record) != column_count:
        message = f"Expected {column_count} columns and found {len(record)}. Check for an extra comma or a missing cell."
        return ParsedRow(line, student_id, {}, [Issue(None, "WRONG_FIELD_COUNT", message)])
    if first_line != line:
        message = f"This student ID already appears on line {first_line} of this file."
        return ParsedRow(line, student_id, {}, [Issue("student_id", "DUPLICATE_ID", message)])

    values = {}
    issues = []
    for field, index in positions:
        values[field], issue = validate_value(field, record[index].strip(), today)
        if issue:
            issues.append(issue)
    return ParsedRow(line, student_id, values, issues)
