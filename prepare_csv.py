"""Make any CSV ready for the SpotOps dashboard upload.

Usage:  python prepare_csv.py yourfile.csv [--label COLUMN_NAME_OR_NUMBER] [--out dataset_ready.csv]

It detects the separator (comma, semicolon, tab), drops text columns, moves the label
column to the end, caps the file at 20,000 rows and writes dataset_ready.csv.
"""
import argparse
import csv
import sys


def isnum(s):
    try:
        float(s)
        return True
    except ValueError:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("--label", help="label column name or 1-based number (default: last column)")
    ap.add_argument("--out", default="dataset_ready.csv")
    ap.add_argument("--max-rows", type=int, default=20000)
    a = ap.parse_args()

    with open(a.input, newline="", encoding="utf-8-sig", errors="replace") as f:
        sample = f.read(20000)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        rows = [[c.strip() for c in r] for r in csv.reader(f, dialect) if r]
    if len(rows) < 22 or len(rows[0]) < 2:
        sys.exit("File has too few rows or columns (need at least 20 data rows and 2 columns).")

    second = rows[1]
    header = any(not isnum(c) and i < len(second) and isnum(second[i]) for i, c in enumerate(rows[0]))
    names = rows[0] if header else ["col%d" % (i + 1) for i in range(len(rows[0]))]
    data = rows[1:] if header else rows
    width = len(names)
    data = [r for r in data if len(r) == width]

    if a.label is None:
        li = width - 1
    elif a.label.isdigit():
        li = int(a.label) - 1
    elif a.label in names:
        li = names.index(a.label)
    else:
        sys.exit("Label column '%s' not found. Columns are: %s" % (a.label, ", ".join(names)))
    if not 0 <= li < width:
        sys.exit("Label column number is out of range (1 to %d)." % width)

    keep = []
    for i in range(width):
        if i == li:
            continue
        vals = [r[i] for r in data if r[i] != ""]
        if vals and sum(isnum(v) for v in vals) / len(vals) >= 0.95:
            keep.append(i)
    if not keep:
        sys.exit("No numeric feature columns were found.")

    out = []
    for r in data:
        if r[li] and all(isnum(r[i]) for i in keep):
            out.append([r[i] for i in keep] + [r[li]])
        if len(out) >= a.max_rows:
            break
    classes = sorted(set(r[-1] for r in out))
    if len(out) < 20 or not 2 <= len(classes) <= 50:
        sys.exit("Label column '%s' has %d different values; it needs 2 to 50 (try --label with another column)."
                 % (names[li], len(classes)))

    with open(a.out, "w", newline="") as f:
        csv.writer(f).writerows(out)
    dropped = [names[i] for i in range(width) if i != li and i not in keep]
    print("Wrote %s: %d rows, %d numeric features, label = '%s' with %d classes."
          % (a.out, len(out), len(keep), names[li], len(classes)))
    if dropped:
        print("Dropped text columns: " + ", ".join(dropped[:12]) + (" ..." if len(dropped) > 12 else ""))


if __name__ == "__main__":
    main()
