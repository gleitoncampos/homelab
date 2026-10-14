#!/usr/bin/env python3
"""Confere o anti-portscan de IoT e visitantes no render do OPNsense.

A regra manual d9e8f7a6 era `block` em TCP de qualquer origem a qualquer
destino, com limite de taxa — mas limite de taxa só existe em regra de
PASSAGEM: no `block` o pf descarta as opções e sobra o bloqueio de todo o TCP.
IoT e visitantes ficaram sem internet (14/10/2026, pflog: rule 244). Agora o
template gera a passagem TCP limitada (sequence 55) a partir de
`opnsense_limite_conexoes`.
"""
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parents[1]
falhas = []


def checar(condicao, msg):
    print(f"  {'OK' if condicao else 'FALHOU'} {msg}")
    if not condicao:
        falhas.append(msg)


comum = yaml.safe_load((RAIZ / "inventories/_common/opnsense.yml").read_text(encoding="utf-8"))
limite = comum.get("opnsense_limite_conexoes") or {}
checar(isinstance(limite.get("conexoes"), int) and isinstance(limite.get("segundos"), int),
       "conexoes e segundos são inteiros (o modelo tem dois campos IntegerField)")
checar(not any(r.get("uuid") == "d9e8f7a6-b5c4-3210-9876-112233445566" for r in comum["opnsense_rules"]),
       "a regra manual d9e8f7a6 saiu")

with tempfile.TemporaryDirectory() as tmp:
    fixture = yaml.safe_load((RAIZ / "tests/fixtures/opnsense.yml").read_text(encoding="utf-8"))
    for tag in (40, 50):
        fixture["vlans"].append({
            "tag": tag, "name": f"V{tag}", "descr": f"vlan {tag}", "ip": f"198.51.{tag}.1",
            "subnet": 24, "dhcp_enabled": False,
            "politica": {"internet": True, "alcanca": [], "gerencia": False},
        })
    fx = Path(tmp) / "fixture.yml"
    fx.write_text(yaml.safe_dump(fixture), encoding="utf-8")
    saida = Path(tmp) / "config.xml"
    r = subprocess.run(
        [sys.executable, str(RAIZ / "tests" / "render_opnsense.py"),
         "--template", str(RAIZ / "templates/network/opnsense/config.xml.j2"),
         "--common", str(RAIZ / "inventories/_common/opnsense.yml"),
         "--fixture", str(fx), "--out", str(saida)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        sys.exit(f"✗ render falhou: {r.stderr.strip()}")
    regras = list(ET.parse(saida).getroot().iter("rule"))


def campo(r, nome):
    return (r.findtext(nome) or "").strip()


for tag in (40, 50):
    eu = f"vlan{tag}"
    minhas = [r for r in regras if eu in campo(r, "interface").split(",") and campo(r, "enabled") == "1"]
    tudo_tcp = [r for r in minhas if campo(r, "action") == "block" and campo(r, "protocol") == "tcp"
                and campo(r, "source_net") == "any" and campo(r, "destination_net") == "any"
                and not campo(r, "destination_port")]
    checar(not tudo_tcp, f"{eu}: nenhum bloqueio de todo o TCP")
    lim = [r for r in minhas if campo(r, "action") == "pass" and campo(r, "max-src-conn-rate")]
    checar(len(lim) == 1, f"{eu}: uma passagem com limite de taxa")
    if lim:
        r = lim[0]
        checar(campo(r, "protocol") == "tcp" and campo(r, "destination_net") == "any"
               and campo(r, "source_net") == eu,
               f"{eu}: a passagem limitada é TCP, da própria VLAN para any")
        checar(campo(r, "max-src-conn-rate") == str(limite["conexoes"])
               and campo(r, "max-src-conn-rates") == str(limite["segundos"]),
               f"{eu}: limite {limite['conexoes']}/{limite['segundos']}s")
        seq = int(campo(r, "sequence"))
        bloqueios_lab = [int(campo(x, "sequence")) for x in minhas
                         if campo(x, "action") == "block" and campo(x, "destination_net").startswith(("lan", "vlan"))]
        saida_geral = [int(campo(x, "sequence")) for x in minhas
                       if campo(x, "action") == "pass" and campo(x, "destination_net") == "any"
                       and campo(x, "protocol") == "any"]
        checar(bool(bloqueios_lab) and seq > max(bloqueios_lab),
               f"{eu}: a passagem limitada vem DEPOIS dos bloqueios ao lab (não abre rede interna)")
        checar(bool(saida_geral) and seq < min(saida_geral), f"{eu}: e antes da saída geral")

if falhas:
    sys.exit(f"\n✗ render-opnsense-antiportscan: {len(falhas)} falha(s)")
print("\n✓ render-opnsense-antiportscan")
