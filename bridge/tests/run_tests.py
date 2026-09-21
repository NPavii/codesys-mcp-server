# -*- coding: utf-8 -*-
# Test runner for the 46 ported ops against the warm daemon.
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BRIDGE = os.path.dirname(HERE)
PROJ = r"D:\KimiData\kimi\Workspaces\CoDeSyS\Project\Project_One\Project_one.project"
TMP = HERE


def submit(task, timeout=180):
    tf = os.path.join(TMP, "task.json")
    with open(tf, "w") as f:
        json.dump(task, f)
    r = subprocess.run([sys.executable, os.path.join(BRIDGE, "bridge_client.py"),
                        "submit", tf, "--force", "--timeout", str(timeout)],
                       capture_output=True, text=True)
    try:
        return json.loads(r.stdout)
    except Exception:
        return {"ok": False, "error": "RAW: %s %s" % (r.stdout[:200], r.stderr[:200])}


def short(res, limit=260):
    if res.get("ok"):
        return "OK " + json.dumps(res.get("data"), ensure_ascii=False)[:limit]
    err = (res.get("error") or "").strip().splitlines()
    tail = err[-1] if err else "?"
    return "ERR " + tail[:limit]


TESTS = [
    # (label, task)
    ("list_applications", {"op": "list_applications", "args": {"project": PROJ}}),
    ("list_tasks", {"op": "list_tasks", "args": {"project": PROJ}}),
    ("device_info", {"op": "device_info", "args": {"project": PROJ}}),
    ("list_device_parameters", {"op": "list_device_parameters", "args": {"project": PROJ}}),
    ("get_compiler_version", {"op": "get_compiler_version", "args": {"project": PROJ}}),
    ("application_state(no session)", {"op": "application_state", "args": {"project": PROJ, "application": "Application"}}),
    ("plc_file_list(no session)", {"op": "plc_file_list", "args": {"project": PROJ, "application": "Application"}}),
    ("application_reset(no confirm)", {"op": "application_reset", "args": {"project": PROJ, "application": "Application", "level": "origin"}}),
    ("plc_file_delete(no confirm)", {"op": "plc_file_delete", "args": {"project": PROJ, "application": "Application", "path": "/tmp/x"}}),
    ("dump_pou_code", {"op": "dump_pou_code", "args": {"project": PROJ, "root": "MX308_CE/Plc Logic/Application", "max_objects": 30}}),
]

if __name__ == "__main__":
    only = sys.argv[1:] or None
    for label, task in TESTS:
        if only and label.split("(")[0] not in only:
            continue
        print("== %s" % label)
        print("   %s" % short(submit(task)))
