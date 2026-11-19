"""Generate an independent messy CSV and optionally import it."""

import argparse
import csv
import os
import random
from datetime import date, timedelta
from pathlib import Path

from portal.database import import_results, initialize
from portal.importer import COLUMNS, MAX_ROWS

GIVEN_NAMES = ("Anika", "Arjun", "Diya", "Kabir", "Meera", "Nikhil", "Zoya", "Ravi", "Élodie", "Leena")
FAMILY_NAMES = ("Bhat", "Chandra", "Devi", "Iyer", "Khan", "Menon", "O'Neil", "Rao", "Shah", "Thomas")


def make_students(count, rng):
    today = date.today()
    for index in range(1, count + 1):
        code = f"DEMO-{index:05}"
        given = rng.choice(GIVEN_NAMES)
        family = rng.choice(FAMILY_NAMES)
        grade_level = rng.randint(1, 12)
        dob = today - timedelta(days=(grade_level + 5) * 365 + rng.randrange(365))
        name = f"{family}, {given}" if index % 17 == 0 else f"{given}  {family}"
        email = f"student{index}@school.example"
        date_text = dob.strftime("%d/%m/%Y") if index % 3 == 0 else dob.isoformat()
        marks = [min(100, max(0, round(rng.gauss(68, 17), 1))) for _ in range(5)]
        row = [code, f" {name} ", email, date_text, f"{grade_level:02}", rng.choice("abcdef"), *marks]
        # Distinct failures on a predictable cycle, with valid rows on either side.
        fault = index % 80
        if fault == 7:
            row[1] = ""
        elif fault == 13:
            row[2] = "student@"
        elif fault == 21:
            row[3] = "31/02/2011"
        elif fault == 28:
            row[5] = "Z"
        elif fault == 34:
            row[6] = 105
        elif fault == 42:
            row[0] = f"demo-{index - 1:05}"
        elif fault == 57:
            row.pop()
        if index % 47 == 0:
            yield []
        yield row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=5000)
    parser.add_argument("--random-seed", type=int, default=42)
    parser.add_argument("--output", type=Path, default=Path("data") / "demo.csv")
    parser.add_argument("--load", action="store_true", help="Import into the same database as the app.")
    args = parser.parse_args()
    if not 1 <= args.rows <= MAX_ROWS:
        parser.error(f"--rows must be between 1 and {MAX_ROWS:,}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(COLUMNS)
        writer.writerows(make_students(args.rows, random.Random(args.random_seed)))
    print(f"Wrote {args.rows:,} student rows to {args.output}")

    if args.load:
        from portal.database import get_import

        database_path = os.environ.get("RESULTS_DB", str(Path("data") / "results.sqlite3"))
        initialize(database_path)
        import_id, replay = import_results(database_path, args.output.read_bytes(), args.output.name)
        result = get_import(database_path, import_id, 1, 1)
        print(f"{'Existing import' if replay else 'Import complete'} · {result['imported']:,} imported, "
              f"{result['rejected']:,} rejected · /import/{import_id}")


if __name__ == "__main__":
    main()
