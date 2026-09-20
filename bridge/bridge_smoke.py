# -*- coding: utf-8 -*-
# Smoke test: proves a --noUI CODESYS process stays alive while the script
# is inside a blocking loop (basis for the warm daemon).
import time
import os

HB = r"D:\KimiData\kimi\Workspaces\CoDeSyS\CodeSYS-MCP\bridge\smoke_heartbeat.txt"
DURATION = 180.0
INTERVAL = 2.0

t0 = time.time()
n = 0
while time.time() - t0 < DURATION:
    n += 1
    with open(HB, "w") as f:
        f.write("tick %d elapsed=%.1f\n" % (n, time.time() - t0))
    time.sleep(INTERVAL)

with open(HB, "w") as f:
    f.write("finished ticks=%d\n" % n)
