#!/usr/bin/env python3
"""Renderiza a rede do AP OpenWrt e trava as invariantes que trancam o
equipamento para fora.

Motivo: nenhum linter olha `.j2`, e o modo de falha aqui não é um erro — é um
AP que some da rede e só volta por console físico. Três invariantes, todas
verificadas contra a fonte do OpenWrt e não contra suposição:

1. **O nome da interface de gerência bate entre os dois templates.** O
   `openwrt_system.j2` escreve `option Interface 'vlanN'` no dropbear, e o
   `openwrt_network.j2` cria a interface. Se os dois discordarem,
   `/etc/init.d/dropbear:145-152` faz `return 1` ANTES do
   `procd_open_instance` — nenhuma instância nasce e não há listener em porta
   nenhuma. Não é "ignora e escuta em tudo". É a invariante mais cara da base.

2. **Toda VLAN do trunk sai tagueada (`:t`).** Uma VLAN untagged no trunk
   entrega o tráfego no domínio errado em silêncio.

3. **O failsafe é armado ANTES de a rede ser escrita, no playbook.** Armar
   depois é o mesmo que não armar: a janela em que o equipamento pode sumir
   começa na escrita. Esta afirmação lê o PLAYBOOK, não uma cópia da lógica —
   um teste que reimplementa a ordem passaria com o playbook errado.
"""
import re
import sys
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

RAIZ = Path(__file__).resolve().parents[3]
TEMPLATES = RAIZ / "IaC" / "ansible" / "templates" / "network"
PLAYBOOK = RAIZ / "IaC" / "ansible" / "playbooks" / "network" / "02-openwrt-setup.yml"

# Faixas de documentação (RFC 5737) — nunca dado real.
CONTEXTO = {
    "openwrt_uplink_port": "wan",
    "openwrt_unused_ports": ["lan2", "lan3"],
    "openwrt_trunk_vlans": [10, 30, 40, 50, 60],
    "openwrt_mgmt_vlan": 10,
    "openwrt_mgmt_ip": "192.0.2.2",
    "openwrt_mgmt_netmask": "255.255.255.0",
    "openwrt_mgmt_gateway": "192.0.2.1",
    "openwrt_mgmt_dns": ["192.0.2.1"],
    "openwrt_ula_prefix": "fd00:db8:1::/48",
    "openwrt_ssh_port": "2222",
    "openwrt_hostname": "ap-exemplo",
    "syslog_server_ip": "192.0.2.10",
    "syslog_server_port": "1514",
    "syslog_server_port_rfc3164": "1515",
    "network_timezone": "America/Sao_Paulo",
}

falhas = []


def checar(condicao, mensagem):
    if condicao:
        print(f"  OK {mensagem}")
    else:
        print(f"  FALHOU {mensagem}")
        falhas.append(mensagem)


env = Environment(
    loader=FileSystemLoader(str(TEMPLATES)),
    undefined=StrictUndefined,
    trim_blocks=True,
    keep_trailing_newline=True,
)
rede = env.get_template("openwrt_network.j2").render(**CONTEXTO)
sistema = env.get_template("openwrt_system.j2").render(**CONTEXTO)

print("Invariante 1 — o dropbear aponta para uma interface que existe:")
m = re.search(r"option Interface '([^']+)'", sistema)
checar(m is not None, "o system declara uma Interface para o dropbear")
if m:
    iface = m.group(1)
    checar(
        f"config interface '{iface}'" in rede,
        f"o network cria a interface '{iface}' que o dropbear exige",
    )

print("\nInvariante 2 — o trunk entrega tudo tagueado:")
for tag in CONTEXTO["openwrt_trunk_vlans"]:
    bloco = re.search(
        rf"config bridge-vlan\n\toption device 'br-lan'\n\toption vlan '{tag}'\n\tlist ports '([^']+)'",
        rede,
    )
    checar(bloco is not None, f"a VLAN {tag} tem seção bridge-vlan")
    if bloco:
        checar(
            bloco.group(1).endswith(":t"),
            f"a VLAN {tag} sai tagueada no trunk ({bloco.group(1)})",
        )

print("\nInvariante 3 — as portas não usadas ficam fora do bridge:")
portas = re.findall(r"config device\n\toption name 'br-lan'.*?\n\n", rede, re.S)
checar(len(portas) == 1, "existe exatamente um bloco de bridge br-lan")
if portas:
    for p in CONTEXTO["openwrt_unused_ports"]:
        checar(
            f"list ports '{p}'" not in portas[0],
            f"a porta {p} não entra no bridge",
        )

print("\nInvariante 4 — o playbook arma o failsafe ANTES de escrever a rede:")
texto = PLAYBOOK.read_text(encoding="utf-8")
pos_armar = texto.find("Armar o failsafe de rede do OpenWrt")
pos_rede = texto.find("Aplicar arquivo de Rede padronizado")
pos_backup = texto.find("Fazer backup persistente das configurações")
checar(pos_armar != -1, "a tarefa que arma o failsafe existe")
checar(pos_rede != -1, "a tarefa que escreve /etc/config/network existe")
checar(pos_backup != -1, "a tarefa de backup persistente existe")
if -1 not in (pos_armar, pos_rede, pos_backup):
    checar(pos_backup < pos_armar, "o backup vem antes de armar o failsafe")
    checar(pos_armar < pos_rede, "o failsafe é armado antes de a rede ser escrita")

print("\nInvariante 5 — a confirmação não usa a conexão antiga:")
checar(
    "meta: reset_connection" in texto,
    "o playbook encerra a conexão antiga antes de confirmar",
)
checar(
    "delegate_to: localhost" in texto and "wait_for:" in texto,
    "a espera pelo endereço novo roda do nó de controle, não do AP",
)

print("\nInvariante 6 — o `when` do bloco de reconexão não lê o que o bloco reescreve:")
# O `when` de um bloco é reavaliado em CADA tarefa dele. O bloco faz
# `set_fact: ansible_host=<gerência>`; um `when` sobre `ansible_host` vira
# falso logo depois, e a espera e a confirmação são puladas — o failsafe
# nunca é cancelado e reverte o AP. Aconteceu em 13/10/2026.
tarefas = yaml.safe_load(texto)[0]["tasks"]
bloco = next((t for t in tarefas if "block" in t and "Reconectar" in t.get("name", "")), None)
checar(bloco is not None, "o bloco de reconexão existe")
if bloco is not None:
    checar(
        "ansible_host" not in str(bloco.get("when", "")),
        "o `when` do bloco não depende de `ansible_host`",
    )
    # O rescue espera o AP voltar no endereço ANTERIOR. Lido de
    # `hostvars[...]['ansible_host']`, ele já devolve o de gerência, porque o
    # `set_fact` do bloco sobrepõe o inventário.
    checar(
        "ansible_host" not in str(bloco.get("rescue", "")),
        "o rescue não lê `ansible_host` (o bloco já o reescreveu)",
    )


def _tarefas(lista):
    for t in lista or []:
        yield t
        for chave in ("block", "rescue", "always"):
            yield from _tarefas(t.get(chave))


print("\nInvariante 7 — tarefa delegada ao nó de controle não pede sudo:")
# `ansible.cfg` liga `become = True`; delegada ao localhost sem
# `become: false`, a tarefa pede senha de sudo na estação e falha antes de
# fazer o que veio fazer. Aconteceu em 13/10/2026 no `wait_for` da reconexão.
for t in _tarefas(tarefas):
    if t.get("delegate_to") == "localhost":
        checar(t.get("become") is False, f"`{t.get('name')}` tem `become: false`")

print("\nHigiene do render:")
for rotulo, texto_render in (("network", rede), ("system", sistema)):
    checar(
        "{{" not in texto_render and "{%" not in texto_render,
        f"não sobrou Jinja não resolvido no {rotulo}",
    )
checar("pppoe" not in rede.lower(), "o alvo não carrega bloco PPPoE")

if falhas:
    print(f"\n✗ render-openwrt-network: {len(falhas)} falha(s)")
    sys.exit(1)
print("\n✓ render-openwrt-network")
