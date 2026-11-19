"""Calculate a report using the exact stored decimal marks."""

from decimal import Decimal, localcontext

from .importer import SUBJECTS

PASS_MARK = 35
GRADE_BANDS = ((90, "A+"), (80, "A"), (70, "B"), (60, "C"), (50, "D"), (35, "E"))


def calculate_report(student):
    marks = {key: Decimal(student[key]) for key in SUBJECTS}
    with localcontext() as context:
        context.prec = 140
        maximum = len(marks) * 100
        total = sum(marks.values())
        percentage = total / len(marks)
    passed = all(mark >= PASS_MARK for mark in marks.values())
    grade = next((grade for minimum, grade in GRADE_BANDS if percentage >= minimum), "F")
    return {
        "subjects": [
            {"name": label, "mark": student[key], "passed": marks[key] >= PASS_MARK}
            for key, label in SUBJECTS.items()
        ],
        "total": format(total, "f"),
        "maximum": maximum,
        "percentage": format(percentage, "f"),
        "grade": grade if passed else "F",
        "passed": passed,
    }
