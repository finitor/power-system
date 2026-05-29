"""Command-line probe for USB SocketCAN adapters."""

from __future__ import annotations

import argparse

from offgrid_power.canbus import interface_state, socketcan_interfaces, stm32_dfu_devices


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe a USB SocketCAN adapter.")
    parser.add_argument("--interface", default="can0", help="SocketCAN interface to open")
    parser.add_argument("--receive-seconds", type=float, default=2.0, help="Seconds to wait for one CAN frame")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    interfaces = socketcan_interfaces()
    print(f"SocketCAN interfaces: {', '.join(interfaces) if interfaces else 'none'}")

    if args.interface not in interfaces:
        dfu_devices = stm32_dfu_devices()
        for device in dfu_devices:
            product = f" {device.product}" if device.product else ""
            serial = f" serial={device.serial}" if device.serial else ""
            print(f"STM32 DFU device present:{product}{serial} ({device.vendor_id}:{device.product_id})")
        if dfu_devices:
            print("Adapter is in DFU/bootloader mode, not SocketCAN mode.")
        else:
            print(f"Interface {args.interface} is not present.")
        return 1

    state = interface_state(args.interface)
    if state == "down":
        print(f"Interface {args.interface} is down.")
        print(f"Bring it up with: sudo ip link set {args.interface} up type can bitrate 500000")
        return 1

    try:
        import can
    except ImportError as exc:
        raise SystemExit("Install python-can with: python -m pip install '.[can]'") from exc

    with can.Bus(interface="socketcan", channel=args.interface) as bus:
        print(f"Opened {args.interface}; waiting {args.receive_seconds:.1f}s for a CAN frame...")
        message = bus.recv(timeout=args.receive_seconds)

    if message is None:
        print("No CAN frames received. This is expected if nothing is connected downstream.")
    else:
        print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
