#!/usr/bin/env python3
"""Confere os CTs do Docker Swarm no Terraform e o keyctl no 00-proxmox-setup.

Em CT LXC não privilegiado o kernel recusa o IPVS ("Failed to create a new
service for vip ... operation not permitted"): sem ele, o routing mesh e o VIP
de serviço do Swarm não funcionam (13/10/2026). Os CTs seguiam o padrão do
script de Docker do community-scripts (`var_unprivileged=1`), feito para Docker
sem Swarm. Decisão: CT privilegiado — a opção "Privileged" do mesmo script —
nos três (200/201 produção, 210 dev, que espelha a produção), 1 vCPU e 4 GB
nos de produção. `keyctl=1` só em não privilegiado, igual ao community-scripts.
"""
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
MAIN = (RAIZ / "terraform" / "proxmox" / "main.tf").read_text(encoding="utf-8")
PB = (RAIZ / "ansible" / "playbooks" / "00-proxmox-setup.yml").read_text(encoding="utf-8")
falhas = []


def checar(condicao, msg):
    print(f"  {'OK' if condicao else 'FALHOU'} {msg}")
    if not condicao:
        falhas.append(msg)


def recurso(nome):
    m = re.search(rf'resource "proxmox_virtual_environment_container" "{nome}" \{{(.*?)\n\}}', MAIN, re.S)
    return m.group(1) if m else ""


for nome in ("docker_swarm_node01", "docker_swarm_node02", "docker_dev_node"):
    corpo = recurso(nome)
    checar(bool(corpo), f"{nome} existe no main.tf")
    checar(re.search(r"^\s*unprivileged\s*=\s*false\b", corpo, re.M) is not None,
           f"{nome}: unprivileged = false")

for nome in ("docker_swarm_node01", "docker_swarm_node02"):
    corpo = recurso(nome)
    checar(re.search(r"cpu\s*\{[^}]*\bcores\s*=\s*1\b", corpo, re.S) is not None, f"{nome}: 1 vCPU")
    checar(re.search(r"memory\s*\{[^}]*\bdedicated\s*=\s*4096\b", corpo, re.S) is not None, f"{nome}: 4 GB")

checar("unprivileged: 1" in PB, "00-proxmox-setup só aplica keyctl em CT `unprivileged: 1`")

# Em CT privilegiado, QUALQUER feature (nesting incluso) só pode ser definida
# por root@pam: com `features {}` no Terraform o token tomava 403 na criação
# (13/10/2026). As features vêm do 00-proxmox-setup (root), como o keyctl.
for nome in ("docker_swarm_node01", "docker_swarm_node02", "docker_dev_node"):
    corpo = recurso(nome)
    checar(re.search(r"^\s*features\s*\{", corpo, re.M) is None,
           f"{nome}: sem bloco features no Terraform (privilegiado = só root@pam)")
checar("else 'nesting=1'" in PB and "'nesting=1,keyctl=1' if" in PB,
       "00-proxmox-setup grava nesting=1 em privilegiado e nesting+keyctl em não privilegiado")

# CT privilegiado só é criado por quem tem Sys.Modify em `/`: sem ele, o
# apply destruía os CTs e falhava ao recriar com 403 (13/10/2026).
TOKEN = (RAIZ / "ansible" / "playbooks" / "00-proxmox-api-token.yml").read_text(encoding="utf-8")
m = re.search(r"tf_privs_root:\s*(.+)", TOKEN)
checar(m is not None and m.group(1).split() == ["Sys.Modify"],
       "role à parte para a raiz, só com Sys.Modify")
m = re.search(r"tf_privs:\s*>-\s*\n((?:[ ]{6}[^#\n]+\n)+)", TOKEN)
checar(m is not None and "VM.Allocate" in m.group(1) and "Sys.Modify" not in set(m.group(1).split() if m else []),
       "a role principal NÃO ganha Sys.Modify (ela não vai para a raiz)")
checar("aclmod / -user" in TOKEN and "aclmod / -token" in TOKEN,
       "a role da raiz vai para o usuário E para o token (privsep=1)")

# Namespace de rede novo herda o IPv4 do namespace INICIAL do kernel
# (devconf_inherit_init_net=0), que num CT é o do HOST Proxmox. Com o host em
# ip_forward=0, o ingress_sbox e os lb_* do Swarm nasciam sem encaminhamento:
# o IPVS recebia e a resposta não saía (13/10/2026, timeout em 80/443).
checar(re.search(r"^\s*net\.ipv4\.ip_forward\s*=\s*1\s*$", PB, re.M) is not None,
       "00-proxmox-setup liga net.ipv4.ip_forward=1 no host")

if falhas:
    sys.exit(f"\n✗ gates-swarm-ct: {len(falhas)} falha(s)")
print("\n✓ gates-swarm-ct")
