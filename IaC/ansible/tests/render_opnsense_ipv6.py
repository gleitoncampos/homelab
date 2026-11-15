#!/usr/bin/env python3
"""Confere o IPv6 das VLANs no render do OPNsense (DECISÃO 021).

IPv6 é opt-in por VLAN (`ipv6_prefix_id`). O que precisa valer:
  - só a VLAN que opta recebe `track6` e regras `inet6`; as outras seguem só v4;
  - o v6 não vira desvio dos controles que existem só em v4: DNS externo fica
    fechado (o redirecionamento forçado é v4) e a gerência do firewall também;
  - o GUA é público: VLAN com v6 bloqueia as outras VLANs com v6 ANTES da saída;
  - NAT66 na CLARO (`opt1`) para a `lan` e para cada VLAN com v6, em `hybrid`;
  - o v6 default é a CLARO, e a WAN1 a contingência (gateway e grupo).
Lê a fixture de testes (VLAN 10 com gerência, VLAN 20 sem).
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


def campo(r, nome):
    return (r.findtext(nome) or "").strip()


fixture = yaml.safe_load((RAIZ / "tests/fixtures/opnsense.yml").read_text(encoding="utf-8"))
com_v6 = {v["tag"]: v for v in fixture["vlans"] if "ipv6_prefix_id" in v}
# Uma VLAN SEM v6 prova o opt-in; a fixture não traz, então entra aqui.
fixture["vlans"].append({
    "tag": 40, "name": "SEMV6", "descr": "sem v6", "ip": "198.51.40.1", "subnet": 24,
    "dhcp_enabled": False, "politica": {"internet": True, "alcanca": [], "gerencia": False},
})
checar(set(com_v6) == {10, 20}, "a fixture tem v6 nas VLANs 10 e 20 (ramos do gerador)")

with tempfile.TemporaryDirectory() as tmp:
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

ifaces = raiz.find("interfaces")
for tag in (10, 20, 40):
    el = ifaces.find(f"vlan{tag}")
    if tag in com_v6:
        checar(campo(el, "ipaddrv6") == "track6" and campo(el, "track6-interface") == "wan"
               and campo(el, "track6-prefix-id") == str(com_v6[tag]["ipv6_prefix_id"]),
               f"vlan{tag}: track6 na wan com o prefix-id do inventário")
    else:
        checar(el.find("ipaddrv6") is None, f"vlan{tag}: sem v6 (opt-in)")

regras = [x for x in raiz.iter("rule") if campo(x, "enabled") == "1" and campo(x, "ipprotocol") == "inet6"]


def v6(tag):
    return [x for x in regras if campo(x, "interface") == f"vlan{tag}"]


checar(not v6(40), "vlan40 (sem v6): nenhuma regra inet6 de política")
for tag in (10, 20):
    eu = f"vlan{tag}"
    minhas = v6(tag)
    dns_self = [x for x in minhas if campo(x, "action") == "pass" and campo(x, "destination_net") == "(self)"
                and campo(x, "destination_port") == "53" and campo(x, "protocol") == "tcp/udp"]
    checar(len(dns_self) == 1, f"{eu}: passagem do DNS (53) no próprio firewall")
    dns_ext = [x for x in minhas if campo(x, "action") == "block" and campo(x, "destination_net") == "any"
               and campo(x, "destination_port") == "53,853" and campo(x, "protocol") == "tcp/udp"]
    checar(len(dns_ext) == 1, f"{eu}: bloqueio de DNS/DoT/DoQ externo")
    saida_geral = [x for x in minhas if campo(x, "action") == "pass" and campo(x, "destination_net") == "any"
                   and campo(x, "protocol") == "any"]
    pol = next(v for v in fixture["vlans"] if v["tag"] == tag)["politica"]
    checar(len(saida_geral) == (1 if pol["internet"] else 0),
           f"{eu}: saída geral para a internet {'presente' if pol['internet'] else 'ausente (internet: false)'}")
    gerencia = [x for x in minhas if campo(x, "action") == "block" and campo(x, "destination_net") == "(self)"
                and campo(x, "destination_port") == "Management_Ports"]
    checar(len(gerencia) == (0 if pol["gerencia"] else 1),
           f"{eu}: bloqueio da gerência do firewall {'ausente (gerencia: true)' if pol['gerencia'] else 'presente'}")
    outras = [x for x in minhas if campo(x, "action") == "block" and campo(x, "destination_net").startswith("vlan")]
    esperado = sorted(f"vlan{t}" for t in com_v6 if t != tag)
    checar(sorted(campo(x, "destination_net") for x in outras) == esperado,
           f"{eu}: bloqueia as outras VLANs com v6 ({', '.join(esperado)})")
    if dns_self and dns_ext:
        checar(int(campo(dns_self[0], "sequence")) < int(campo(dns_ext[0], "sequence")),
               f"{eu}: DNS no firewall passa ANTES de fechar o externo")
    if saida_geral:
        s_out = int(campo(saida_geral[0], "sequence"))
        checar(int(campo(dns_ext[0], "sequence")) < s_out, f"{eu}: DNS externo fechado ANTES da saída")
        checar(bool(outras) and max(int(campo(x, "sequence")) for x in outras) < s_out,
               f"{eu}: bloqueio das outras VLANs vem ANTES da saída (GUA é público)")

# NAT66
ob = raiz.find("nat/outbound")
checar(ob is not None and campo(ob, "mode") == "hybrid", "NAT de saída em hybrid (as regras v4 automáticas ficam)")
nat6 = [x for x in (ob.findall("rule") if ob is not None else [])]
fontes = sorted(campo(x.find("source"), "network") for x in nat6)
checar(fontes == sorted(["lan"] + [f"vlan{t}" for t in com_v6]),
       f"NAT66 para a lan e para cada VLAN com v6 ({', '.join(fontes)})")
checar(all(campo(x, "interface") == "opt1" and campo(x, "ipprotocol") == "inet6" and x.find("target") is not None
           and campo(x, "target") == "" for x in nat6),
       "NAT66 sai pela CLARO (opt1), em inet6, com o endereço da interface (target vazio)")

# Gateways v6: a CLARO é a default
gw = {g.findtext("name"): g for g in raiz.iter("gateway_item")}
checar(campo(gw["CLARO_DHCP6"], "defaultgw") == "1" and campo(gw["CLARO_DHCP6"], "priority") == "1",
       "CLARO_DHCP6 é a default v6 (defaultgw 1, priority 1)")
checar(campo(gw["WAN_PPPOEV6"], "defaultgw") == "0" and campo(gw["WAN_PPPOEV6"], "priority") == "2",
       "WAN_PPPOEV6 é a contingência (defaultgw 0, priority 2)")
comum = yaml.safe_load((RAIZ / "inventories/_common/opnsense.yml").read_text(encoding="utf-8"))
grupo = next(g for g in comum["opnsense_gateway_groups"] if g["name"] == "WAN_IPV6")
tiers = {m["gateway"]: m["tier"] for m in grupo["membros"]}
checar(tiers == {"CLARO_DHCP6": 1, "WAN_PPPOEV6": 2}, "grupo WAN_IPV6: CLARO tier 1, WAN1 tier 2")

if falhas:
    sys.exit(f"\n✗ render-opnsense-ipv6: {len(falhas)} falha(s)")
print("\n✓ render-opnsense-ipv6")
