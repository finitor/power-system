#!/usr/bin/env python3
import concurrent.futures
import socket
import sys
import time


HOSTS = ["192.168.0.201", "192.168.0.202"]
PORTS = range(1, 65536)
TIMEOUT = 0.35
WORKERS = 120


def check(host, port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(TIMEOUT)
    try:
        return port if sock.connect_ex((host, port)) == 0 else None
    finally:
        sock.close()


def scan(host):
    started = time.time()
    open_ports = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for result in pool.map(lambda port: check(host, port), PORTS):
            if result is not None:
                open_ports.append(result)

    elapsed = time.time() - started
    print(f"{host}: open_tcp_ports={open_ports} elapsed={elapsed:.1f}s", flush=True)


def main():
    for host in HOSTS:
        try:
            scan(host)
        except Exception as exc:
            print(f"{host}: scan_failed={exc!r}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
