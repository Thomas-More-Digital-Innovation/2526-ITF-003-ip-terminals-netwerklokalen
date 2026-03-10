#!/usr/bin/env python3
"""
IP Terminal Configurator

Two operating modes, selected at launch:
  (default)  Hardware mode – rotary encoder + I2C LCD (Raspberry Pi)
  --tui      TUI mode      – ncurses interface for SSH / serial console

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
CONNECTION_NAME = "static-end0"


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
    """Launch a curses-based TUI for configuring network settings over SSH."""
    import curses

    def tui_main(stdscr):
        global subnet_prefix, use_dhcp

        curses.curs_set(0)
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_BLACK,  curses.COLOR_CYAN)   # selected row
        curses.init_pair(2, curses.COLOR_CYAN,   -1)                  # title / labels
        curses.init_pair(3, curses.COLOR_GREEN,  -1)                  # status: ok
        curses.init_pair(4, curses.COLOR_RED,    -1)                  # status: error

        stdscr.keypad(True)
        stdscr.timeout(100)

        get_network_settings()

        selected_field = 0
        status_msg     = ""
        status_ok      = True

        # ---- drawing helpers ----

        def hline(row):
            _, w = stdscr.getmaxyx()
            try:
                stdscr.addstr(row, 0, "─" * (w - 1))
            except curses.error:
                pass

        def draw_main():
            stdscr.erase()
            h, w = stdscr.getmaxyx()

            title = " IP Terminal Configurator "
            stdscr.addstr(0, max(0, (w - len(title)) // 2),
                          title, curses.color_pair(2) | curses.A_BOLD)
            hline(1)

            for i, label in enumerate(MENU_OPTIONS):
                val  = field_value_str(i)
                line = f"  {label:<16}  {val}"
                attr = curses.color_pair(1) if i == selected_field else curses.A_NORMAL
                try:
                    stdscr.addstr(2 + i, 0, line.ljust(w - 1), attr)
                except curses.error:
                    pass

            hline(2 + MENU_COUNT)
            help_row = 3 + MENU_COUNT
            try:
                stdscr.addstr(help_row, 0,
                              "  ↑/↓ Navigate   Enter Edit   a Apply   r Reload   q/Esc Quit")
            except curses.error:
                pass

            if status_msg:
                attr = curses.color_pair(3) if status_ok else curses.color_pair(4)
                try:
                    stdscr.addstr(help_row + 2, 0, f"  {status_msg}", attr)
                except curses.error:
                    pass

            stdscr.refresh()

        # ---- generic single-line text input ----

        def read_line(row, col, initial="", allowed="0123456789.", max_len=15):
            """
            Inline text editor. Returns (text, confirmed).
            Supports: printable chars, Backspace, Delete, ←/→, Home, End,
                      Enter (confirm), Esc (cancel).
            """
            buf = list(initial)
            pos = len(buf)
            curses.curs_set(1)
            stdscr.timeout(-1)  # blocking while typing
            try:
                while True:
                    text    = "".join(buf)
                    display = (text + " " * max_len)[:max_len]
                    try:
                        stdscr.addstr(row, col, display, curses.A_REVERSE)
                        stdscr.move(row, col + min(pos, max_len - 1))
                    except curses.error:
                        pass
                    stdscr.refresh()

                    key = stdscr.getch()

                    if key in (curses.KEY_ENTER, ord("\n"), ord("\r")):
                        return ("".join(buf), True)
                    elif key == 27:  # Esc
                        return (initial, False)
                    elif key in (curses.KEY_BACKSPACE, 127, 8):
                        if pos > 0:
                            buf.pop(pos - 1)
                            pos -= 1
                    elif key == curses.KEY_DC:
                        if pos < len(buf):
                            buf.pop(pos)
                    elif key == curses.KEY_LEFT:
                        pos = max(0, pos - 1)
                    elif key == curses.KEY_RIGHT:
                        pos = min(len(buf), pos + 1)
                    elif key == curses.KEY_HOME:
                        pos = 0
                    elif key == curses.KEY_END:
                        pos = len(buf)
                    elif 32 <= key < 127:
                        ch = chr(key)
                        if ch in allowed and len(buf) < max_len:
                            buf.insert(pos, ch)
                            pos += 1
            finally:
                curses.curs_set(0)
                stdscr.timeout(100)

        # ---- edit sub-screens ----

        def draw_edit_screen(title, prompt, value_str, error=""):
            """Render the common edit screen layout. Returns the (row, col) for input."""
            stdscr.erase()
            stdscr.addstr(0, 0, f"  Edit: {title}",
                          curses.color_pair(2) | curses.A_BOLD)
            hline(1)
            stdscr.addstr(3, 4, prompt)
            input_col = 4 + len(prompt) + 1
            if error:
                attr = curses.color_pair(4)
                try:
                    stdscr.addstr(5, 4, f"  {error}", attr)
                except curses.error:
                    pass
            hline(7)
            stdscr.addstr(8, 0, "  Type value   Enter Confirm   Esc Cancel")
            stdscr.refresh()
            return 3, input_col

        def edit_prefix():
            """Edit the subnet prefix length with direct keyboard entry."""
            global subnet_prefix
            error = ""
            while True:
                prompt = f"Prefix length (cur:/{subnet_prefix}) /"
                row, col = draw_edit_screen(MENU_OPTIONS[1], prompt, "", error)
                text, confirmed = read_line(row, col, initial="",
                                            allowed="0123456789", max_len=2)
                if not confirmed:
                    return False
                try:
                    val = int(text)
                    if not 0 <= val <= 32:
                        raise ValueError
                    subnet_prefix = val
                    return True
                except ValueError:
                    error = f"Invalid prefix '{text}' — enter 0..32"

        def edit_octet_field(field):
            """Edit an IPv4 address with direct keyboard entry (e.g. 192.168.1.1)."""
            current = octets_str(field_octets(field))
            error   = ""
            while True:
                row, col = draw_edit_screen(MENU_OPTIONS[field], "Address:",
                                            current, error)
                text, confirmed = read_line(row, col, initial=current,
                                            allowed="0123456789.", max_len=15)
                if not confirmed:
                    return False
                try:
                    parts = text.strip().split(".")
                    if len(parts) != 4:
                        raise ValueError
                    values = [int(p) for p in parts]
                    if not all(0 <= v <= 255 for v in values):
                        raise ValueError
                    field_octets(field)[:] = values
                    return True
                except ValueError:
                    error   = f"Invalid address '{text}' — use X.X.X.X (0-255)"
                    current = text

        def view_ip_screen():
            """Show the currently active IP address. Any key returns."""
            ip = get_live_ip()
            stdscr.erase()
            stdscr.addstr(0, 0, "  Current IP address",
                          curses.color_pair(2) | curses.A_BOLD)
            hline(1)
            stdscr.addstr(3, 4, "Active IP:")
            try:
                stdscr.addstr(3, 15, ip, curses.A_BOLD)
            except curses.error:
                pass
            hline(5)
            stdscr.addstr(6, 0, "  Any key to go back")
            stdscr.refresh()
            stdscr.timeout(-1)
            stdscr.getch()
            stdscr.timeout(100)

        def edit_ip_address():
            """Show IP submenu (Enable DHCP / View IP / Set static IP / ← Back)."""
            global use_dhcp
            IP_SUBMENU = ["Enable DHCP", "View IP", "Set static IP", "<- Back"]
            sel = 0
            stdscr.timeout(-1)
            try:
                while True:
                    stdscr.erase()
                    stdscr.addstr(0, 0, "  IP address",
                                  curses.color_pair(2) | curses.A_BOLD)
                    hline(1)
                    for idx, label in enumerate(IP_SUBMENU):
                        attr = curses.color_pair(1) if idx == sel else curses.A_NORMAL
                        try:
                            stdscr.addstr(3 + idx, 4, label.ljust(24), attr)
                        except curses.error:
                            pass
                    hline(3 + len(IP_SUBMENU))
                    stdscr.addstr(4 + len(IP_SUBMENU), 0,
                                  "  ↑/↓ Select   Enter Confirm   Esc Back")
                    stdscr.refresh()
                    key = stdscr.getch()
                    if key in (curses.KEY_ENTER, ord("\n"), ord("\r")):
                        if sel == 0:  # Enable DHCP
                            use_dhcp = True
                            return False  # return to main menu
                        elif sel == 1:  # View IP
                            stdscr.timeout(100)
                            view_ip_screen()
                            stdscr.timeout(-1)
                            # loop back to submenu
                        elif sel == 2:  # Set static IP
                            use_dhcp = False
                            stdscr.timeout(100)
                            if edit_octet_field(0):
                                auto_gateway_from_ip()
                            stdscr.timeout(-1)
                            # loop back to submenu
                        else:  # ← Back
                            return False
                    elif key == 27:  # Esc
                        return False
                    elif key == curses.KEY_UP:
                        sel = (sel - 1) % len(IP_SUBMENU)
                    elif key == curses.KEY_DOWN:
                        sel = (sel + 1) % len(IP_SUBMENU)
            finally:
                stdscr.timeout(100)

        # ---- main event loop ----

        while True:
            draw_main()
            key = stdscr.getch()

            if key == curses.KEY_UP:
                selected_field = (selected_field - 1) % MENU_COUNT
                status_msg     = ""

            elif key == curses.KEY_DOWN:
                selected_field = (selected_field + 1) % MENU_COUNT
                status_msg     = ""

            elif key in (curses.KEY_ENTER, ord("\n"), ord("\r")):
                if selected_field == 0:
                    edit_ip_address()
                elif selected_field == 1:
                    if use_dhcp:
                        status_msg = "Disabled in DHCP mode"
                        status_ok  = False
                    elif edit_prefix():
                        auto_gateway_from_ip()
                elif selected_field == 2 and use_dhcp:
                    status_msg = "Disabled in DHCP mode"
                    status_ok  = False
                else:
                    edit_octet_field(selected_field)
                if not (selected_field in (1, 2) and use_dhcp):
                    status_msg = ""

            elif key in (ord("a"), ord("A")):
                try:
                    apply_all_settings()
                    status_msg = "Applied settings successfully."
                    status_ok  = True
                except Exception as exc:
                    status_msg = f"Error: {exc}"
                    status_ok  = False

            elif key in (ord("r"), ord("R")):
                get_network_settings()
                status_msg = "Settings reloaded from system."
                status_ok  = True

            elif key in (ord("q"), ord("Q"), 27):  # q/Q/Esc
                break

    curses.wrapper(tui_main)


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
            "  --tui      TUI      – ncurses interface for SSH / serial console\n"
        ),
    )
    parser.add_argument(
        "--tui",
        action="store_true",
        help="Run in TUI (ncurses) mode instead of hardware mode.",
    )
    args = parser.parse_args()

    if args.tui:
        run_tui()
    else:
        run_hardware()
