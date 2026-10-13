#!/usr/bin/env python3
"""O 00-node-bootstrap entra como root mesmo com `ansible_user` no inventário.

`remote_user: root` no play PERDE para `ansible_user` de host: em prod o
hosts.ini declara `ansible_user=<system_user>`, a conta que este playbook
existe para criar — `Permission denied` como gleiton (13/10/2026). Só
`vars: ansible_user: root` no play vence a variável de host.
"""
import sys
from pathlib import Path

import yaml

PB = Path(__file__).resolve().parents[1] / "playbooks" / "00-node-bootstrap.yml"
play = yaml.safe_load(PB.read_text(encoding="utf-8"))[0]
falhas = []


def checar(condicao, msg):
    print(f"  {'OK' if condicao else 'FALHOU'} {msg}")
    if not condicao:
        falhas.append(msg)


checar((play.get("vars") or {}).get("ansible_user") == "root", "play declara vars.ansible_user = root")

# O template Debian 13 do CT vem SEM o pacote sudo: /etc/sudoers.d não existe
# e o `visudo` do validate também não (13/10/2026). O pacote tem de entrar
# antes da task que escreve em /etc/sudoers.d.
tarefas = (play.get("pre_tasks") or []) + (play.get("tasks") or [])
nomes = [t.get("name", "") for t in tarefas]
i_sudo = next((i for i, t in enumerate(tarefas)
               if (t.get("ansible.builtin.apt") or {}).get("name") == "sudo"), None)
i_sudoers = next((i for i, n in enumerate(nomes) if "sudoers.d" in n), None)
checar(i_sudo is not None, "o play instala o pacote sudo")
checar(None not in (i_sudo, i_sudoers) and i_sudo < i_sudoers,
       "o sudo é instalado antes de escrever em /etc/sudoers.d")
# 01-bootstrap-os: o dist-upgrade atualiza a libc, o systemd se re-executa e
# reinicia o systemd-networkd do CT — a conexão SSH da task morre e o
# ansible-playbook fica pendurado sem timeout (13/10/2026, 21 min parado com
# o upgrade já concluído nos nós). Assíncrona, cada consulta usa conexão nova.
PB01 = PB.parent / "01-bootstrap-os.yml"
t01 = [t for p in yaml.safe_load(PB01.read_text(encoding="utf-8")) for t in (p.get("tasks") or [])]
up = next((t for t in t01 if (t.get("ansible.builtin.apt") or {}).get("upgrade")), None)
checar(up is not None, "01-bootstrap-os tem a task de upgrade")
if up is not None:
    checar(int(up.get("async") or 0) > 0 and int(up.get("poll") or 0) > 0,
           "o upgrade do 01-bootstrap-os é assíncrono (async + poll)")

# Repositório do Docker: `arch=` é nome do dpkg (amd64), não do kernel.
# `ansible_architecture` dá `x86_64`, o apt ignora a linha e `docker-ce`
# some (13/10/2026: "No package matching 'docker-ce' is available").
repo = next((t for t in t01 if "ansible.builtin.apt_repository" in t
             and "download.docker.com" in str(t["ansible.builtin.apt_repository"].get("repo"))
             and t["ansible.builtin.apt_repository"].get("state", "present") == "present"), None)
checar(repo is not None, "01-bootstrap-os adiciona o repositório do Docker")
if repo is not None:
    checar("ansible_architecture" not in str(repo["ansible.builtin.apt_repository"]["repo"]),
           "o arch= do repositório do Docker não vem de ansible_architecture")

# DOCKER-USER: a faixa de gerência é LISTA (`mgmt_allowed_cidrs`), e os DROP
# entram DEPOIS de todos os RETURN dela — posição fixa 4/5/6 só valia para uma
# faixa; com três, DROP no meio dos RETURN fecharia a porta a quem foi liberado.
regras = (PB.parent.parent / "tasks" / "docker_user_rules.yml").read_text(encoding="utf-8")
checar("mgmt_allowed_cidr " not in regras and "mgmt_allowed_cidr }}" not in regras
       and "mgmt_allowed_cidr is" not in regras,
       "docker_user_rules não usa mais a faixa única `mgmt_allowed_cidr`")
checar("mgmt_allowed_cidrs | length" in regras,
       "a posição dos DROP é calculada pelo tamanho de `mgmt_allowed_cidrs`")

# Persistência do DOCKER-USER pelo UFW, não pelo iptables-persistent: os dois
# pacotes conflitam no Debian, e instalar um removia o outro a cada execução
# (13/10/2026: `Remove: ufw` no history do apt dos nós).
_instala = [t for t in yaml.safe_load(regras)
            if "iptables-persistent" in str((t.get("ansible.builtin.apt") or {}).get("name"))
            and (t.get("ansible.builtin.apt") or {}).get("state", "present") == "present"]
checar(not _instala, "docker_user_rules não instala o iptables-persistent")
checar("/etc/ufw/after.rules" in regras, "docker_user_rules persiste pelo /etc/ufw/after.rules")
tpl = PB.parent.parent / "templates" / "ufw_docker_user.rules.j2"
checar(tpl.exists(), "existe o template do bloco DOCKER-USER do UFW")
if tpl.exists():
    from jinja2 import Environment, StrictUndefined
    bloco = Environment(undefined=StrictUndefined, trim_blocks=True).from_string(
        tpl.read_text(encoding="utf-8")).render(mgmt_allowed_cidrs=["192.0.2.0/24", "198.51.100.0/24"])
    linhas = [l for l in bloco.splitlines() if l.startswith("-A DOCKER-USER")]
    ret = [i for i, l in enumerate(linhas) if l.endswith("-j RETURN")]
    drop = [i for i, l in enumerate(linhas) if l.endswith("-j DROP")]
    checar(bloco.lstrip().startswith("*filter") and bloco.rstrip().endswith("COMMIT"),
           "o bloco é *filter ... COMMIT")
    checar(":DOCKER-USER - [0:0]" in bloco, "o bloco declara a chain DOCKER-USER (reload a reconstrói)")
    checar(len(ret) == 6 and len(drop) == 3, f"6 RETURN e 3 DROP com 2 faixas (veio {len(ret)}/{len(drop)})")
    checar(ret and drop and max(ret) < min(drop), "todo RETURN vem antes de todo DROP")

if falhas:
    sys.exit("\n✗ gates-node-bootstrap")
print("\n✓ gates-node-bootstrap")
