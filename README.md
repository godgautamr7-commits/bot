# VPS Discord Bot – Docker edition

The bot now runs **in Docker**, and every VPS a user deploys is a **Docker container** (Ubuntu 22.04/24.04, Debian 11/12)
with CPU / RAM / disk limits and an SSHX web-terminal (root inside the container only).

## Run the bot (Linux server with Docker)
```bash
cp .env.example .env        # fill DISCORD_TOKEN, ADMIN_IDS, PUBLIC_URL
docker compose up -d --build
docker compose logs -f      # watch the startup checks
```
Optional Cloudflare Tunnel in Docker: set `CLOUDFLARE_TUNNEL_TOKEN` in `.env`, then
`docker compose --profile tunnel up -d` (public hostname -> `http://localhost:8765`).

## Add more servers (nodes)
`/node-add name:node2` in Discord gives you the command. On the new server copy the `node/` folder and run it as root:
```bash
bash install_node.sh https://bot.yourdomain.com <TOKEN>     # installs Docker if missing, then starts the node
```

## Notes
- The bot container mounts `/var/run/docker.sock`, so it can create sibling containers on the host. Whoever controls the bot controls Docker on that host.
- `network_mode: host` + `NET_ADMIN` are needed so the anti-abuse firewall rules (DOCKER-USER chain) can be applied. Linux hosts only (not Docker Desktop).
- Per-VPS **disk limit** needs Docker `overlay2` on **xfs with `pquota`** (or btrfs/zfs). Otherwise the bot warns in the log channel and creates VPS without a disk cap. Set `REQUIRE_DISK_LIMIT=1` to refuse instead.
- Container "VPS" are not full VMs: no systemd inside, no Docker-in-Docker by default.
- `bot.db` is stored in the `bot-data` volume.

## Resource checks (startup + every deploy)
On start the bot prints a checklist (✔/✘): Python, packages, `.env`, Docker CLI + daemon, VPS network, **CPU / RAM / disk**,
**cgroup limits** (CPU, RAM, PIDs), storage driver, IP forwarding, internet (Docker Hub / sshx.io / apt), panel port,
OS images, a **real test container** with limits, per-VPS disk-limit support, and how many VPS of each plan still fit.
Before **every** VPS is created (local node and remote nodes) `check_capacity()` refuses it with a clear message if the
server has too little free RAM/disk, or fewer cores/RAM than the plan needs. Tunables in `.env`:
`MIN_FREE_RAM_MB=512`, `MIN_FREE_DISK_GB=5`, `RAM_OVERCOMMIT=0` (e.g. `1.0` = never promise more RAM than the server has),
`CPU_OVERCOMMIT=0` (e.g. `2` = up to 2 CPU promised per core).

## Auto-detect + KVM / LXD / Docker / LXC (v22)
**Best supported runtime is picked automatically** - for the bot's own machine (node1) and for every node:

`KVM (real VMs) > LXD > Docker > LXC`  ·  KVM not possible -> LXD/Docker/LXC · nothing found -> Docker.

Supported = already working here OR can be installed automatically here (`node/detect.py`, report with `python3 node/detect.py --report`).

**node1 (bot machine):** detected on every start; the log channel and the startup checklist show the result.
- Bot in Docker + you want KVM: run once on the host `sudo bash setup_host_kvm.sh`, then `docker compose up -d --build`.
  (the compose file already mounts the host's libvirt socket + `/var/lib/lb-kvm`). Without it node1 uses Docker.
- Force a type: `LOCAL_NODE_TYPE=kvm|lxd|docker|lxc` in `.env`.

**Remote nodes:** `/node-add name:node2 type:<Auto|KVM|LXD|Docker|LXC>` gives the command, e.g.
```bash
bash install_node.sh https://bot.yourdomain.com <TOKEN>          # auto-detect (default)
bash install_node.sh https://bot.yourdomain.com <TOKEN> kvm      # force
```
- **kvm**: real virtual machines via libvirt (cloud images, qcow2 disk = hard disk limit, root SSHX terminal). Needs `/dev/kvm`.
- **lxd / lxc**: system containers (agent runs natively as a systemd service; LXD/LXC is installed automatically).
- **docker**: agent runs in a container, VPS = Docker containers (Docker installed automatically).

Press **🔌 Connect the node**: the bot self-tests the node (`info` op) and shows its type, readiness, VPS count and disk-limit status.
`/node-list` shows each node's type. Note: Docker + LXD/KVM on the same host can clash on firewall FORWARD rules - if VPS have no internet there, force one type.
