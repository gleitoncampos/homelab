#!/usr/bin/env python3
"""Renderiza a rede do AP OpenWrt e trava as invariantes que trancam o
equipamento para fora.

Motivo: nenhum linter olha `.j2`, e o modo de falha aqui não é um erro — é um
AP que some da rede e só volta por console físico. Três invariantes, todas
verificadas contra a fonte do OpenWrt e não contra suposição:

1. **O nome da interface de gerência bate entre os dois templates.** O
   `openwrt_dropbear.j2` escreve `option Interface 'vlanN'` no dropbear, e o
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
firewall = env.get_template("openwrt_firewall.j2").render(**CONTEXTO)
dropbear = (
    env.get_template("openwrt_dropbear.j2").render(**CONTEXTO)
    if (TEMPLATES / "openwrt_dropbear.j2").exists()
    else ""
)

print("Invariante 1 — o dropbear aponta para uma interface que existe:")
# O dropbear só lê /etc/config/dropbear. A seção dentro do system era
# ignorada: o AP seguia na 22 com senha ligada (13/10/2026).
checar("config dropbear" not in sistema, "o system não carrega seção dropbear (seria ignorada)")
m = re.search(r"option Interface '([^']+)'", dropbear)
checar(m is not None, "o template do dropbear declara uma Interface")
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
    "tasks/reset_connection.yml" in texto,
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


print("\nInvariante 6b — o failsafe é cancelado também quando a rede não troca:")
# Armado sempre, confirmado só na troca: com o AP já na gerência, toda
# `--tags rede` era desfeita 120 s depois, com reboot (14/10/2026).
_arma = [i for i, t in enumerate(tarefas) if "iac-net-failsafe" in str(t.get("ansible.builtin.raw", ""))
         and "start-stop-daemon -S" in str(t.get("ansible.builtin.raw", ""))]
# Três tasks soltas com `when: not _trocar_rede` (o reset não pode herdar
# condição de bloco — tasks/reset_connection.yml).
_sem_troca = [(i, t) for i, t in enumerate(tarefas)
              if "_trocar_rede" in str(t.get("when", "")) and "not" in str(t.get("when", ""))]
_cancela = [(i, t) for i, t in _sem_troca if "openwrt_failsafe_confirm_file" in str(t)]
checar(len(_arma) == 1 and "when" not in tarefas[_arma[0]], "o failsafe é armado em toda etapa `rede`")
checar(len(_cancela) == 1, "existe cancelamento para `not _trocar_rede`")
if _arma and _cancela:
    i, t = _cancela[0]
    checar(i > _arma[0], "o cancelamento vem depois do arme")
    _fw = next((k for k, x in _sem_troca if "/etc/init.d/firewall reload" in str(x)), None)
    _rc = next((k for k, x in _sem_troca if "reset_connection" in str(x.get("ansible.builtin.include_tasks", ""))), None)
    checar(None not in (_fw, _rc) and _fw < _rc < i,
           "recarrega o firewall, abre conexão nova e só então confirma")
    _wifi = next((j for j, x in enumerate(tarefas) if "wifi" in (x.get("tags") or [])), None)
    checar(_wifi is None or i < _wifi, "o cancelamento vem antes da etapa `wifi` (janela de 120 s)")


print("\nInvariante 6c — `meta: reset_connection` nunca dentro de bloco condicional:")
# O meta não aceita `when`: dentro de bloco com condição ele rodava assim mesmo
# (WARNING do Ansible). Só por include_tasks com `when` próprio (14/10/2026).
def _metas_em_bloco(lista, cond=False):
    for t in lista or []:
        c = cond or ("when" in t and "block" in t)
        if t.get("ansible.builtin.meta") == "reset_connection" and (c or "when" in t):
            yield t.get("name")
        for chave in ("block", "rescue", "always"):
            yield from _metas_em_bloco(t.get(chave), c)
checar(list(_metas_em_bloco(tarefas)) == [], "nenhum reset_connection herda ou recebe `when`")

print("\nInvariante 7 — tarefa delegada ao nó de controle não pede sudo:")
# `ansible.cfg` liga `become = True`; delegada ao localhost sem
# `become: false`, a tarefa pede senha de sudo na estação e falha antes de
# fazer o que veio fazer. Aconteceu em 13/10/2026 no `wait_for` da reconexão.
for t in _tarefas(tarefas):
    if t.get("delegate_to") == "localhost":
        checar(t.get("become") is False, f"`{t.get('name')}` tem `become: false`")

print("\nInvariante 8 — a interface do dropbear está numa zona que aceita entrada:")
# Interface fora de qualquer zona cai em `defaults input REJECT`: o AP sobe na
# gerência, manda syslog para fora e recusa o SSH. Aconteceu em 13/10/2026 —
# a zona `lan` ainda listava a rede `lan`, que o network novo não cria mais.
if m:
    zonas = re.findall(r"config zone\n(.*?)(?:\n\n|\Z)", firewall, re.S)
    aceita = [
        z for z in zonas
        if f"list network '{iface}'" in z and "option input 'ACCEPT'" in z
    ]
    checar(len(aceita) == 1, f"a interface '{iface}' está numa zona com input ACCEPT")

print("\nInvariante 9 — o failsafe restaura o dropbear:")
# O dropbear passa a escutar só na interface de gerência. Revertido o network
# sem reverter o dropbear, a interface some e o AP fica sem SSH nenhum.
checar("Aplicar configuração do dropbear" in texto, "o playbook escreve /etc/config/dropbear")
for padroes in (
    RAIZ / "IaC/ansible/inventories/prod/group_vars/network/10-defaults.yml",
    RAIZ / "IaC/ansible/inventories/dev/group_vars/network/10-defaults.yml",
):
    lista = yaml.safe_load(padroes.read_text(encoding="utf-8"))["openwrt_failsafe_files"]
    checar("dropbear" in lista, f"`openwrt_failsafe_files` inclui dropbear em {padroes.parents[2].name}")
checar(
    "'dropbear'" in (TEMPLATES / "openwrt_failsafe.sh.j2").read_text(encoding="utf-8"),
    "o default do failsafe inclui dropbear",
)

print("\nInvariante 10 — a troca recarrega o firewall do AP:")
# Sem restart do fw4, a uci nova fica gravada e o nft segue com as
# interfaces antigas: a gerência nova cai no reject (13/10/2026).
checar("/etc/init.d/firewall restart" in texto, "o script da troca reinicia o firewall")

print("\nInvariante 10b — desligar o dnsmasq roda no --tags rede e devolve o resolvedor:")
# O passo 11 rodou só `--tags rede`/`wifi`; sem tag, o disable do dnsmasq
# ficou de fora. E o `stop` dele deixa /tmp/resolv.conf em 127.0.0.1: o AP
# ficou sem resolver nome nenhum (14/10/2026).
_dns = [t for t in tarefas if "/etc/init.d/dnsmasq disable" in str(t.get("ansible.builtin.raw", ""))]
checar(len(_dns) == 1, "uma task desliga o dnsmasq")
if _dns:
    _tg = _dns[0].get("tags") or []
    checar("rede" in ([_tg] if isinstance(_tg, str) else _tg), "ela tem a tag `rede`")
    _cmd = str(_dns[0]["ansible.builtin.raw"])
    checar(
        "ln -sf /tmp/resolv.conf.d/resolv.conf.auto /tmp/resolv.conf" in _cmd
        and _cmd.find("ln -sf") > _cmd.find("dnsmasq stop"),
        "ela religa /tmp/resolv.conf ao resolv.conf.auto depois do stop",
    )

print("\nInvariante 10c — instalação de pacote não devolve o rc do opkg:")
# opkg sai com 255 quando não acha pacote; o Ansible lê 255 de `raw` como
# queda de SSH e o play para com `failed=0` (14/10/2026, luci-app-access-control).
_qol = [t for t in tarefas if "luci-theme-argon" in str(t.get("ansible.builtin.raw", ""))]
checar(len(_qol) == 1, "uma task instala os pacotes de QoL")
if _qol:
    _c = str(_qol[0]["ansible.builtin.raw"]).strip()
    checar('echo "QOL:$p:rc=$?"' in _c and _c.endswith("echo QOL:FIM"),
           "a task imprime o rc de cada pacote em vez de devolvê-lo")
    checar("luci-app-access-control" not in _c, "sem pacote que não existe no feed oficial")

print("\nInvariante 10e — pacote no AP só pelo helper opkg/apk (25.12 usa apk):")
_pb = {n: yaml.safe_load((RAIZ / f"IaC/ansible/playbooks/network/{n}").read_text(encoding="utf-8"))[0]["tasks"]
       for n in ("02-openwrt-setup.yml", "02-openwrt-exporter.yml")}
for _n, _ts in _pb.items():
    for _t in _ts:
        _r = str(_t.get("ansible.builtin.raw", ""))
        if re.search(r"\b(opkg|apk) (install|add|update|list-installed|info)\b", _r) or "pkg_instala" in _r or "pkg_tem" in _r:
            checar("openwrt_pkg.sh" in _r and not re.search(r"\b(opkg|apk) (install|add|update|list-installed)\b", _r),
                   f"{_n} / {_t.get('name')}: usa o helper openwrt_pkg.sh")

print("\nInvariante 10d — sysctl do AP: módulo do BBR, arquivo próprio e persistente:")
# Na 24.10 oficial o BBR é kmod à parte, o `sysctl -p` puro lia o sysctl.conf
# herdado (chave inexistente no kernel 6.x) e o `-w` sumia no reboot (14/10/2026).
_sys = [t for t in tarefas if "net.ipv4.tcp_congestion_control=bbr" in str(t.get("ansible.builtin.raw", ""))]
_bbr = [i for i, t in enumerate(tarefas) if "kmod-tcp-bbr" in str(t.get("ansible.builtin.raw", ""))]
checar(len(_sys) == 1 and len(_bbr) == 1, "uma task instala o kmod-tcp-bbr e uma aplica o sysctl")
if _sys and _bbr:
    _c = str(_sys[0]["ansible.builtin.raw"])
    checar(_bbr[0] < tarefas.index(_sys[0]), "o módulo é instalado antes do sysctl")
    checar("/etc/sysctl.d/" in _c and "sysctl -p /etc/sysctl.d/" in _c and "sysctl -w" not in _c,
           "o sysctl vai para /etc/sysctl.d/ e só esse arquivo é aplicado")
    checar('echo "BBR:rc=$?"' in str(tarefas[_bbr[0]]["ansible.builtin.raw"]),
           "a instalação do módulo imprime o rc do opkg")

print("\nInvariante 11b — o path de cada rádio é conferido no AP antes de gravar o wireless:")
# O path muda com o kernel; gravado errado, a casa perde o Wi-Fi (14/10/2026).
_conf = next((i for i, t in enumerate(tarefas) if "/sys/devices/" in str(t.get("ansible.builtin.raw", ""))
              and "openwrt_radio0_path" in str(t.get("ansible.builtin.raw", ""))), None)
_grava = next((i for i, t in enumerate(tarefas) if "/etc/config/wireless" in str(t.get("ansible.builtin.raw", ""))
               and "cat <<" in str(t.get("ansible.builtin.raw", ""))), None)
checar(_conf is not None and _grava is not None and _conf < _grava,
       "a conferência do path vem antes de gravar o /etc/config/wireless")
if _conf is not None:
    checar("wifi" in (tarefas[_conf].get("tags") or []), "a conferência roda com a tag `wifi`")
    checar("PATH-AUSENTE" in str(tarefas[_conf].get("failed_when", "")), "path ausente faz a task falhar")

print("\nInvariante 11 — todo rádio declara o `path` do hardware:")
# Sem `option path`, o netifd não associa o wifi-device a um phy, os rádios
# ficam em `retry_setup_failed` e nenhum SSID sobe (13/10/2026).
_ficticio = {
    f"wifi_{r}_{c}": f"exemplo-{r}-{c}"
    for r in ("main", "iot", "media", "guest")
    for c in ("ssid", "password")
}
_defaults = yaml.safe_load(
    (RAIZ / "IaC/ansible/inventories/prod/group_vars/network/10-defaults.yml").read_text(encoding="utf-8")
)
try:
    sem_fio = env.get_template("openwrt_wireless.j2").render(
        **CONTEXTO, **_ficticio,
        **{k: v for k, v in _defaults.items() if k.startswith("openwrt_radio")},
    )
except Exception as erro:  # StrictUndefined: variável de path ausente
    sem_fio = ""
    checar(False, f"o wireless renderiza com os defaults ({erro})")
radios = re.findall(r"config wifi-device '([^']+)'\n(.*?)(?:\n\n|\Z)", sem_fio, re.S)
checar(len(radios) == 2, "o wireless declara dois rádios")
for nome, corpo in radios:
    checar(re.search(r"option path '[^']+'", corpo) is not None, f"o {nome} declara `option path`")

print("\nInvariante 12 — o 5 GHz escolhe canal só fora do DFS:")
# Com `channel auto` livre, o reboot semanal pôs o rádio no canal 140 (DFS) e
# a TV da Media nem tentava associar; no 36 associou na hora (20/10/2026).
_dfs = {52, 56, 60, 64, 100, 104, 108, 112, 116, 120, 124, 128, 132, 136, 140, 144}
_r5 = dict(radios).get("radio1", "")
_canais = re.search(r"option channels '([^']+)'", _r5)
checar(_canais is not None, "o radio1 restringe o ACS com `option channels`")
if _canais:
    _lista = {int(c) for c in _canais.group(1).split()}
    checar(not (_lista & _dfs), f"nenhum canal DFS na lista ({sorted(_lista & _dfs)})")

print("\nInvariante 13 — o 2,4 GHz fica num canal que não encavala (1, 6 ou 11):")
# Com ACS o rádio escolheu o 6, encavalado num vizinho a -22 dBm no canal 5;
# fixado no 1 pela medição dos vizinhos (20/10/2026).
_c24 = re.search(r"option channel '([^']+)'", dict(radios).get("radio0", ""))
checar(_c24 is not None and _c24.group(1) in ("1", "6", "11"),
       f"o radio0 usa canal 1, 6 ou 11 ({_c24.group(1) if _c24 else None})")

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
