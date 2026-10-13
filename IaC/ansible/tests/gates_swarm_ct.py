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

if falhas:
    sys.exit(f"\n✗ gates-swarm-ct: {len(falhas)} falha(s)")
print("\n✓ gates-swarm-ct")
