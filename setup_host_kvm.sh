#!/usr/bin/env bash
# One-time, on the HOST that runs the bot in Docker: makes KVM available so node1 auto-detects it.
#   sudo bash setup_host_kvm.sh      then:   docker compose up -d --build
# (Not needed on remote nodes: install_node.sh does this by itself.)
set -e
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }
if [ ! -e /dev/kvm ]; then
  for m in kvm_intel kvm_amd; do modprobe "$m" 2>/dev/null && break || true; done
fi
[ -e /dev/kvm ] || { echo "✘ /dev/kvm missing: no hardware virtualization (or nested virtualization is off). Docker/LXD will be used instead."; exit 1; }
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq && apt-get install -y -qq qemu-system-x86 qemu-utils libvirt-daemon-system libvirt-clients genisoimage openssh-client dnsmasq-base iptables
systemctl enable --now libvirtd
virsh -c qemu:///system net-start default 2>/dev/null || true
virsh -c qemu:///system net-autostart default
mkdir -p /var/lib/lb-kvm && chmod 755 /var/lib/lb-kvm
echo "✔ KVM ready. Restart the bot: docker compose up -d --build   (node1 will now auto-detect KVM)"
