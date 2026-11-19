#!/usr/bin/env python3
"""Startup checklist for the bot (Docker edition). Standard library only.
Runs before the bot starts; every requirement gets a spinner, then a green tick or a red cross.
Inside Docker the bot always runs in the foreground - docker compose keeps it alive and restarts it."""
import asyncio
import ast
import itertools
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time

TTY = sys.stdout.isatty()
G, R, Y, D, X = ("\033[92m", "\033[91m", "\033[93m", "\033[90m", "\033[0m") if TTY else ("",) * 5
FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class Spinner:
    def __init__(self, label):
        self.label, self.stop_ev = label, threading.Event()
        self.t = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        for f in itertools.cycle(FRAMES):
            if self.stop_ev.is_set():
                return
            sys.stdout.write(f"\r{Y}{f}{X} {self.label} ...")
            sys.stdout.flush()
            time.sleep(0.08)

    def __enter__(self):
        if TTY:
            self.t.start()
        else:
            print(f"... {self.label}", flush=True)
        return self

    def __exit__(self, *a):
        self.stop_ev.set()
        if TTY:
            self.t.join()
            sys.stdout.write("\r" + " " * (len(self.label) + 12) + "\r")


def step(label, fn, fatal=True):
    detail, err = "", None
    with Spinner(label):
        time.sleep(0.2)
        try:
            detail = fn() or ""
        except Exception as e:
            err = e
    if err is None:
        print(f"{G}✔{X} {label}" + (f" {D}({detail}){X}" if detail else ""), flush=True)
        return True
    print(f"{R}✘{X} {label} {D}— {err}{X}", flush=True)
    if fatal:
        print(f"\n{R}Cannot continue. Fix the problem above and run: docker compose up -d --build{X}")
        sys.exit(1)
    return False


def run(cmd, timeout=120):
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout + p.stderr).strip()


# ---------------------------------------------------------------- individual checks
def chk_python():
    if sys.version_info < (3, 9):
        raise RuntimeError("Python 3.9+ needed")
    return sys.version.split()[0]


def chk_modules():
    import aiohttp, discord  # noqa: F401
    return f"discord.py {discord.__version__}"


def chk_env():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    if not os.getenv("DISCORD_TOKEN") or "PASTE" in os.getenv("DISCORD_TOKEN", ""):
        raise RuntimeError("DISCORD_TOKEN missing in .env")
    ids = os.getenv("ADMIN_IDS", "")
    if not any(x.strip().isdigit() for x in ids.replace(",", " ").split()):
        raise RuntimeError("ADMIN_IDS not set in .env (put your Discord user ID)")
    return "token + admin ids ok"


def chk_docker_cli():
    if not shutil.which("docker"):
        raise RuntimeError("docker CLI missing (use the provided Dockerfile)")
    return run("docker --version")[1][:60]


def chk_docker_daemon():
    rc, out = run("docker info --format '{{.ServerVersion}} · {{.Driver}}'", timeout=30)
    if rc:
        raise RuntimeError("cannot reach the Docker daemon - mount /var/run/docker.sock into the container. " + out[-120:])
    return out


def chk_network():
    import agent
    note = asyncio.run(agent.ensure_lxd())
    return note or "VPS network ready"


def chk_iptables():
    if not shutil.which("iptables"):
        raise RuntimeError("iptables missing")
    rc, out = run("iptables -S DOCKER-USER", timeout=15)
    if rc:
        raise RuntimeError("cannot use host iptables (need network_mode: host + cap_add NET_ADMIN): " + out[-100:])
    return "host iptables reachable"


def chk_firewall():
    import agent
    asyncio.run(agent.apply_protection())
    return "anti-abuse rules applied"


def _meminfo():
    """(total, available) bytes of the host (Docker containers see the host's /proc/meminfo)."""
    try:
        with open("/proc/meminfo") as f:
            kv = {l.split(":")[0]: int(l.split()[1]) * 1024 for l in f if ":" in l}
        return kv["MemTotal"], kv.get("MemAvailable", kv.get("MemFree", 0))
    except Exception:
        pg = os.sysconf("SC_PAGE_SIZE")
        tot = pg * os.sysconf("SC_PHYS_PAGES")
        return tot, tot


def _plans():
    """Read PLANS = {...} from bot.py so the checks always use the real plan sizes. Fallback = defaults."""
    try:
        txt = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.py"), encoding="utf-8").read()
        m = re.search(r"^PLANS\s*=\s*(\{.*?\})\s*$", txt, re.M)
        return ast.literal_eval(m.group(1))
    except Exception:
        return {2: (2, 1, 30), 4: (5, 2, 40), 6: (8, 2, 50), 8: (16, 3, 70)}  # (ram GB, cpu, disk GB)


def _docker_info():
    rc, out = run("docker info --format '{{json .}}'", timeout=30)
    if rc:
        raise RuntimeError("cannot read docker info: " + out[-120:])
    return json.loads(out)


def chk_specs():
    du = shutil.disk_usage("/")
    ram, _ = _meminfo()
    free_gb = du.free // 10**9
    if free_gb < 15:
        raise RuntimeError(f"only {free_gb}GB free disk - VPS need much more")
    return f"{os.cpu_count()} CPU · {ram / 1024**3:.1f}GB RAM · {free_gb}GB free disk"


def chk_ram():
    total, avail = _meminfo()
    min_mb = int(os.getenv("MIN_FREE_RAM_MB", "512"))
    if avail < min_mb * 1024**2:
        raise RuntimeError(f"only {avail / 1024**2:.0f}MB RAM available (need at least {min_mb}MB free) - new VPS will be refused")
    return f"{avail / 1024**3:.1f}GB available of {total / 1024**3:.1f}GB"


def chk_cpu():
    n = os.cpu_count() or 1
    try:
        load = os.getloadavg()[0]
    except Exception:
        load = 0.0
    if load > n * 1.5:
        raise RuntimeError(f"server is overloaded (load {load:.1f} on {n} cores)")
    return f"{n} cores · load {load:.2f}"


def chk_cgroups():
    """Docker can only enforce --cpus / --memory / --pids-limit when the kernel cgroup controllers are on."""
    info = _docker_info()
    warns = " | ".join(info.get("Warnings") or [])
    missing = []
    for needle, what in (("memory limit", "RAM limit"), ("cpu cfs", "CPU limit"), ("pids limit", "process limit"),
                         ("cpu shares", "CPU shares")):
        if needle in warns.lower():
            missing.append(what)
    if missing:
        raise RuntimeError("kernel cannot enforce: " + ", ".join(missing) +
                           " (enable cgroups / add 'cgroup_enable=memory' to the kernel cmdline). " + warns[:100])
    note = f"cgroup v{info.get('CgroupVersion', '?')} · {info.get('CgroupDriver', '?')}"
    if "swap limit" in warns.lower():
        note += " · no swap limit (RAM cap still works)"
    return note


def chk_storage_driver():
    info = _docker_info()
    drv = info.get("Driver", "?")
    root = info.get("DockerRootDir", "?")
    fs = ""
    for k, v in info.get("DriverStatus") or []:
        if k == "Backing Filesystem":
            fs = v
    if drv != "overlay2":
        raise RuntimeError(f"storage driver is '{drv}' (overlay2 recommended) · root {root}")
    return f"{drv} on {fs or '?'} · {root}"


def chk_disk_quota():
    """Per-VPS disk cap (--storage-opt size) needs overlay2 on xfs+pquota. Tested with a real container."""
    img = _local_image()
    if not img:
        return "skipped (no image yet - tested on first deploy)"
    rc, out = run(f"docker run --rm --entrypoint true --storage-opt size=1G {img}", timeout=120)
    if rc:
        if os.getenv("REQUIRE_DISK_LIMIT"):
            raise RuntimeError("disk limit NOT enforceable and REQUIRE_DISK_LIMIT=1: " + out[-100:])
        raise RuntimeError("disk limit NOT enforceable (VPS get no disk cap): need overlay2 on xfs with pquota. " + out[-80:])
    return "per-VPS disk limit works"


def _local_image():
    """Any image already on this host that can run the smoke tests (our built VPS image, else a base image)."""
    for tag in ("lb-vps:ubuntu22", "lb-vps:ubuntu24", "lb-vps:debian12", "lb-vps:debian11",
                "ubuntu:22.04", "ubuntu:24.04", "debian:12", "debian:11"):
        if run(f"docker image inspect {tag}", timeout=20)[0] == 0:
            return tag
    return ""


def chk_images():
    import agent
    have = [k for k, (_, base) in agent.IMAGES.items() if run(f"docker image inspect lb-vps:{k}", timeout=20)[0] == 0]
    if len(have) == len(agent.IMAGES):
        return "all OS images built"
    return f"{len(have)}/{len(agent.IMAGES)} OS images built - the rest download on first deploy"


def chk_smoke_test():
    """Creates (and removes) a real container with the same kind of limits a VPS gets."""
    import agent
    img = _local_image()
    if not img:
        rc, out = run("docker pull ubuntu:22.04", timeout=300)
        if rc:
            raise RuntimeError("cannot download ubuntu:22.04 from Docker Hub: " + out[-100:])
        img = "ubuntu:22.04"
    run("docker rm -f lb-startup-test", timeout=30)
    cmd = (f"docker run --rm --name lb-startup-test --network {agent.NETWORK} --cpus 1 --memory 128m "
           f"--memory-swap 128m --pids-limit 128 --security-opt no-new-privileges "
           f"--sysctl net.ipv6.conf.all.disable_ipv6=1 --dns 1.1.1.1 --entrypoint sh {img} -c 'echo ok'")
    rc, out = run(cmd, timeout=120)
    if rc or "ok" not in out:
        raise RuntimeError("test container with CPU/RAM/PID limits failed: " + out[-140:])
    return "test container with CPU/RAM/PID limits ran fine"


def chk_ip_forward():
    try:
        v = open("/proc/sys/net/ipv4/ip_forward").read().strip()
    except Exception:
        return "could not read (skipped)"
    if v != "1":
        raise RuntimeError("net.ipv4.ip_forward=0 - containers will have no internet (sysctl -w net.ipv4.ip_forward=1)")
    return "ip_forward on"


def chk_internet():
    problems = []
    for label, url in (("Docker Hub", "https://registry-1.docker.io/v2/"), ("sshx.io", "https://sshx.io/get"),
                       ("Ubuntu/Debian mirrors", "http://archive.ubuntu.com/ubuntu/")):
        rc, out = run(f"curl -4 -sS -m 12 -o /dev/null -w '%{{http_code}}' {url}", timeout=30)
        if rc or not out[-3:].isdigit() or out[-3:] == "000":
            problems.append(label)
    if problems:
        raise RuntimeError("cannot reach: " + ", ".join(problems) + " (VPS image build / SSHX would fail)")
    return "Docker Hub · sshx.io · apt mirrors reachable"


def chk_panel_port():
    port = int(os.getenv("PANEL_PORT", "8765"))
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
    except OSError as e:
        raise RuntimeError(f"port {port} is already in use ({e})")
    finally:
        s.close()
    return f"panel port {port} free"


def chk_capacity():
    """How many VPS of each plan fit right now (by free RAM, CPU cores and free disk)."""
    import agent
    total, avail = _meminfo()
    free_gb = shutil.disk_usage("/").free / 1024**3
    cores = os.cpu_count() or 1
    rc, out = run("docker ps --filter label=lb.vps=1 -q | wc -l", timeout=20)
    running = int(out) if rc == 0 and out.isdigit() else 0
    fits = []
    for inv, (ram, cpu, disk) in sorted(_plans().items()):
        n = int(min(avail / 1024**3 // ram, free_gb // 5 if disk else 99))  # disk is a cap, not pre-allocated
        fits.append(f"{ram}GB:{max(n, 0)}")
    big = max(r for r, _, _ in _plans().values())
    note = f"{running} VPS running · room for (by RAM) " + " · ".join(fits)
    if big * 1024**3 > total:
        note += f" · ⚠ biggest plan ({big}GB) is larger than this server's RAM"
    return note


def chk_db():
    sqlite3.connect("bot.db").execute("select 1").fetchone()
    return "bot.db ok"


def run_all():
    print(f"\n{G}━━ Startup checks ━━{X}")
    step("Python version", chk_python)
    step("Python packages", chk_modules)
    step(".env config", chk_env)
    import agent as _agent  # auto-detects the runtime of this machine (kvm > lxd > docker > lxc)
    kind = _agent.KIND
    step(f"Runtime auto-detect → {kind.upper()}", lambda: _agent.REPORT_TEXT.splitlines()[0], fatal=False)
    if kind == "docker":
        step("Docker CLI", chk_docker_cli)
        step("Docker daemon (socket)", chk_docker_daemon)
        step("VPS network", chk_network)
    step("Server specs (CPU / RAM / disk)", chk_specs, fatal=False)
    step("Free RAM", chk_ram, fatal=False)
    step("CPU load", chk_cpu, fatal=False)
    if kind == "docker":
        step("Docker cgroup limits (CPU / RAM / PIDs)", chk_cgroups, fatal=False)
        step("Docker storage driver", chk_storage_driver, fatal=False)
    step("IP forwarding (container internet)", chk_ip_forward, fatal=False)
    if kind == "docker":
        step("Internet: Docker Hub / SSHX / apt", chk_internet, fatal=False)
    step("Panel port", chk_panel_port, fatal=False)
    if kind == "docker":
        step("OS images", chk_images, fatal=False)
        step("Test container (limits + network)", chk_smoke_test, fatal=False)
        step("Per-VPS disk limit", chk_disk_quota, fatal=False)
    else:
        print(f"{D}  ({kind}: runtime, network and OS images are prepared automatically in the background after start){X}")
    step("VPS capacity", chk_capacity, fatal=False)
    step("Host iptables", chk_iptables, fatal=False)
    step("Firewall / anti-abuse protection", chk_firewall, fatal=False)
    step("Database", chk_db)
    print(f"{G}━━ All checks done ━━{X}\n", flush=True)


def main_entry(argv):
    """python3 bot.py -> run the checks, then return so bot.py starts the bot in this process."""
    if len(argv) > 1 and argv[1] in ("stop", "restart", "status", "logs"):
        print("Docker edition - use: docker compose stop | restart | ps | logs -f")
        sys.exit(0)
    run_all()


if __name__ == "__main__":
    run_all()
