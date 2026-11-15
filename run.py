#!/usr/bin/env python3
"""Khởi động CICD App (chạy ở cửa sổ hiện tại, Ctrl+C để dừng).

    python run.py                 # cổng mặc định 8088 (hoặc biến môi trường CICD_PORT)
    python run.py 8888            # cổng 8888
    python run.py --port 8888     # tương tự; cũng nhận -port 8888, -p 8888, --port=8888
    python run.py 8888 --host 127.0.0.1 # chỉ cho chính máy này truy cập (mặc định: mọi địa chỉ IP của máy)
"""
import argparse
import os
import sys


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"cổng phải là số, nhận được '{value}'")
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError(f"cổng phải từ 1 đến 65535, nhận được {port}")
    return port


def parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="run.py", description="Khởi động CICD App")
    ap.add_argument("port_pos", nargs="?", type=_port, metavar="PORT", help="cổng (mặc định 8088)")
    ap.add_argument("--port", "-port", "-p", type=_port, dest="port", help="cổng (mặc định 8088)")
    ap.add_argument("--host", "-host", dest="host", help="địa chỉ lắng nghe (mặc định 0.0.0.0 = mọi địa chỉ IP của máy; 127.0.0.1 = chỉ máy này)")
    args = ap.parse_args(argv)
    if args.port and args.port_pos and args.port != args.port_pos:
        ap.error(f"hai cổng khác nhau: {args.port_pos} và --port {args.port}")
    args.port = args.port or args.port_pos
    return args


if __name__ == "__main__":
    args = parse_args(sys.argv[1:])
    # Đặt trước khi import cicd: cicd.config đọc CICD_PORT / CICD_HOST lúc import
    if args.port:
        os.environ["CICD_PORT"] = str(args.port)
    if args.host:
        os.environ["CICD_HOST"] = args.host
    from cicd.web import main

    main()
