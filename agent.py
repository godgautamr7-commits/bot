#!/usr/bin/env python3
"""Bot-side node1 loader.  `import agent as nodeapi` in bot.py.

Auto-detects what THIS machine (the one the bot runs on) supports and loads that backend:
    kvm (real VMs)  >  lxd  >  docker  >  lxc          (see node/detect.py)
KVM not possible -> LXD / Docker / LXC, whichever is best. Force one with NODE_TYPE=kvm|lxd|docker|lxc
(or LOCAL_NODE_TYPE, which wins over NODE_TYPE for node1 only).

Everything else (OPS, sh, IMAGES, ensure_lxd, ...) comes from the chosen backend, so bot.py does not care.
"""
import importlib
import os
import sys

_NODE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "node")
if _NODE_DIR not in sys.path:
    sys.path.append(_NODE_DIR)  # appended, so node/agent.py can never shadow this file

import detect  # noqa: E402

_want = os.getenv("LOCAL_NODE_TYPE") or os.getenv("NODE_TYPE") or "auto"
try:
    KIND, REPORT = detect.choose(_want)
except ValueError as _e:
    print(f"{_e} - using auto", flush=True)
    KIND, REPORT = detect.choose("auto")
REPORT_TEXT = detect.text_report(REPORT)
print("node1 " + REPORT_TEXT, flush=True)

_be = importlib.import_module(f"backends.{KIND}_backend")


def __getattr__(name):  # agent.OPS, agent.sh, agent.IMAGES ... -> the chosen backend
    return getattr(_be, name)
