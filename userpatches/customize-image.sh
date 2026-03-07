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
DEFAULT_STATIC_IP="172.16.250.1/16" # Plus prefix!
DEFAULT_GATEWAY="172.16.0.1"
DEFAULT_DNS="1.1.1.1"

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

# Prevent cisco user from becoming root via 'su' or 'su -'
# Insert a PAM rule at the top of /etc/pam.d/su that denies the invoking user if they are cisco
sed -i '1s/^/auth\trequisite\tpam_succeed_if.so use_uid user != cisco\n/' /etc/pam.d/su

# Allow 'cisco' user to run shutdown/reboot without typing sudo
# Sudoers entry grants NOPASSWD privilege for power commands only (no shell, no su)
cat <<EOF > /etc/sudoers.d/cisco-power
cisco ALL=(ALL) NOPASSWD: /sbin/shutdown, /sbin/reboot, /sbin/poweroff, /sbin/halt
EOF
chmod 440 /etc/sudoers.d/cisco-power

# Wrapper scripts so the user can type 'shutdown' / 'reboot' directly (no sudo prefix)
cat <<'WRAPPER' > /usr/local/bin/shutdown
#!/bin/bash
exec sudo /sbin/shutdown "$@"
WRAPPER
chmod +x /usr/local/bin/shutdown

cat <<'WRAPPER' > /usr/local/bin/reboot
#!/bin/bash
exec sudo /sbin/reboot "$@"
WRAPPER
chmod +x /usr/local/bin/reboot

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
mkdir -p /etc/NetworkManager/system-connections
cat <<EOF > /etc/NetworkManager/system-connections/static-end0.nmconnection
[connection]
id=static-end0
type=ethernet
interface-name=end0

[ethernet]

[ipv4]
method=manual
addresses=${DEFAULT_STATIC_IP}
gateway=${DEFAULT_GATEWAY}

[ipv6]
method=ignore
EOF

chmod 600 /etc/NetworkManager/system-connections/static-end0.nmconnection

# Install i2c-tools
apt-get install -y i2c-tools

# Install packages (IP control, display, rotary encoder)
apt-get install python3-venv -y
apt-get install python3-dev -y

# Copy any files from /tmp/overlay to /opt
cp -r /tmp/overlay/* /opt/ || true
# Create the venv and install requirements from /opt/requirements.txt
python3 -m venv /opt/.venv
/opt/.venv/bin/pip install --upgrade pip
/opt/.venv/bin/pip install -r /opt/requirements.txt

# Create systemd service that activates /opt/.venv and runs /opt/main.py on boot
cat <<EOF > /etc/systemd/system/ip-terminal.service
[Unit]
Description=IP Terminal Main Service
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
ExecStart=/opt/.venv/bin/python /opt/main.py
WorkingDirectory=/opt
Restart=always
User=root
[Install]
WantedBy=multi-user.target
EOF

systemctl enable ip-terminal.service
systemctl enable NetworkManager-wait-online.service

# Set hostname
echo "${DEFAULT_HOSTNAME}" > /etc/hostname
sed -i "s/127.0.1.1.*/127.0.1.1\t${DEFAULT_HOSTNAME}/" /etc/hosts || echo "127.0.1.1	${DEFAULT_HOSTNAME}" >> /etc/hosts

# Fix DNS to not use systemd-resolved stub resolver (See: https://www.turek.dev/posts/disable-systemd-resolved-cleanly/)
mkdir -p /etc/systemd/resolved.conf.d/
touch /etc/systemd/resolved.conf.d/disable-stub.conf
cat <<EOF > /etc/systemd/resolved.conf.d/disable-stub.conf
[Resolve]
DNSStubListener=no
EOF

ln -sf /run/systemd/resolve/resolv.conf /etc/resolv.conf

# Set dns in /etc/systemd/resolved.conf
cat <<EOF > /etc/systemd/resolved.conf
[Resolve]
DNS=${DEFAULT_DNS}
EOF

# Remove bash history
rm -f /root/.bash_history
