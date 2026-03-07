import time
import subprocess
import RPi.GPIO as GPIO
from RPLCD.i2c import CharLCD

# Rotary encoder pins
CLK = 17
DT  = 27
SW  = 22

# LCD configuration (16 columns, 2 rows)
lcd = CharLCD('PCF8574', 0x27, cols=16, rows=2)

# Network settings – populated at startup from nmcli
ip_octets      = [192, 168, 1,   1]
subnet_prefix  = 16                    # CIDR prefix length (0-32)
gateway_octets = [192, 168, 1,   1]
dns_octets     = [1,   1,   1,   1]

# Main-menu options
MENU_OPTIONS = ["IP address", "Subnet prefix", "Gateway", "DNS"]
MENU_COUNT   = len(MENU_OPTIONS)

# mode: 'menu' | 'edit'
mode        = 'menu'
menu_index  = 0   # highlighted option in menu
edit_field  = 0   # 0=IP, 1=Subnet prefix, 2=Gateway, 3=DNS
state_index = 0   # 0-3 while editing octets

# Rotary encoder pulse accumulator (4 quadrature edges = 1 detent)
_encoder_pulses  = 0
_PULSES_PER_STEP = 2


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def octets_str(octs):
    return ".".join(str(o) for o in octs)


def current_octets():
    """Return the octet list for the field currently being edited."""
    if edit_field == 0:
        return ip_octets
    elif edit_field == 2:
        return gateway_octets
    else:  # 3 = DNS
        return dns_octets


def prefix_to_octets(prefix_len):
    """Convert a CIDR prefix length (0-32) to a 4-element subnet-mask list."""
    prefix_len = max(0, min(32, prefix_len))
    mask = (0xFFFFFFFF << (32 - prefix_len)) & 0xFFFFFFFF
    return [(mask >> (8 * i)) & 0xFF for i in reversed(range(4))]


# ---------------------------------------------------------------------------
# nmcli helpers
# ---------------------------------------------------------------------------

def get_network_settings():
    """Populate ip_octets, subnet_prefix, gateway_octets, dns_octets from nmcli/resolved."""
    global subnet_prefix
    # IP + prefix length
    try:
        result = subprocess.run(
            ["nmcli", "-g", "IP4.ADDRESS", "connection", "show", "static-end0"],
            capture_output=True, text=True, check=True,
        )
        addr = result.stdout.strip().split("\n")[0]  # e.g. '192.168.1.50/16'
        if addr and "/" in addr:
            ip_part, prefix_part = addr.split("/")
            ip_octets[:]  = [int(x) for x in ip_part.split(".")]
            subnet_prefix = int(prefix_part)
    except Exception:
        pass

    # Gateway
    try:
        result = subprocess.run(
            ["nmcli", "-g", "IP4.GATEWAY", "connection", "show", "static-end0"],
            capture_output=True, text=True, check=True,
        )
        gw = result.stdout.strip()
        if gw and gw != "--":
            gateway_octets[:] = [int(x) for x in gw.split(".")]
    except Exception:
        pass

    # DNS – read from /etc/systemd/resolved.conf
    try:
        with open("/etc/systemd/resolved.conf") as f:
            for line in f:
                line = line.strip()
                if line.startswith("DNS="):
                    dns_str = line.split("=", 1)[1].split()[0]  # first server only
                    dns_octets[:] = [int(x) for x in dns_str.split(".")]
                    break
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def write_row(row, text):
    lcd.cursor_pos = (row, 0)
    lcd.write_string(f"{text:<16}")


def update_display():
    lcd.clear()
    time.sleep(0.002)  # HD44780 needs ~1.52 ms to process clear before accepting writes
    if mode == 'menu':
        write_row(0, "Select field:")
        write_row(1, f"> {MENU_OPTIONS[menu_index]}")
    else:  # edit
        labels = {0: "Edit IP", 1: "Edit prefix", 2: "Edit GW", 3: "Edit DNS"}
        write_row(0, labels[edit_field])
        if edit_field == 1:  # subnet prefix
            write_row(1, f"/{subnet_prefix}")
        else:
            parts = [str(o) for o in current_octets()]
            parts[state_index] = f">{parts[state_index]}"
            write_row(1, ".".join(parts))


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

def apply_settings():
    lcd.clear()
    lcd.write_string("Applying...     ")

    try:
        if edit_field == 0:
            cidr = f"{octets_str(ip_octets)}/{subnet_prefix}"
            subprocess.run(
                ["nmcli", "connection", "modify", "static-end0",
                 "ipv4.addresses", cidr],
                check=True,
            )
        elif edit_field == 1:  # subnet prefix
            cidr = f"{octets_str(ip_octets)}/{subnet_prefix}"
            subprocess.run(
                ["nmcli", "connection", "modify", "static-end0",
                 "ipv4.addresses", cidr],
                check=True,
            )
        elif edit_field == 2:  # Gateway
            subprocess.run(
                ["nmcli", "connection", "modify", "static-end0",
                 "ipv4.gateway", octets_str(gateway_octets)],
                check=True,
            )
        else:  # DNS
            dns_str = octets_str(dns_octets)
            conf_lines = []
            try:
                with open("/etc/systemd/resolved.conf") as f:
                    conf_lines = f.readlines()
            except FileNotFoundError:
                pass
            # Replace or append the DNS= line under [Resolve]
            in_resolve = False
            dns_written = False
            new_lines = []
            for line in conf_lines:
                if line.strip() == "[Resolve]":
                    in_resolve = True
                if in_resolve and line.strip().startswith("DNS="):
                    new_lines.append(f"DNS={dns_str}\n")
                    dns_written = True
                else:
                    new_lines.append(line)
            if not dns_written:
                if not any(l.strip() == "[Resolve]" for l in new_lines):
                    new_lines.insert(0, "[Resolve]\n")
                new_lines.append(f"DNS={dns_str}\n")
            with open("/etc/systemd/resolved.conf", "w") as f:
                f.writelines(new_lines)
            subprocess.run(["systemctl", "restart", "systemd-resolved"], check=True)
            # DNS changes don't need nmcli reconnect
            lcd.clear()
            lcd.write_string("Done!           ")
            time.sleep(2)
            return

        subprocess.run(["nmcli", "con", "up", "static-end0"], check=True)

        lcd.clear()
        lcd.write_string("Done!           ")
        time.sleep(2)

    except subprocess.CalledProcessError as e:
        print(f"nmcli error: {e}")
        lcd.clear()
        lcd.write_string("nmcli error!    ")
        time.sleep(3)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    global mode, menu_index, edit_field, state_index, _encoder_pulses, subnet_prefix

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
    DISPLAY_DELAY    = 0.05  # 50 ms – wait for encoder to settle before refreshing LCD

    try:
        while True:
            current_A = GPIO.input(CLK)
            current_B = GPIO.input(DT)
            sw_state  = GPIO.input(SW)

            # --- Rotary encoder: full quadrature decoding (A_RISE/FALL, B_RISE/FALL) ---
            if current_A == 1 and last_A == 0:        # A_RISE
                _encoder_pulses += 1 if current_B == 0 else -1
            elif current_A == 0 and last_A == 1:      # A_FALL
                _encoder_pulses += 1 if current_B == 1 else -1

            if current_B == 1 and last_B == 0:        # B_RISE
                _encoder_pulses += 1 if current_A == 1 else -1
            elif current_B == 0 and last_B == 1:      # B_FALL
                _encoder_pulses += 1 if current_A == 0 else -1

            # Act once per full detent
            if abs(_encoder_pulses) >= _PULSES_PER_STEP:
                direction       = 1 if _encoder_pulses > 0 else -1
                _encoder_pulses = 0

                if mode == 'menu':
                    menu_index = (menu_index + direction) % MENU_COUNT
                    print(f"Menu: {MENU_OPTIONS[menu_index]}")
                else:  # edit
                    if edit_field == 1:  # subnet prefix
                        subnet_prefix = max(0, min(32, subnet_prefix + direction))
                        print(f"Subnet prefix: /{subnet_prefix}")
                    else:
                        octs = current_octets()
                        octs[state_index] = (octs[state_index] + direction) % 256
                        print(f"Octet {state_index + 1}: {octs[state_index]}")

                display_dirty    = True
                last_change_time = time.monotonic()

            # --- Button press ---
            if sw_state != last_sw_state and sw_state == 0:
                if mode == 'menu':
                    get_network_settings()
                    edit_field  = menu_index
                    state_index = 0
                    mode        = 'edit'
                    print(f"Editing: {MENU_OPTIONS[edit_field]}")

                else:  # edit
                    if edit_field == 1 or state_index == 3:
                        # On last step – apply directly
                        print(f"Applying: {MENU_OPTIONS[edit_field]}")
                        apply_settings()
                        mode = 'menu'
                        print("Back to menu")
                    else:
                        state_index += 1
                        print(f"Editing octet {state_index + 1}")

                display_dirty    = True
                last_change_time = time.monotonic()

            # --- Deferred display refresh (avoid blocking I2C during fast turns) ---
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


if __name__ == "__main__":
    main()
