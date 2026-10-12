"""A synthetic capture worker for supervisor tests. Speaks the real protocol; behavior is chosen by argv[1]."""
import json
import struct
import sys
import time

mode = sys.argv[1]
inp, out = sys.stdin.buffer, sys.stdout.buffer


def send(o):
    b = json.dumps(o).encode()
    out.write(struct.pack(">I", len(b)) + b)
    out.flush()


if mode == "deaf":                       # never reads its input
    time.sleep(3600)
while True:
    head = inp.read(4)
    if len(head) < 4:
        break
    req = json.loads(inp.read(struct.unpack(">I", head)[0]))
    if mode == "hang":
        time.sleep(3600)
    elif mode == "huge":
        send({"id": req["id"], "status": "ok", "lines": ["x" * 2_000_000], "colors": 1, "error": ""})
    elif mode == "garbage":
        out.write(struct.pack(">I", 5) + b"nope!"); out.flush()
    elif mode in ("array", "null", "badstatus", "badlines"):
        send({"array": [], "null": None,
              "badstatus": {"id": req["id"], "status": "maybe", "lines": [], "colors": 0, "error": ""},
              "badlines": {"id": req["id"], "status": "ok", "lines": "text", "colors": 0, "error": ""}}[mode])
    elif mode == "nested":
        out.write(struct.pack(">I", 200000) + b"[" * 200000); out.flush()
    elif mode == "partial":
        out.write(struct.pack(">I", 50) + b'{"id"'); out.flush(); time.sleep(3600)
    elif mode == "late":                 # answers after a delay (abandon/late-answer tests)
        time.sleep(float(sys.argv[2]))
        send({"id": req["id"], "status": "ok", "lines": ["late answer from the fake"], "colors": 9, "error": ""})
    else:
        send({"id": req["id"], "status": "ok", "lines": ["hello from the fake worker"], "colors": 9, "error": ""})
