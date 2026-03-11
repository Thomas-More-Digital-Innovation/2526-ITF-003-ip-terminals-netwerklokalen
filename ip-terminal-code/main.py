#!/usr/bin/env python3
"""
IP Terminal Configurator

Two operating modes, selected at launch:
  (default)  Hardware mode – rotary encoder + I2C LCD (Raspberry Pi)
  --tui      TUI mode      – dialog-based interface for SSH / serial console

Usage:
  python3 main.py          # hardware mode
  python3 main.py --tui    # TUI mode
"""

import argparse
import ipaddress
import subprocess
import sys
import time


# ---------------------------------------------------------------------------
# Shared state  (module-level so both modes can read/write it)
# ---------------------------------------------------------------------------

ip_octets      = [192, 168, 1, 1]
subnet_prefix  = 16                 # CIDR prefix length (0-32)
gateway_octets = [192, 168, 1, 1]
dns_octets     = [1,   1,   1, 1]
use_dhcp       = False              # True = ipv4.method auto

MENU_OPTIONS    = ["IP address", "Subnet prefix", "Gateway", "DNS"]
MENU_COUNT      = len(MENU_OPTIONS)
CONNECTION_NAME = "end0"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def octets_str(octs):
    return ".".join(str(o) for o in octs)


def field_octets(field):
    """Return the mutable octet list for a field index (0=IP, 2=GW, 3=DNS)."""
    if field == 0:
        return ip_octets
    elif field == 2:
        return gateway_octets
    else:
        return dns_octets


def field_value_str(field):
    """Human-readable current value for a field index."""
    if field == 0:
        return "DHCP" if use_dhcp else octets_str(ip_octets)
    if field == 1:
        return "DHCP" if use_dhcp else f"/{subnet_prefix}"
    if field == 2:
        return "DHCP" if use_dhcp else octets_str(gateway_octets)
    return octets_str(dns_octets)  # field 3: DNS


def auto_gateway_from_ip():
    """Set gateway_octets to the first host address (network + 1) of the current subnet."""
    try:
        network = ipaddress.IPv4Network(
            f"{octets_str(ip_octets)}/{subnet_prefix}", strict=False
        )
        gw = network.network_address + 1
        gateway_octets[:] = list(gw.packed)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Network helpers (shared)
# ---------------------------------------------------------------------------

def get_network_settings():
    """Populate shared state from nmcli / systemd-resolved."""
    global subnet_prefix, use_dhcp

    try:
        result = subprocess.run(
            ["nmcli", "-g", "IP4.ADDRESS", "connection", "show", CONNECTION_NAME],
            capture_output=True, text=True, check=True,
        )
        addr = result.stdout.strip().split("\n")[0]
        if addr and "/" in addr:
            ip_part, prefix_part = addr.split("/")
            ip_octets[:]  = [int(x) for x in ip_part.split(".")]
            subnet_prefix = int(prefix_part)
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["nmcli", "-g", "IP4.GATEWAY", "connection", "show", CONNECTION_NAME],
            capture_output=True, text=True, check=True,
        )
        gw = result.stdout.strip()
        if gw and gw != "--":
            gateway_octets[:] = [int(x) for x in gw.split(".")]
    except Exception:
        pass

    try:
        with open("/etc/systemd/resolved.conf") as f:
            for line in f:
                line = line.strip()
                if line.startswith("DNS="):
                    dns_str = line.split("=", 1)[1].split()[0]
                    dns_octets[:] = [int(x) for x in dns_str.split(".")]
                    break
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["nmcli", "-g", "ipv4.method", "connection", "show", CONNECTION_NAME],
            capture_output=True, text=True, check=True,
        )
        use_dhcp = result.stdout.strip() == "auto"
    except Exception:
        pass


def get_live_ip():
    """Return the currently active IPv4 address (DHCP lease or configured static)."""
    try:
        result = subprocess.run(
            ["nmcli", "-g", "IP4.ADDRESS", "connection", "show", CONNECTION_NAME],
            capture_output=True, text=True, check=True,
        )
        addr = result.stdout.strip().split("\n")[0]
        if addr and "/" in addr:
            return addr.split("/")[0]
        return addr or "No IP assigned"
    except Exception:
        return "Unknown"


def apply_all_settings():
    """Apply IP/prefix, gateway and DNS all at once."""
    # 1. IP address + prefix (or DHCP)
    if use_dhcp:
        subprocess.run(
            ["nmcli", "connection", "modify", CONNECTION_NAME,
             "ipv4.method", "auto", "ipv4.addresses", "", "ipv4.gateway", ""],
            check=True, capture_output=True,
        )
    else:
        cidr = f"{octets_str(ip_octets)}/{subnet_prefix}"
        subprocess.run(
            ["nmcli", "connection", "modify", CONNECTION_NAME,
             "ipv4.method", "manual",
             "ipv4.addresses", cidr,
             "ipv4.gateway", octets_str(gateway_octets)],
            check=True, capture_output=True,
        )
    subprocess.run(["nmcli", "con", "up", CONNECTION_NAME], check=True, capture_output=True)

    # 2. DNS
    dns_str = octets_str(dns_octets)
    subprocess.run(
        ["sudo", "/usr/local/sbin/set-dns.sh", dns_str],
        check=True, capture_output=True,
    )


def apply_settings(field):
    """Apply the current value of *field* via nmcli / systemd-resolved."""
    if field in (0, 1):
        if use_dhcp:
            subprocess.run(
                ["nmcli", "connection", "modify", CONNECTION_NAME,
                 "ipv4.method", "auto", "ipv4.addresses", "", "ipv4.gateway", ""],
                check=True, capture_output=True,
            )
        else:
            cidr = f"{octets_str(ip_octets)}/{subnet_prefix}"
            subprocess.run(
                ["nmcli", "connection", "modify", CONNECTION_NAME,
                 "ipv4.method", "manual", "ipv4.addresses", cidr],
                check=True, capture_output=True,
            )
        subprocess.run(["nmcli", "con", "up", CONNECTION_NAME], check=True, capture_output=True)

    elif field == 2:  # Gateway (static only – no-op in DHCP mode)
        if not use_dhcp:
            subprocess.run(
                ["nmcli", "connection", "modify", CONNECTION_NAME,
                 "ipv4.gateway", octets_str(gateway_octets)],
                check=True, capture_output=True,
            )
            subprocess.run(["nmcli", "con", "up", CONNECTION_NAME], check=True, capture_output=True)

    else:  # DNS
        dns_str = octets_str(dns_octets)
        subprocess.run(
            ["sudo", "/usr/local/sbin/set-dns.sh", dns_str],
            check=True, capture_output=True,
        )


# ---------------------------------------------------------------------------
# Hardware mode  –  rotary encoder + I2C LCD
# ---------------------------------------------------------------------------

def run_hardware():
    """Main loop for the physical rotary-encoder + I2C-LCD interface."""
    global subnet_prefix, use_dhcp

    import RPi.GPIO as GPIO
    from RPLCD.i2c import CharLCD

    # Pin assignments
    CLK = 17
    DT  = 27
    SW  = 22

    lcd = CharLCD("PCF8574", 0x27, cols=16, rows=2)

    HW_MENU       = ["IP address", "Subnet prefix", "Gateway", "DNS", "Apply"]
    HW_MENU_COUNT = len(HW_MENU)
    IP_SUBMENU    = ["Enable DHCP", "View IP", "Set static IP", "<- Back"]

    # Local UI state
    mode             = "menu"   # "menu" | "ip_mode" | "view_ip" | "edit"
    menu_index       = 0
    edit_field       = 0        # field index into MENU_OPTIONS (0-3)
    state_index      = 0        # octet being edited (0-3)
    ip_mode_index    = 0
    live_ip          = [""]
    edit_return_mode = "menu"
    encoder_pulses   = 0
    PULSES_PER_STEP  = 2

    # ---- LCD helpers ----

    def write_row(row, text):
        lcd.cursor_pos = (row, 0)
        lcd.write_string(f"{text:<16}")

    def update_display():
        lcd.clear()
        time.sleep(0.002)  # HD44780 needs ~1.52 ms after clear
        if mode == "menu":
            write_row(0, "Select:")
            write_row(1, f"> {HW_MENU[menu_index]}")
        elif mode == "ip_mode":
            write_row(0, "IP address:")
            write_row(1, f"> {IP_SUBMENU[ip_mode_index]}")
        elif mode == "view_ip":
            write_row(0, "Current IP:")
            write_row(1, live_ip[0][:16])
        else:  # edit
            labels = {0: "Edit IP", 1: "Edit prefix", 2: "Edit GW", 3: "Edit DNS"}
            write_row(0, labels[edit_field])
            if edit_field == 1:
                write_row(1, f"/{subnet_prefix}")
            else:
                parts = [str(o) for o in field_octets(edit_field)]
                parts[state_index] = f">{parts[state_index]}"
                write_row(1, ".".join(parts))

    def do_apply_all():
        lcd.clear()
        lcd.write_string("Applying...     ")
        try:
            apply_all_settings()
            lcd.clear()
            lcd.write_string("Done!           ")
            time.sleep(2)
        except subprocess.CalledProcessError as exc:
            print(f"apply error: {exc}")
            lcd.clear()
            lcd.write_string("Error!          ")
            time.sleep(3)

    # ---- GPIO setup ----
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(CLK, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(DT,  GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(SW,  GPIO.IN, pull_up_down=GPIO.PUD_UP)

    last_A        = GPIO.input(CLK)
    last_B        = GPIO.input(DT)
    last_sw_state = GPIO.input(SW)

    get_network_settings()
    update_display()
    print("IP configurator ready. Turn to navigate, press to select.")

    display_dirty    = False
    last_change_time = 0.0
    DISPLAY_DELAY    = 0.05  # 50 ms – let encoder settle before refreshing LCD

    try:
        while True:
            current_A = GPIO.input(CLK)
            current_B = GPIO.input(DT)
            sw_state  = GPIO.input(SW)

            # Full quadrature decoding (A_RISE/FALL, B_RISE/FALL)
            if current_A == 1 and last_A == 0:
                encoder_pulses += 1 if current_B == 0 else -1
            elif current_A == 0 and last_A == 1:
                encoder_pulses += 1 if current_B == 1 else -1

            if current_B == 1 and last_B == 0:
                encoder_pulses += 1 if current_A == 1 else -1
            elif current_B == 0 and last_B == 1:
                encoder_pulses += 1 if current_A == 0 else -1

            if abs(encoder_pulses) >= PULSES_PER_STEP:
                direction      = 1 if encoder_pulses > 0 else -1
                encoder_pulses = 0

                if mode == "menu":
                    menu_index = (menu_index + direction) % HW_MENU_COUNT
                    print(f"Menu: {HW_MENU[menu_index]}")
                elif mode == "ip_mode":
                    ip_mode_index = (ip_mode_index + direction) % len(IP_SUBMENU)
                    print(f"IP submenu: {IP_SUBMENU[ip_mode_index]}")
                else:  # edit
                    if edit_field == 1:
                        subnet_prefix = max(0, min(32, subnet_prefix + direction))
                        print(f"Subnet prefix: /{subnet_prefix}")
                    else:
                        octs = field_octets(edit_field)
                        octs[state_index] = (octs[state_index] + direction) % 256
                        print(f"Octet {state_index + 1}: {octs[state_index]}")

                display_dirty    = True
                last_change_time = time.monotonic()

            # Button press (active-low)
            if sw_state != last_sw_state and sw_state == 0:
                if mode == "menu":
                    if menu_index == HW_MENU_COUNT - 1:  # Apply
                        print("Applying all settings")
                        do_apply_all()
                    else:
                        edit_field = menu_index
                        if edit_field == 0:  # IP address → show DHCP/Static submenu
                            ip_mode_index = 0 if use_dhcp else 1
                            mode = "ip_mode"
                            print("IP mode submenu")
                        elif edit_field in (1, 2) and use_dhcp:
                            print(f"{HW_MENU[edit_field]} disabled in DHCP mode")
                            write_row(0, "Disabled in")
                            write_row(1, "DHCP mode")
                            time.sleep(2)
                        else:
                            state_index = 0
                            edit_return_mode = "menu"
                            mode = "edit"
                            print(f"Editing: {MENU_OPTIONS[edit_field]}")
                elif mode == "ip_mode":
                    if ip_mode_index == 0:  # Enable DHCP
                        use_dhcp = True
                        mode = "menu"
                        print("DHCP selected — back to main menu")
                    elif ip_mode_index == 1:  # View IP
                        live_ip[0] = get_live_ip()
                        print(f"Current IP: {live_ip[0]}")
                        mode = "view_ip"
                    elif ip_mode_index == 2:  # Set static IP
                        use_dhcp = False
                        state_index = 0
                        edit_return_mode = "ip_mode"
                        mode = "edit"
                        print("Static IP — editing octets")
                    else:  # ← Back
                        mode = "menu"
                        print("Back to main menu")
                elif mode == "view_ip":
                    mode = "ip_mode"  # any press returns to submenu
                    print("Back to IP submenu")
                else:  # edit
                    if edit_field == 1 or state_index == 3:
                        if edit_field in (0, 1):
                            auto_gateway_from_ip()
                            print(f"Auto-gateway set to: {octets_str(gateway_octets)}")
                        mode = edit_return_mode
                        edit_return_mode = "menu"
                        print(f"Back to {mode}")
                    else:
                        state_index += 1
                        print(f"Editing octet {state_index + 1}")

                display_dirty    = True
                last_change_time = time.monotonic()

            # Deferred display refresh
            if display_dirty and (time.monotonic() - last_change_time) >= DISPLAY_DELAY:
                update_display()
                display_dirty = False

            last_A        = current_A
            last_B        = current_B
            last_sw_state = sw_state
            time.sleep(0.001)

    except KeyboardInterrupt:
        print("\nExiting.")
    finally:
        GPIO.cleanup()
        lcd.clear()


# ---------------------------------------------------------------------------
# TUI mode  –  ncurses interface for SSH / serial console
# ---------------------------------------------------------------------------

def run_tui():
    """Launch a dialog-based TUI for configuring network settings over SSH."""

    def dialog(*args):
        """Run dialog and return (returncode, output).  UI is drawn on the
        terminal; the selected value / typed text is captured from stderr."""
        result = subprocess.run(
            ["dialog"] + list(args),
            stderr=subprocess.PIPE, text=True,
        )
        return result.returncode, result.stderr.strip()

    def msgbox(title, text):
        content_lines = text.splitlines()
        height = min(len(content_lines) + 6, 40)
        width  = max(min(max((len(l) for l in content_lines), default=0) + 6, 78), 50)
        subprocess.run(["dialog", "--title", title, "--msgbox", text, str(height), str(width)])

    def run_ip_settings():
        subprocess.run(["nmtui", "edit", CONNECTION_NAME])
        subprocess.run(["nmcli", "con", "down", CONNECTION_NAME])
        subprocess.run(["nmtui", "connect", CONNECTION_NAME])

    def view_ips():
        lines = []
        for family, field in (("IPv4", "IP4.ADDRESS"), ("IPv6", "IP6.ADDRESS")):
            try:
                result = subprocess.run(
                    ["nmcli", "-g", field, "connection", "show", CONNECTION_NAME],
                    capture_output=True, text=True, check=True,
                )
                addrs = [
                    a.strip().replace("\\:", ":")
                    for raw in result.stdout.strip().splitlines()
                    for a in raw.split(" | ")
                    if a.strip() and a.strip() != "--"
                ]
                lines.append(f"{family}:")
                lines.extend(f"  {a}" for a in addrs) if addrs else lines.append("  (none)")
            except Exception as exc:
                lines.append(f"{family}: error ({exc})")
        msgbox("IP Addresses", "\n".join(lines))

    def run_dns_settings():
        current = octets_str(dns_octets)
        while True:
            rc, text = dialog(
                "--title", "Edit DNS settings",
                "--inputbox", "DNS server:", "8", "40", current,
            )
            if rc != 0:
                return
            try:
                parts = text.split(".")
                if len(parts) != 4:
                    raise ValueError
                values = [int(p) for p in parts]
                if not all(0 <= v <= 255 for v in values):
                    raise ValueError
                dns_octets[:] = values
                subprocess.run(
                    ["sudo", "/usr/local/sbin/set-dns.sh", text],
                    check=True, capture_output=True,
                )
                msgbox("DNS", f"DNS set to {text}")
                return
            except ValueError:
                msgbox("Error", f"Invalid address '{text}'\nUse X.X.X.X (0-255)")
                current = text
            except subprocess.CalledProcessError as exc:
                msgbox("Error", f"Error applying DNS:\n{exc}")
                return

    get_network_settings()
    while True:
        rc, choice = dialog(
            "--title", "IP Terminal Configurator",
            "--menu", "Select an option:", "13", "50", "3",
            "1", "Edit IP settings",
            "2", "Edit DNS settings",
            "3", "View IPs",
        )
        if rc != 0:
            break
        if choice == "1":
            run_ip_settings()
        elif choice == "2":
            run_dns_settings()
        elif choice == "3":
            view_ips()
    subprocess.run(["clear"])


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="IP Terminal Configurator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Modes:\n"
            "  (default)  Hardware – rotary encoder + I2C LCD (Raspberry Pi GPIO)\n"
            "  --tui      TUI      – dialog-based interface for SSH / serial console\n"
        ),
    )
    parser.add_argument(
        "--tui",
        action="store_true",
        help="Run in TUI (dialog) mode instead of hardware mode.",
    )
    args = parser.parse_args()

    if args.tui:
        run_tui()
    else:
        run_hardware()
