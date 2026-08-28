#!/usr/bin/env python3
"""Run a manager command through local Clash HTTP CONNECT tunnels."""

from __future__ import annotations

import argparse
import base64
import os
import select
import socket
import socketserver
import subprocess
import sys
import threading
from dataclasses import dataclass
from typing import Sequence
from urllib.parse import SplitResult, urlsplit, urlunsplit

from manager_api.config import get_settings


@dataclass(frozen=True)
class ProxyEndpoint:
    """Describe the local HTTP CONNECT proxy without retaining a full URL."""

    host: str
    port: int
    authorization: str | None = None


@dataclass(frozen=True)
class TunnelTarget:
    """Describe one remote TCP endpoint exposed on a local loopback port."""

    host: str
    port: int
    local_port: int
    name: str


class ClashTunnelServer(socketserver.ThreadingTCPServer):
    """Forward a local TCP listener to one remote target through Clash."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, target: TunnelTarget, proxy: ProxyEndpoint) -> None:
        self.target = target
        self.proxy = proxy
        super().__init__(("127.0.0.1", target.local_port), ClashTunnelHandler)


class ClashTunnelHandler(socketserver.BaseRequestHandler):
    """Bridge one client socket to a Clash CONNECT tunnel."""

    server: ClashTunnelServer

    def handle(self) -> None:
        """Open CONNECT once and relay bytes until either endpoint closes."""
        upstream = socket.create_connection(
            (self.server.proxy.host, self.server.proxy.port),
            timeout=15,
        )
        try:
            response_tail = self._open_connect(upstream)
            if response_tail:
                self.request.sendall(response_tail)
            self._relay(self.request, upstream)
        finally:
            upstream.close()

    def _open_connect(self, upstream: socket.socket) -> bytes:
        """Ask Clash to create a TCP tunnel and return bytes after its headers."""
        target = self.server.target
        headers = [
            f"CONNECT {target.host}:{target.port} HTTP/1.1",
            f"Host: {target.host}:{target.port}",
            "Proxy-Connection: Keep-Alive",
        ]
        if self.server.proxy.authorization is not None:
            headers.append(f"Proxy-Authorization: {self.server.proxy.authorization}")
        upstream.sendall(("\r\n".join(headers) + "\r\n\r\n").encode("ascii"))

        response = b""
        while b"\r\n\r\n" not in response:
            chunk = upstream.recv(4096)
            if not chunk:
                raise ConnectionError("Clash closed the CONNECT response")
            response += chunk
            if len(response) > 16384:
                raise ConnectionError("Clash CONNECT response exceeded 16 KiB")

        headers_blob, _, tail = response.partition(b"\r\n\r\n")
        status_line = headers_blob.split(b"\r\n", 1)[0]
        if b" 200 " not in status_line:
            raise ConnectionError(status_line.decode("latin1", "replace"))
        return tail

    @staticmethod
    def _relay(client: socket.socket, upstream: socket.socket) -> None:
        """双向透传数据库协议，不解码也不记录业务内容。"""
        sockets = [client, upstream]
        while sockets:
            readable, _, _ = select.select(sockets, [], [], 60)
            if not readable:
                raise TimeoutError("Clash tunnel idle timeout")
            for source in readable:
                payload = source.recv(65536)
                if not payload:
                    return
                destination = upstream if source is client else client
                destination.sendall(payload)


def parse_proxy(value: str) -> ProxyEndpoint:
    """Parse one HTTP proxy URL into the minimum connection fields."""
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("proxy must be an http:// or https:// URL with a host")

    authorization: str | None = None
    if parsed.username is not None:
        raw = f"{parsed.username}:{parsed.password or ''}".encode()
        authorization = "Basic " + base64.b64encode(raw).decode("ascii")

    return ProxyEndpoint(
        host=parsed.hostname,
        port=parsed.port or (443 if parsed.scheme == "https" else 80),
        authorization=authorization,
    )


def parse_target(url: str, *, local_port: int, name: str, default_port: int) -> TunnelTarget:
    """Extract the remote host and port from an existing manager connection URL."""
    parsed = urlsplit(url)
    if not parsed.hostname:
        raise ValueError(f"{name} URL is missing a hostname")
    return TunnelTarget(
        host=parsed.hostname,
        port=parsed.port or default_port,
        local_port=local_port,
        name=name,
    )


def localize_url(url: str, local_port: int) -> str:
    """Keep credentials/path intact while replacing only the transport endpoint."""
    parsed = urlsplit(url)
    credentials = parsed.netloc.rsplit("@", 1)[0] if "@" in parsed.netloc else ""
    netloc = f"{credentials + '@' if credentials else ''}127.0.0.1:{local_port}"
    rebuilt = SplitResult(
        scheme=parsed.scheme,
        netloc=netloc,
        path=parsed.path,
        query=parsed.query,
        fragment=parsed.fragment,
    )
    return urlunsplit(rebuilt)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """Parse the tunnel transport options and child command."""
    parser = argparse.ArgumentParser(
        description="Route one manager command through local Clash HTTP CONNECT tunnels.",
    )
    parser.add_argument(
        "--proxy",
        default="http://127.0.0.1:7890",
        help="Clash HTTP proxy URL (default: http://127.0.0.1:7890)",
    )
    parser.add_argument(
        "--postgres-port",
        type=int,
        default=15432,
        help="temporary local PostgreSQL listener (default: 15432)",
    )
    parser.add_argument(
        "--redis-port",
        type=int,
        default=16379,
        help="temporary local Redis listener (default: 16379)",
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="command to run after --",
    )
    args = parser.parse_args(argv)
    if args.command[:1] == ["--"]:
        args.command = args.command[1:]
    if not args.command:
        parser.error("provide a command after --")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    """Start both forwards, inject loopback URLs into the child process, and wait."""
    args = parse_args(argv or sys.argv[1:])
    settings = get_settings()
    proxy = parse_proxy(args.proxy)
    targets = (
        parse_target(
            settings.database_url,
            local_port=args.postgres_port,
            name="PostgreSQL",
            default_port=5432,
        ),
        parse_target(
            settings.redis_url,
            local_port=args.redis_port,
            name="Redis",
            default_port=6379,
        ),
    )
    servers = [ClashTunnelServer(target, proxy) for target in targets]
    threads = [
        threading.Thread(target=server.serve_forever, daemon=True)
        for server in servers
    ]
    for target, thread in zip(targets, threads):
        print(
            f"{target.name} 通过 Clash 映射到 127.0.0.1:{target.local_port}",
            flush=True,
        )
        thread.start()

    child_environment = {
        **dict(os.environ),
        "DATABASE_URL": localize_url(settings.database_url, args.postgres_port),
        "REDIS_URL": localize_url(settings.redis_url, args.redis_port),
    }
    try:
        return subprocess.run(args.command, env=child_environment, check=False).returncode
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
