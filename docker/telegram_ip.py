#!/usr/bin/env python3
"""Пишет в /etc/hosts первый доступный IPv4 api.telegram.org.

DNS этой машины часто отдаёт адрес, на котором новый TCP:443 зависает.
Уже установленное соединение при этом может жить дальше.
"""

from __future__ import annotations

import os
import socket
import sys

FALLBACK_IPS = (
    "149.154.167.220",
    "149.154.167.51",
    "149.154.175.100",
    "149.154.166.110",
)


def candidates() -> list[str]:
    found: list[str] = []
    try:
        for item in socket.getaddrinfo(
            "api.telegram.org", 443, socket.AF_INET, socket.SOCK_STREAM
        ):
            ip = item[4][0]
            if ip not in found:
                found.append(ip)
    except OSError:
        pass
    for ip in FALLBACK_IPS:
        if ip not in found:
            found.append(ip)
    return found


def reachable(ip: str) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(3)
    try:
        sock.connect((ip, 443))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def pin(ip: str) -> None:
    path = "/etc/hosts"
    with open(path, encoding="utf-8") as fh:
        lines = [line for line in fh.read().splitlines() if "api.telegram.org" not in line]
    lines.append(f"{ip} api.telegram.org")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def main() -> None:
    chosen = next((ip for ip in candidates() if reachable(ip)), None)
    if chosen:
        pin(chosen)
        print(f"api.telegram.org -> {chosen}", flush=True)
    else:
        print("api.telegram.org: доступный адрес не найден, остаётся DNS", flush=True)
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
