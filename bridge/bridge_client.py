# -*- coding: utf-8 -*-
# Client for the warm CODESYS daemon.
#   python bridge_client.py submit <task.json> [--timeout 120]
#       -> writes bridge/queue/<id>.json, waits for <id>.result.json, prints it
#   python bridge_client.py ping   -> prints daemon_ping.json
#   python bridge_client.py stop   -> asks the daemon to save/close/exit
#   python bridge_client.py wait   -> wait until the daemon answers ping
import argparse
import json
import os
import shutil
import sys
import time

BRIDGE_DIR = os.path.dirname(os.path.abspath(__file__))
QUEUE_DIR = os.path.join(BRIDGE_DIR, "queue")
PING_FILE = os.path.join(BRIDGE_DIR, "daemon_ping.json")
os.makedirs(QUEUE_DIR, exist_ok=True)


def daemon_alive(max_age_s=5.0):
    try:
        with open(PING_FILE, "r") as f:
            ping = json.load(f)
        if ping.get("status") == "stopped":
            return False, ping
        return (time.time() - ping["ts"]) < max_age_s, ping
    except Exception:
        return False, None


def cmd_wait(args):
    t0 = time.time()
    while time.time() - t0 < args.timeout:
        ok, ping = daemon_alive()
        if ok:
            print(json.dumps(ping, indent=2, ensure_ascii=False))
            return 0
        time.sleep(1.0)
    print("daemon did not answer within %ss" % args.timeout, file=sys.stderr)
    return 1


def cmd_ping(args):
    ok, ping = daemon_alive(max_age_s=args.max_age)
    if ok:
        print(json.dumps(ping, indent=2, ensure_ascii=False))
        return 0
    print("daemon not responding", file=sys.stderr)
    return 1


def cmd_stop(args):
    task_id = "stop-%d-%d" % (int(time.time() * 1000), os.getpid())
    with open(os.path.join(QUEUE_DIR, task_id + ".json"), "w") as f:
        json.dump({"op": "stop", "args": {}}, f)
    t0 = time.time()
    while time.time() - t0 < args.timeout:
        if not os.path.exists(os.path.join(QUEUE_DIR, task_id + ".json")):
            # wait for the daemon's final ping (status "stopped"), not a stale one
            t1 = time.time()
            while time.time() - t1 < 15:
                ok, ping = daemon_alive(max_age_s=5.0)
                if ping and ping.get("status") == "stopped":
                    print(json.dumps(ping, indent=2, ensure_ascii=False))
                    return 0
                time.sleep(0.5)
            ok, ping = daemon_alive(max_age_s=5.0)
            print(json.dumps(ping, indent=2, ensure_ascii=False) if ping else "stopped")
            return 0
        time.sleep(0.5)
    print("stop not confirmed within %ss" % args.timeout, file=sys.stderr)
    return 1


def cmd_submit(args):
    with open(args.task_file, "r", encoding="utf-8") as f:
        task = json.load(f)
    alive, _ = daemon_alive()
    if not alive and not args.force:
        print("daemon is not running (stale/missing daemon_ping.json). "
              "Start bridge_daemon.cmd first, or pass --force.", file=sys.stderr)
        return 2
    task_id = "t%d-%d" % (int(time.time() * 1000), os.getpid())
    qpath = os.path.join(QUEUE_DIR, task_id + ".json")
    shutil.copyfile(args.task_file, qpath)
    rpath = os.path.join(QUEUE_DIR, task_id + ".result.json")
    t0 = time.time()
    while time.time() - t0 < args.timeout:
        if os.path.exists(rpath):
            with open(rpath, "r", encoding="utf-8") as f:
                print(f.read())
            os.remove(rpath)
            return 0
        time.sleep(0.3)
    print("timeout %ss waiting for result" % args.timeout, file=sys.stderr)
    return 1


def main():
    ap = argparse.ArgumentParser(description="CODESYS warm-daemon client")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("submit")
    p.add_argument("task_file")
    p.add_argument("--timeout", type=float, default=240.0)
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_submit)

    p = sub.add_parser("ping")
    p.add_argument("--max-age", type=float, default=5.0)
    p.set_defaults(fn=cmd_ping)

    p = sub.add_parser("wait")
    p.add_argument("--timeout", type=float, default=120.0)
    p.set_defaults(fn=cmd_wait)

    p = sub.add_parser("stop")
    p.add_argument("--timeout", type=float, default=60.0)
    p.set_defaults(fn=cmd_stop)

    args = ap.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
