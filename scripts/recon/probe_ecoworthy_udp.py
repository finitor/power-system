#!/usr/bin/env python3
import socket


HOSTS = ["192.168.0.201", "192.168.0.202"]
PORTS = [
    53,
    67,
    68,
    123,
    137,
    138,
    161,
    500,
    502,
    1900,
    5353,
    5683,
    6666,
    6667,
    6668,
    6669,
    8899,
    9999,
    10000,
    49152,
]


def probe(host, port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(0.7)
    try:
        sock.sendto(b"\x00", (host, port))
        data, addr = sock.recvfrom(2048)
        return f"response len={len(data)} from={addr} hex={data[:40].hex()}"
    except socket.timeout:
        return "no_response"
    except OSError as exc:
        return f"os_error errno={exc.errno} {exc.strerror}"
    finally:
        sock.close()


def main():
    for host in HOSTS:
        print(host)
        for port in PORTS:
            result = probe(host, port)
            if result != "no_response":
                print(f"  udp/{port}: {result}")


if __name__ == "__main__":
    main()
