#!/usr/bin/env python3
"""network tool — read-only networking math & lookups (stdlib only).

Two sub-operations, a nod to the OSI Explorer project on the same site:
  - cidr : subnet arithmetic (network/broadcast/mask/host count/range)
  - dns  : resolve a hostname to A/AAAA addresses

Deliberately read-only and side-effect-free: `cidr` is pure arithmetic and
`dns` only performs name resolution (no sockets are opened to the target), so
there is no SSRF surface.
"""
import socket
import ipaddress

from ..registry import Tool


def _cidr(value):
    net = ipaddress.ip_network(value.strip(), strict=False)
    hosts = net.num_addresses
    usable = max(hosts - 2, 0) if net.version == 4 and net.prefixlen < 31 else hosts
    first, last = net[0], net[-1]
    return {
        "summary": "%s → network %s, broadcast %s, netmask %s, %d addresses (%d usable), range %s–%s" % (
            net.with_prefixlen, net.network_address,
            getattr(net, "broadcast_address", last), net.netmask,
            hosts, usable, first, last),
        "network": str(net.network_address),
        "netmask": str(net.netmask),
        "prefixlen": net.prefixlen,
        "num_addresses": hosts,
        "usable_hosts": usable,
    }


def _dns(host):
    host = host.strip()
    if not host or "/" in host or " " in host:
        return {"ok": False, "summary": "invalid hostname"}
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        return {"ok": False, "summary": "DNS lookup failed for %s (%s)" % (host, e)}
    addrs = sorted({i[4][0] for i in infos})
    v4 = [a for a in addrs if ":" not in a]
    v6 = [a for a in addrs if ":" in a]
    parts = []
    if v4:
        parts.append("A: " + ", ".join(v4))
    if v6:
        parts.append("AAAA: " + ", ".join(v6))
    return {"summary": "%s → %s" % (host, "; ".join(parts) or "no records"),
            "host": host, "a": v4, "aaaa": v6}


def _run(args):
    op = (args.get("operation") or "").lower().strip()
    if op == "cidr":
        return _cidr(args.get("value", ""))
    if op == "dns":
        return _dns(args.get("value", ""))
    return {"ok": False, "summary": "operation must be 'cidr' or 'dns'"}


TOOL = Tool(
    name="network",
    description=(
        "Networking helpers: 'cidr' for subnet math (network, broadcast, mask, "
        "host count, range) and 'dns' to resolve a hostname to IP addresses."
    ),
    args={
        "operation": "cidr | dns",
        "value": "for cidr: a network like 10.0.0.0/24 ; for dns: a hostname like example.com",
    },
    examples=[
        '{"tool":"network","args":{"operation":"cidr","value":"192.168.1.0/26"}}',
        '{"tool":"network","args":{"operation":"dns","value":"chrome.net.ua"}}',
    ],
    run=_run,
    terminal=True,
)
