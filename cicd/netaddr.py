"""Địa chỉ IP của máy và các URL để mở CICD App."""
import socket

import psutil


def lan_ips() -> list[str]:
    """Các địa chỉ IPv4 của máy (mọi card mạng: LAN, Wi-Fi, VPN...), bỏ 127.x và 169.254.x."""
    ips = []
    for addrs in psutil.net_if_addrs().values():
        for a in addrs:
            if a.family == socket.AF_INET and not a.address.startswith(("127.", "169.254.")) and a.address not in ips:
                ips.append(a.address)
    return sorted(ips, key=lambda ip: tuple(int(x) for x in ip.split(".")))


def access_urls(host: str | None, port: int) -> list[str]:
    """URL để mở web. host 0.0.0.0 = lắng nghe trên mọi địa chỉ -> 127.0.0.1 + từng IP của máy."""
    if host in (None, "", "0.0.0.0"):
        return [f"http://127.0.0.1:{port}"] + [f"http://{ip}:{port}" for ip in lan_ips()]
    return [f"http://{host}:{port}"]


def is_loopback(addr: str | None) -> bool:
    return bool(addr) and (addr.startswith("127.") or addr in ("::1", "localhost") or addr.startswith("::ffff:127."))
