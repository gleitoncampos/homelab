#!/usr/bin/env python3
"""Confere que os managers do Swarm escapam do redirecionamento de DNS.

O Traefik roda em manager (`node.role == manager`) e emite certificado por
DNS-01 na Cloudflare. O cliente ACME acha a zona pelo SOA, e o Unbound não o
devolve para o domínio do homelab (override curinga `*`): sem SOA, a zona
vira `dpdns.org` e a emissão falha. O compose já aponta o desafio para
1.1.1.1/1.0.0.1, mas o NAT de DNS forçado desviava a 53 da VLAN 20 de volta
ao Unbound (13/10/2026). No firewall antigo o nó do Traefik era isento; aqui
a isenção sai do inventário (`homelab_managers`), não de IP escrito à mão.
"""
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parents[1]
PB = RAIZ / "playbooks" / "network" / "01-opnsense-setup.yml"
falhas = []


def checar(condicao, msg):
    print(f"  {'OK' if condicao else 'FALHOU'} {msg}")
    if not condicao:
        falhas.append(msg)


with tempfile.TemporaryDirectory() as tmp:
    fixture = yaml.safe_load((RAIZ / "tests/fixtures/opnsense.yml").read_text(encoding="utf-8"))
    fixture["dns_redirect_bypass_ips"] = ["192.0.2.50"]
    fixture["_dns_bypass_managers"] = ["198.51.100.10"]
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
    raiz = ET.parse(saida).getroot()

alias = next((a for a in raiz.iter("alias") if a.findtext("name") == "DNS_REDIRECT_BYPASS"), None)
checar(alias is not None, "existe o alias DNS_REDIRECT_BYPASS")
if alias is not None:
    conteudo = set((alias.findtext("content") or "").split())
    checar("192.0.2.50" in conteudo, "mantém os IPs de dns_redirect_bypass_ips")
    checar("198.51.100.10" in conteudo, "inclui os managers do Swarm")

# A isenção do NAT sozinha não basta: a regra anti-DoH bloqueia QUALQUER porta
# para os IPs da DOH_list, e todo resolvedor público grande está nela
# (1.1.1.1, 8.8.8.8, 9.9.9.9...). Os isentos precisam de passagem na 53,
# antes do bloqueio, e só na 53 (13/10/2026).
regras = list(raiz.iter("rule"))
def _campo(r, *caminho):
    for c in caminho:
        v = r.findtext(c)
        if v is not None:
            return v
    return ""
passa = next((i for i, r in enumerate(regras)
              if _campo(r, "action", "type") == "pass"
              and "DNS_REDIRECT_BYPASS" in _campo(r, "source_net", "source/network", "source/address")
              and "DOH_list" in _campo(r, "destination_net", "destination/network", "destination/address")), None)
bloq = next((i for i, r in enumerate(regras) if r.get("uuid") == "ffec6963-1b39-4078-80a4-c866447be8f7"), None)
checar(passa is not None, "existe passagem DNS_REDIRECT_BYPASS -> DOH_list")
if passa is not None:
    r = regras[passa]
    checar(_campo(r, "destination_port", "destination/port") == "53", "a passagem é só para a porta 53")
    checar(bloq is not None and passa < bloq, "a passagem vem antes do bloqueio anti-DoH")
    if bloq is not None:
        checar(int(_campo(r, "sequence") or 0) <= int(_campo(regras[bloq], "sequence") or 0),
               "sequence da passagem <= sequence do bloqueio")

pb = PB.read_text(encoding="utf-8")
checar("_dns_bypass_managers" in pb and "homelab_managers" in pb,
       "o playbook monta _dns_bypass_managers a partir de homelab_managers")
checar(pb.find("_dns_bypass_managers") < pb.find("Aplicar novo arquivo /conf/config.xml"),
       "a lista é montada antes de aplicar o config.xml")

# O mesmo bloqueio mata o ping para 1.1.1.1/8.8.8.8, que é o sinal de
# internet do led-health do AP: com a internet no ar, o AP piscava "sem
# internet" (14/10/2026). Passagem só de ICMP echo request, antes do bloqueio.
ping = next((i for i, r in enumerate(regras)
             if _campo(r, "action", "type") == "pass"
             and _campo(r, "protocol").lower() == "icmp"
             and "DOH_list" in _campo(r, "destination_net", "destination/network", "destination/address")), None)
checar(ping is not None, "existe passagem ICMP -> DOH_list")
if ping is not None:
    r = regras[ping]
    checar(_campo(r, "icmptype") == "echoreq", "a passagem ICMP é só echo request")
    checar(bloq is not None and ping < bloq, "a passagem ICMP vem antes do bloqueio anti-DoH")
    if bloq is not None:
        checar(int(_campo(r, "sequence") or 0) <= int(_campo(regras[bloq], "sequence") or 0),
               "sequence da passagem ICMP <= sequence do bloqueio")

# DoT (TCP 853) bloqueado só na ENTRADA das VLANs não confiáveis (15/10/2026).
# Em floating, na WAN ou sem `direction: in`, a regra pegaria a saída DoT do
# próprio Unbound para os upstreams e o firewall ficaria sem DNS.
dot = next((r for r in regras if r.get("uuid") == "c3d9e1f0-5a6b-4c7d-8e9f-0a1b2c3d4e85"), None)
checar(dot is not None, "existe o bloqueio de DoT (TCP 853)")
if dot is not None:
    checar(_campo(dot, "action", "type") == "block", "DoT: action block")
    checar(_campo(dot, "protocol") == "tcp", "DoT: protocolo tcp")
    checar(_campo(dot, "interface") == "vlan40,vlan50", "DoT: só nas VLANs 40 e 50")
    checar(_campo(dot, "direction") == "in", "DoT: direction in (não pega a saída do firewall)")
    checar(_campo(dot, "floating") in ("", "0"), "DoT: não é floating")
    porta = next((a for a in raiz.iter("alias") if a.findtext("name") == _campo(dot, "destination_port")), None)
    checar(porta is not None and (porta.findtext("content") or "").split() == ["853"],
           "DoT: destino é um alias só com a 853")

if falhas:
    sys.exit(f"\n✗ render-opnsense-dns-bypass: {len(falhas)} falha(s)")
print("\n✓ render-opnsense-dns-bypass")
