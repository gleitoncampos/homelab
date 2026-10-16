#!/usr/bin/env python3
"""Confere o DHCPv4 do Kea no config.xml renderizado do OPNsense.

Existe porque o OPNsense 26.7 tirou o ISC DHCP do núcleo (virou o plugin
`os-isc-dhcp`): o template gerava `<dhcpd>` e nada servia DHCP — Wi-Fi e LAN
sem endereço (13/10/2026). O Kea é quem serve agora, e este teste trava:

1. Nenhum `<dhcpd>` habilitado: instalar o plugin depois poria dois
   servidores na mesma rede.
2. Kea ligado nas interfaces certas: `lan` e toda VLAN com `dhcp_enabled`.
3. Uma `subnet4` por rede, com o CIDR da REDE (não o IP do gateway) e o pool
   do inventário.
4. `routers` e `domain_name_servers` ESCRITOS. O `option_data_autocollect`
   só preenche esses campos quando alguém salva pela GUI/API
   (`KeaDhcpv4::setNodes`); o IaC grava o config.xml direto, e sem eles o
   cliente recebe endereço sem gateway.
5. Reservas de `dhcp_static_maps` na subnet da LAN, ou na da VLAN de `rede`, por UUID.
"""
import copy
import ipaddress
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import jinja2
import yaml

RAIZ = Path(__file__).resolve().parents[1]
FIXTURE = RAIZ / "tests" / "fixtures" / "opnsense.yml"
COMMON = RAIZ / "inventories" / "_common" / "opnsense.yml"
TEMPLATE = RAIZ / "templates" / "network" / "opnsense" / "config.xml.j2"

falhas = []


def checar(condicao, mensagem):
    print(f"  {'OK' if condicao else 'FALHOU'} {mensagem}")
    if not condicao:
        falhas.append(mensagem)


with tempfile.TemporaryDirectory() as tmp:
    saida = Path(tmp) / "config.xml"
    r = subprocess.run(
        [sys.executable, str(RAIZ / "tests" / "render_opnsense.py"),
         "--template", str(TEMPLATE), "--common", str(COMMON),
         "--fixture", str(FIXTURE), "--out", str(saida)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        sys.exit(f"✗ render falhou: {r.stderr.strip()}")
    raiz = ET.parse(saida).getroot()

fixture = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
redes = [("lan", fixture["lan_ipaddr"], 24,
          fixture["lan_dhcp_range_from"], fixture["lan_dhcp_range_to"])]
redes += [
    (f"vlan{v['tag']}", v["ip"], int(v["subnet"]), v["dhcp_start"], v["dhcp_end"])
    for v in fixture["vlans"] if v.get("dhcp_enabled")
]

print("Invariante 1 — o ISC não está habilitado:")
ligados = [e for e in raiz.findall("./dhcpd/*") if (e.findtext("enable") or "") == "1"]
checar(not ligados, "nenhuma interface em <dhcpd> com enable=1")

dhcp4 = raiz.find("./OPNsense/Kea/dhcp4")
checar(dhcp4 is not None, "existe OPNsense/Kea/dhcp4")
if dhcp4 is None:
    sys.exit(f"\n✗ render-opnsense-kea: {len(falhas)} falha(s)")

print("\nInvariante 2 — Kea ligado nas interfaces com DHCP:")
checar(dhcp4.findtext("general/enabled") == "1", "dhcp4.general.enabled = 1")
interfaces = set(filter(None, (dhcp4.findtext("general/interfaces") or "").split(",")))
checar(interfaces == {r[0] for r in redes},
       f"interfaces = {sorted(r[0] for r in redes)} (veio {sorted(interfaces)})")

print("\nInvariante 3 e 4 — uma subnet4 por rede, com pool, gateway e DNS:")
subnets = {s.findtext("subnet"): s for s in dhcp4.findall("subnets/subnet4")}
checar(len(subnets) == len(redes), f"{len(redes)} subnet4 (veio {len(subnets)})")
uuids = [s.get("uuid") for s in dhcp4.findall("subnets/subnet4")]
checar(all(uuids) and len(set(uuids)) == len(uuids), "toda subnet4 tem uuid único")
for nome, ip, prefixo, inicio, fim in redes:
    cidr = str(ipaddress.ip_interface(f"{ip}/{prefixo}").network)
    s = subnets.get(cidr)
    checar(s is not None, f"{nome}: subnet4 {cidr}")
    if s is None:
        continue
    checar((s.findtext("pools") or "").strip() == f"{inicio}-{fim}", f"{nome}: pool {inicio}-{fim}")
    checar(s.findtext("option_data/routers") == ip, f"{nome}: routers = {ip}")
    checar(s.findtext("option_data/domain_name_servers") == ip, f"{nome}: DNS = {ip}")

print("\nInvariante 5 — reservas na subnet da LAN ou da VLAN de `rede`:")
cidr_por_rede = {nome: str(ipaddress.ip_interface(f"{ip}/{pref}").network)
                 for nome, ip, pref, _i, _f in redes}
reservas = dhcp4.findall("reservations/reservation")
mapas = fixture.get("dhcp_static_maps") or []
checar(len(reservas) == len(mapas), f"{len(mapas)} reservas (veio {len(reservas)})")
checar(any(m.get("rede", "lan") != "lan" for m in mapas), "a fixture tem reserva fora da LAN")
for m, r in zip(mapas, reservas):
    rede = m.get("rede", "lan")
    cidr = cidr_por_rede.get(rede)
    esperado = subnets[cidr].get("uuid") if cidr in subnets else None
    checar(esperado is not None and r.findtext("subnet") == esperado,
           f"{m['hostname']}: subnet = uuid da {rede}")
    checar(cidr is not None and ipaddress.ip_address(m["ipaddr"]) in ipaddress.ip_network(cidr),
           f"{m['hostname']}: ip dentro de {cidr}")
    checar(r.findtext("ip_address") == m["ipaddr"], f"{m['hostname']}: ip {m['ipaddr']}")
    checar(r.findtext("hw_address") == m["mac"], f"{m['hostname']}: mac {m['mac']}")

print("\nInvariante 6 — o playbook gera a config do Kea e sobe o serviço:")
# `rc.reload_all` não toca no Kea: `/etc/rc.conf.d/kea` ficava com
# `kea_enable="NO"` e o kea-dhcp4.conf era o .sample do pacote (13/10/2026).
# É o template `OPNsense/Kea` que escreve `kea_enable` e o `kea_setup`
# (`pluginctl -c kea_sync`, que gera o kea-dhcp4.conf) — o mesmo par que o
# reconfigure da GUI roda.
pb = (RAIZ / "playbooks" / "network" / "01-opnsense-setup.yml").read_text(encoding="utf-8")
p_reload = pb.find("/usr/local/etc/rc.reload_all")
p_tpl = pb.find("configctl template reload OPNsense/Kea")
p_kea = pb.find("configctl kea restart")
checar(p_tpl != -1, "o playbook recarrega o template OPNsense/Kea")
checar(p_kea != -1, "o playbook reinicia o Kea")
checar(-1 not in (p_reload, p_tpl, p_kea) and p_reload < p_tpl < p_kea,
       "ordem: rc.reload_all → template reload → kea restart")

print("\nInvariante 7 — o rc.reload_all roda fora da sessão SSH e o play reconecta:")
# Toda execução de 13/10/2026 perdeu a sessão no meio do `rc.reload_all`: o
# estado dela some do pf, o resto do fluxo cai no block padrão (`block drop
# in ... inet all`) e o sshd registra `Read error ... Operation timed out`.
# As tasks seguintes — inclusive a do Kea — nunca rodavam. O reload vai
# destacado (`daemon`) e o play só segue por conexão NOVA.
# pidfile POR EXECUÇÃO: um reload anterior preso (o php não sai com o Kea
# ligado) segura o pidfile fixo e o `daemon` recusa com rc=3 (13/10/2026).
p_daemon = pb.find('daemon -f -p "{{ _reload_pidfile }}" /usr/local/etc/rc.reload_all')
checar("_reload_pidfile: \"/var/run/iac-reload_all.{{ now(" in pb, "o pidfile do reload é único por execução")
p_reset = pb.find("meta: reset_connection", max(p_daemon, 0))
p_espera = pb.find("wait_for_connection", max(p_daemon, 0))
checar(p_daemon != -1, "o rc.reload_all é disparado com `daemon`, fora da sessão")
checar("command: /usr/local/etc/rc.reload_all" not in pb, "nenhuma task roda o rc.reload_all preso à sessão")
checar(-1 not in (p_daemon, p_reset, p_espera, p_kea) and p_daemon < p_reset < p_espera < p_kea,
       "ordem: daemon → reset_connection → wait_for_connection → Kea")

print("\nInvariante 8 — o pre_task recusa reserva que não cabe na rede:")
# O `isIPInCIDR` do modelo do OPNsense não roda com o config.xml gravado
# direto: `rede` errada ou IP fora rendiam reserva órfã sem erro (16/10/2026).
# Avalia a expressão REAL do playbook com a fixture e com mutações dela.
GATE_RESERVA = "Abortar se uma reserva de DHCP não cabe na rede declarada"
play = yaml.safe_load(pb)[0]
gate = next((t for t in play.get("pre_tasks", []) if t.get("name") == GATE_RESERVA), None)
checar(gate is not None, f"pre_task '{GATE_RESERVA}' existe")
if gate is not None:
    checar(gate["ansible.builtin.assert"]["that"] == ["_reservas_erros | trim | length == 0"],
           "o assert exige `_reservas_erros` vazio")
    expr = jinja2.Environment().from_string(gate["vars"]["_reservas_erros"])

    def erros(dado):
        return expr.render(**dado).strip()

    checar(erros(fixture) == "", f"fixture passa (veio {erros(fixture)!r})")
    mutacoes = {
        "rede inexistente": lambda d: d["dhcp_static_maps"][2].update(rede="vlan99"),
        "VLAN sem DHCP": lambda d: d["dhcp_static_maps"][2].update(rede="vlan10"),
        "IP fora da VLAN": lambda d: d["dhcp_static_maps"][2].update(ipaddr="192.0.2.10"),
        "IP fora da LAN": lambda d: d["dhcp_static_maps"][0].update(ipaddr="203.0.113.11"),
        "MAC repetido": lambda d: d["dhcp_static_maps"][1].update(
            mac=d["dhcp_static_maps"][0]["mac"].upper()),
        "IP repetido": lambda d: d["dhcp_static_maps"][1].update(
            ipaddr=d["dhcp_static_maps"][0]["ipaddr"]),
        "IP com letra": lambda d: d["dhcp_static_maps"][0].update(ipaddr="192.0.2.2O1"),
        "IP com zero à esquerda": lambda d: d["dhcp_static_maps"][0].update(ipaddr="192.0.2.0201"),
        "IP com 3 octetos": lambda d: d["dhcp_static_maps"][0].update(ipaddr="192.0.2"),
        "MAC inválido": lambda d: d["dhcp_static_maps"][0].update(mac="zz:zz"),
        "MAC com hífen": lambda d: d["dhcp_static_maps"][0].update(mac="00-00-5e-00-53-01"),
        "IP do gateway": lambda d: d["dhcp_static_maps"][0].update(ipaddr="192.0.2.1"),
        "endereço de rede": lambda d: d["dhcp_static_maps"][0].update(ipaddr="192.0.2.0"),
        "broadcast": lambda d: d["dhcp_static_maps"][0].update(ipaddr="192.0.2.255"),
    }
    for nome, mutar in mutacoes.items():
        dado = copy.deepcopy(fixture)
        mutar(dado)
        checar(erros(dado) != "", f"{nome}: recusado ({erros(dado)!r})")

if falhas:
    print(f"\n✗ render-opnsense-kea: {len(falhas)} falha(s)")
    sys.exit(1)
print("\n✓ render-opnsense-kea")
