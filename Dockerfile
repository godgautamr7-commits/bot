# Bot image: runs the Discord bot. node1 (this machine) is AUTO-DETECTED: kvm > lxd > docker > lxc.
#  - docker: talks to the HOST's Docker daemon through the mounted socket (VPS = sibling containers)
#  - kvm:    talks to the HOST's libvirt through the mounted socket (VPS = real VMs); needs libvirt on the host
FROM docker:27-cli AS dockercli

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update \
 && apt-get install -y --no-install-recommends iptables procps curl ca-certificates \
      libvirt-clients qemu-utils genisoimage openssh-client \
 && rm -rf /var/lib/apt/lists/*
COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY agent.py bot.py startup.py ./
COPY node/detect.py ./node/detect.py
COPY node/backends ./node/backends
# bot.db lives in /data (a volume) so it survives updates
WORKDIR /data
CMD ["python", "/app/bot.py"]
