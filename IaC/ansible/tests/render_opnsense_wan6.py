#!/usr/bin/env python3
"""Confere o modo IPv6 da WAN PPPoE no config.xml renderizado do OPNsense.

`pppoev6` só negocia IPV6CP: a pppoe0 fica com link-local e nada mais. No
26.7, `interface_dhcpv6_prepare` só põe no dhcp6c.conf interface `dhcp6` ou
`slaac`, e o `ppp-ipv6.php` só dispara DHCPv6 nesses dois modos — o gateway
v6 da WAN ficava down para sempre (13/10/2026). `dhcp6` sobre a PPPoE pede
endereço e prefixo delegado quando o link sobe.
"""
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]

with tempfile.TemporaryDirectory() as tmp:
    saida = Path(tmp) / "config.xml"
    r = subprocess.run(
        [sys.executable, str(RAIZ / "tests" / "render_opnsense.py"),
         "--template", str(RAIZ / "templates/network/opnsense/config.xml.j2"),
         "--common", str(RAIZ / "inventories/_common/opnsense.yml"),
         "--fixture", str(RAIZ / "tests/fixtures/opnsense.yml"),
         "--out", str(saida)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        sys.exit(f"✗ render falhou: {r.stderr.strip()}")
    wan = ET.parse(saida).getroot().find("./interfaces/wan")

falhas = []
for condicao, msg in (
    (wan.findtext("ipaddr") == "pppoe", "IPv4 da WAN em pppoe"),
    (wan.findtext("ipaddrv6") == "dhcp6", f"IPv6 da WAN em dhcp6 (veio {wan.findtext('ipaddrv6')})"),
    (wan.findtext("dhcp6-ia-pd-len") == "8", "pede prefixo delegado /56 (dhcp6-ia-pd-len=8)"),
):
    print(f"  {'OK' if condicao else 'FALHOU'} {msg}")
    if not condicao:
        falhas.append(msg)

if falhas:
    sys.exit(f"\n✗ render-opnsense-wan6: {len(falhas)} falha(s)")
print("\n✓ render-opnsense-wan6")
