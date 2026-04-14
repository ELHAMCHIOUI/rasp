#!/usr/bin/env python3
"""
G-5500 Rotator Serial Controller
Raspberry Pi → Arduino/G-5500 via UART
"""

import serial
import time
import sys
import threading

# ── Configuration ──────────────────────────────────────────────────────────────
SERIAL_PORT = "/dev/ttyUSB0"   # Change to /dev/ttyAMA0 for GPIO UART, or /dev/ttyUSB0 for USB-Serial
BAUD_RATE   = 9600             # Match your Arduino sketch baud rate
TIMEOUT     = 2                # Read timeout in seconds


def open_serial(port: str, baud: int) -> serial.Serial:
    """Open and return serial connection."""
    try:
        ser = serial.Serial(
            port=port,
            baudrate=baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=TIMEOUT
        )
        print(f"[OK] Connected to {port} @ {baud} baud")
        return ser
    except serial.SerialException as e:
        print(f"[ERROR] Cannot open {port}: {e}")
        print("  • Check port with: ls /dev/tty*")
        print("  • Try: sudo chmod 666 /dev/ttyUSB0")
        sys.exit(1)


def send_command(ser: serial.Serial, cmd: str) -> None:
    """Send a command string followed by newline."""
    payload = (cmd.strip() + "\n").encode("utf-8")
    ser.write(payload)
    print(f"  → Sent : {cmd.strip()}")


def read_response(ser: serial.Serial, delay: float = 0.3) -> str:
    """Read all available lines from serial buffer."""
    time.sleep(delay)
    lines = []
    while ser.in_waiting:
        line = ser.readline().decode("utf-8", errors="replace").strip()
        if line:
            lines.append(line)
            print(f"  ← Recv : {line}")
    return "\n".join(lines)


def listener_thread(ser: serial.Serial, stop_event: threading.Event) -> None:
    """Background thread: print anything the Arduino sends unsolicited."""
    while not stop_event.is_set():
        try:
            if ser.in_waiting:
                line = ser.readline().decode("utf-8", errors="replace").strip()
                if line:
                    print(f"\r  ← Recv : {line}\n> ", end="", flush=True)
            time.sleep(0.05)
        except serial.SerialException:
            break


def print_help() -> None:
    print("""
  Commands you can type:
  ─────────────────────────────────────────────────────
  L            Move LEFT
  R            Move RIGHT
  U            Move UP
  D            Move DOWN
  S            STOP all movement
  POS          Query current position
  SCAN         Start automatic sweep
  STOPSCAN     Stop automatic sweep
  GOTO:az:el   Go to azimuth/elevation  e.g.  GOTO:180:45
  ─────────────────────────────────────────────────────
  help         Show this help
  quit / exit  Close connection and exit
  ─────────────────────────────────────────────────────
""")


def interactive_loop(ser: serial.Serial) -> None:
    """Interactive REPL for sending commands."""
    stop_event = threading.Event()
    listener = threading.Thread(target=listener_thread, args=(ser, stop_event), daemon=True)
    listener.start()

    print_help()
    print("  G-5500 controller ready. Type a command:\n")

    try:
        while True:
            try:
                raw = input("> ").strip()
            except EOFError:
                break

            if not raw:
                continue

            cmd_up = raw.upper()

            if cmd_up in ("QUIT", "EXIT", "Q"):
                print("[INFO] Closing connection.")
                break

            if cmd_up == "HELP":
                print_help()
                continue

            # Validate known commands
            valid_prefixes = ("L", "R", "U", "D", "S", "POS", "SCAN", "STOPSCAN", "GOTO:")
            if not any(cmd_up.startswith(p) for p in valid_prefixes):
                print(f"  [WARN] Unknown command '{raw}'. Type 'help' for the list.")
                continue

            send_command(ser, raw)
            # Give the Arduino time to respond before next prompt
            time.sleep(0.4)

    except KeyboardInterrupt:
        print("\n[INFO] Interrupted by user.")
    finally:
        stop_event.set()
        ser.close()
        print("[INFO] Serial port closed.")


# ── Quick automated test sequence ──────────────────────────────────────────────
def run_test_sequence(ser: serial.Serial) -> None:
    """Send every command once to verify serial comms, then drop into REPL."""
    test_commands = [
        ("POS",         "Query initial position"),
        ("L",           "Move LEFT  for 1 s"),
        ("S",           "Stop"),
        ("R",           "Move RIGHT for 1 s"),
        ("S",           "Stop"),
        ("U",           "Move UP    for 1 s"),
        ("S",           "Stop"),
        ("D",           "Move DOWN  for 1 s"),
        ("S",           "Stop"),
        ("GOTO:180:45", "Go to AZ=180 EL=45"),
        ("POS",         "Check position after GOTO"),
        ("SCAN",        "Start sweep"),
        ("STOPSCAN",    "Stop sweep"),
    ]

    print("\n══ AUTO TEST SEQUENCE ════════════════════════════════════════\n")
    for cmd, description in test_commands:
        print(f"[TEST] {description}")
        send_command(ser, cmd)
        read_response(ser, delay=0.5)
        time.sleep(1.0)

    print("\n══ TEST COMPLETE — entering interactive mode ═════════════════\n")
    interactive_loop(ser)


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="G-5500 Serial Controller for Raspberry Pi")
    parser.add_argument("--port",  default=SERIAL_PORT, help=f"Serial port (default: {SERIAL_PORT})")
    parser.add_argument("--baud",  type=int, default=BAUD_RATE, help=f"Baud rate (default: {BAUD_RATE})")
    parser.add_argument("--test",  action="store_true", help="Run automated test sequence first")
    args = parser.parse_args()

    ser = open_serial(args.port, args.baud)
    time.sleep(2)          # Wait for Arduino reset after serial open
    read_response(ser)     # Flush the welcome banner

    if args.test:
        run_test_sequence(ser)
    else:
        interactive_loop(ser)
