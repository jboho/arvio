"""Write items.jsonl for the example task, the way a project's exporter would.

Run: python3 examples/hello/make_items.py
Ids are content hashes, so running it again keeps every id and every label.
"""

import hashlib
import json
import os
import tempfile
from pathlib import Path

HERE = Path(__file__).parent

# (notice, summary sentence, words to highlight, judge verdict, judge reason, line)
CASES = [
    (
        "The 08:10 to Northfield leaves from platform 4. "
        "It calls at Elm Road and Cross Hill.",
        "The 08:10 to Northfield leaves from platform 4.",
        ["platform 4"],
        "supported",
        None,
        "north",
    ),
    (
        "Weekend trains run every 30 minutes until 22:00.",
        "On weekends, trains run every 20 minutes.",
        ["every 30 minutes"],
        "unsupported",
        "wrong_number",
        "north",
    ),
    (
        "The station cafe opens at 06:30 on weekdays.",
        "The cafe opens at 06:30 every day.",
        ["on weekdays"],
        "unsupported",
        "unsupported",
        "south",
    ),
    (
        "Bicycles are allowed on all trains except between 07:00 and 09:00.",
        "Bicycles are not allowed on trains between 07:00 and 09:00.",
        ["except between 07:00 and 09:00"],
        "supported",
        None,
        "south",
    ),
    (
        "Platform 2 is closed for repairs until 14 March. Use platform 3 instead.",
        "Platform 3 is closed for repairs.",
        ["Platform 2 is closed"],
        "unsupported",
        "contradicts",
        "north",
    ),
    (
        "Tickets bought online must be printed or shown on a phone.",
        "Online tickets can be shown on a phone.",
        ["shown on a phone"],
        "supported",
        None,
        "south",
    ),
    (
        "The last train to Southgate leaves at 23:45 and does not stop at Mill Lane.",
        "The last train to Southgate stops at Mill Lane.",
        ["does not stop at Mill Lane"],
        "unsupported",
        "contradicts",
        "south",
    ),
    (
        "Lifts are available at platforms 1 and 4.",
        "There is a lift at platform 4.",
        ["platforms 1 and 4"],
        "supported",
        None,
        "north",
    ),
    (
        "Engineering work on Sunday: buses replace trains "
        "between Elm Road and Cross Hill.",
        "On Sunday, buses run between Elm Road and Cross Hill instead of trains.",
        ["buses replace trains"],
        "supported",
        None,
        "north",
    ),
    (
        "Children under 5 travel free with a paying adult.",
        "Children under 12 travel free.",
        ["under 5"],
        "unsupported",
        "wrong_number",
        "south",
    ),
    (
        "Quiet coaches are at the front of every train.",
        "Phone calls are not allowed in the quiet coaches.",
        [],
        "supported",
        None,
        "north",
    ),
    (
        "Delays of 15 minutes or more can be claimed for a refund within 28 days.",
        "You can claim a refund for a delay of 15 minutes or more.",
        ["15 minutes or more"],
        "supported",
        None,
        "south",
    ),
]

NOTICE_HISTORY = """\
--- notice, first version
+++ notice, current version
@@ -1 +1 @@
-Platform 2 is closed for repairs until 7 March.
+Platform 2 is closed for repairs until 14 March. Use platform 3 instead.
"""


def item(notice, summary, highlight, verdict, reason, line):
    digest = hashlib.sha256(f"{notice}\n{summary}".encode()).hexdigest()
    display = [
        {"kind": "text", "title": "Notice", "text": notice, "highlight": highlight},
        {"kind": "text", "title": "Summary sentence", "text": summary},
        {
            "kind": "fields",
            "title": "Details",
            "fields": [["Line", f"{line}ern line"], ["Posted by", "Station office"]],
            "collapsed": True,
        },
        {
            "kind": "text",
            "title": "Notice record",
            "text": f"board={line}-concourse\nposted_by=station-office\n"
            f"chars={len(notice)}",
            "mono": True,
            "collapsed": True,
        },
    ]
    if "Platform 2" in notice:
        display.append(
            {
                "kind": "diff",
                "title": "Notice history",
                "text": NOTICE_HISTORY,
                "collapsed": True,
            }
        )
    judge = {"verdict": verdict}
    if reason:
        judge["reason"] = reason
    return {
        "id": f"ex-{digest[:10]}",
        "display": display,
        "prefill": {"sentence": summary},
        "judge": judge,
        "meta": {"line": line},
    }


def main():
    rows = [item(*case) for case in CASES]
    fd, tmp = tempfile.mkstemp(dir=HERE, prefix=".items.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, HERE / "items.jsonl")
    print(f"wrote {len(rows)} items from {len(CASES)} cases")
    if len(rows) != len(CASES):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
