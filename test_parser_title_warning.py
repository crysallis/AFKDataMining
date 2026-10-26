"""Title warning in parse_members: only a confirmed member card with no title badge warns;
header text and power misreads that are discarded as non-members stay quiet.
Run: venv/Scripts/python.exe test_parser_title_warning.py
Plain-script convention (no pytest in this repo).
"""
import logging
import sys
sys.path.insert(0, "src")
sys.stdout.reconfigure(encoding="utf-8")

from parser import parse_members

failures = 0
total = 0


def check(desc, actual, expected):
    global failures, total
    total += 1
    ok = actual == expected
    print(f"[{'PASS' if ok else 'FAIL'}] {desc}: got {actual!r}, expected {expected!r}")
    if not ok:
        failures += 1


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def blk(x, y, text):
    return ([[x, y], [x + 100, y], [x + 100, y + 30], [x, y + 30]], text, 0.99)


def parse(blocks):
    handler = Capture()
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        members = parse_members(blocks)
    finally:
        root.removeHandler(handler)
    return members, [m for m in handler.messages if "No title matched" in m]


def card(y, name, title=None):
    blocks = [blk(200, y, name), blk(700, y, "2h ago"), blk(200, y + 95, "96205K")]
    if title:
        blocks.append(blk(40, y - 40, title))
    return blocks


members, warnings = parse(card(500, "Kit", title="Sage"))
check("a member with a title badge is read with that title", [(m.name, m.title) for m in members], [("Kit", "Sage")])
check("a member with a title badge does not warn", warnings, [])

members, warnings = parse(card(500, "Kit"))
check("a real member with no title badge is still returned", [m.name for m in members], ["Kit"])
check("a real member with no title badge warns exactly once", len(warnings), 1)
check("the warning names the member", "'Kit'" in warnings[0], True)

members, warnings = parse([blk(200, 161, "Active Players:89")])
check("header text is not a member", members, [])
check("header text does not warn about a missing title", warnings, [])

members, warnings = parse([blk(200, 106, "Max")])
check("the 'Max' header is not a member and does not warn", (members, warnings), ([], []))

members, warnings = parse([blk(200, 1533, "N96205K(Base）")])
check("a power misread with a leading letter is not a member and does not warn", (members, warnings), ([], []))

members, warnings = parse([blk(200, 161, "Active Players:89")] + card(500, "Kit", title="Sage"))
check("header text beside a real member changes neither the member nor the warnings",
      ([m.name for m in members], warnings), (["Kit"], []))

if failures:
    print(f"\n{failures}/{total} cases failed.")
    sys.exit(1)
print(f"\nAll {total} cases passed.")
