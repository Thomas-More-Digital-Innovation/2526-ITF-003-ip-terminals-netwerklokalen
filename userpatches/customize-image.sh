#!/bin/bash

# arguments: $RELEASE $LINUXFAMILY $BOARD $BUILD_DESKTOP
#
# This is the image customization script

# NOTE: It is copied to /tmp directory inside the image
# and executed there inside chroot environment
# so don't reference any files that are not already installed

# NOTE: If you want to transfer files between chroot and host
# userpatches/overlay directory on host is bind-mounted to /tmp/overlay in chroot
# The sd card's root path is accessible via $SDCARD variable.

RELEASE=$1
LINUXFAMILY=$2
BOARD=$3
BUILD_DESKTOP=$4

# SETTINGS
DEFAULT_ACCOUND="cisco"
DEFAULT_PASSWORD="cisco"
DEFAULT_HOSTNAME="ip-terminal"
DEFAULT_STATIC_IP="192.168.1.58"
DEFAULT_GATEWAY="192.168.1.1"

# Update and install your custom packages
apt-get update
apt-get install -y overlayroot btop

# Configure Immutability (overlayroot)
# This makes the root partition read-only and uses RAM for writes.
cat <<EOF > /etc/overlayroot.conf
overlayroot="tmpfs"
overlayroot_cfgmnt="disabled"
EOF

# Disable armbian first run wizard (https://forum.armbian.com/topic/23740-is-is-possible-to-disable-the-first-run-wizard-via-armbain-build-framework/)
rm /root/.not_logged_in_yet

# Handle your /opt files
# Armbian automatically puts your 'userpatches/overlay' files into the image's /
# but if you need to set specific execution permissions:
# chmod +x /opt/my-app/start.sh || true

# Silence SSH messages about overlayfs
touch /root/.hushlogin

# Set root password to 'cisco'
echo "root:${DEFAULT_PASSWORD}" | chpasswd

# Create 'cisco' user with password 'cisco'
useradd -m -s /bin/bash cisco
echo "cisco:${DEFAULT_PASSWORD}" | chpasswd
mkdir -p /home/cisco/
touch /home/cisco/.hushlogin

# Disable SSH root login
# sed -i 's/PermitRootLogin yes/PermitRootLogin no/' /etc/ssh/sshd_config

# Install packages (networking tools)
apt-get install -y bind9-dnsutils # nslookup and dig
apt-get install traceroute # traceroute
apt-get install -y netcat-traditional # netcat
apt-get install -y telnet # telnet
apt-get install -y nmap # nmap
apt-get install -y tcpdump # tcpdump

# Set static IP using NetworkManager
# Write the connection profile directly (nmcli won't work in chroot since the daemon isn't running)
mkdir -p /etc/NetworkManager/system-connections
cat <<EOF > /etc/NetworkManager/system-connections/static-end0.nmconnection
[connection]
id=static-end0
type=ethernet
interface-name=end0

[ethernet]

[ipv4]
method=manual
addresses=${DEFAULT_STATIC_IP}/24
gateway=${DEFAULT_GATEWAY}

[ipv6]
method=ignore
EOF
chmod 600 /etc/NetworkManager/system-connections/static-end0.nmconnection

# Set hostname
echo "${DEFAULT_HOSTNAME}" > /etc/hostname
sed -i "s/127.0.1.1.*/127.0.1.1\t${DEFAULT_HOSTNAME}/" /etc/hosts || echo "127.0.1.1	${DEFAULT_HOSTNAME}" >> /etc/hosts

# Remove bash history
rm -f /root/.bash_history
