#!/usr/bin/env python3
"""Confere o usuário do exporter do Proxmox no 00-proxmox-api-token.yml.

O exporter só LÊ a API (nós, convidados, storage): role PVEAuditor, nunca
root@pam. Usuário `pve_exporter_user` (prometheus@pve) com a senha do vault,
nos dois hosts — os dois hipervisores não são cluster, então cada um tem o próprio
cadastro, e o exporter usa uma credencial só (13/10/2026). A senha é
reconciliada a cada execução: o vault manda.
"""
import sys
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parents[1]
play = yaml.safe_load((RAIZ / "playbooks" / "00-proxmox-api-token.yml").read_text(encoding="utf-8"))[0]
tarefas = play.get("tasks") or []
texto = [(t.get("name", ""), str(t)) for t in tarefas]
falhas = []


def checar(condicao, msg):
    print(f"  {'OK' if condicao else 'FALHOU'} {msg}")
    if not condicao:
        falhas.append(msg)


cria = [t for n, t in texto if "pveum user add {{ pve_exporter_user }}" in t]
checar(len(cria) == 1, "cria o usuário pve_exporter_user")
senha = [tk for tk in tarefas if "pve_exporter_password" in str(tk)]
checar(bool(senha) and all(tk.get("no_log") is True for tk in senha),
       "toda task que usa pve_exporter_password tem no_log")
checar(any("/access/password" in str(tk) for tk in senha), "reconcilia a senha pela API (o vault manda)")
acl = [t for n, t in texto if "aclmod / -user {{ pve_exporter_user }} -role PVEAuditor" in t]
checar(len(acl) == 1, "aplica PVEAuditor em / ao usuário do exporter")
checar(not any("pve_exporter_user" in t and "Administrator" in t for n, t in texto),
       "o usuário do exporter nunca recebe Administrator")

pre = str(play.get("pre_tasks") or [])
checar("validate_vault.yml" in pre and "pve_exporter_password" in pre,
       "gate do vault exige pve_exporter_* antes das tasks com no_log")

if falhas:
    sys.exit(f"\n✗ gates-pve-exporter: {len(falhas)} falha(s)")
print("\n✓ gates-pve-exporter")
