import re, sys
p = sys.argv[1] if len(sys.argv) > 1 else "dashboard.py"
s = open(p, encoding="utf-8").read()
if "_s = read_status()" in s:
    print("already patched")
    sys.exit()
m = re.search(r"^([ \t]*)threading\.Thread\(target=watcher[^\n]*$", s, re.M)
if not m or "def read_status" not in s:
    print("NOT FOUND")
    sys.exit(1)
i = m.group(1)
new = i + "_s = read_status()\n" + i + 'ST["prev"], ST["vm"] = _s["state"], _s["vm"]\n' + m.group(0)
s = s[:m.start()] + new + s[m.end():]
open(p, "w", encoding="utf-8", newline="\n").write(s)
print("patched", p)
