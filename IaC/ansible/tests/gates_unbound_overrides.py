#!/usr/bin/env python3
"""Confere que o playbook do OPNsense recusa override do Unbound que derruba o daemon.

O override `*` vira uma local-zone `redirect` em `<homelab_domain>`. O Unbound
só aceita local-data no topo dessa zona: com outro host no mesmo domínio ele
NÃO sobe ("local-data in redirect zone must reside at top of zone" -> "fatal
error: Could not set up local zones") e o DNS da casa cai (20/11/2026,
`smb.<homelab_domain>`). O `--check` do playbook não pega, então a recusa é uma
task antes de qualquer escrita.

O teste extrai a task REAL do playbook, pelo nome, e roda com o ansible: quem
sabotar a condição ou apagar a task faz o teste falhar.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parents[1]
PB = RAIZ / "playbooks" / "network" / "01-opnsense-setup.yml"
NOME = "Abortar se um override do Unbound cair no domínio de um curinga"
falhas = []


def checar(condicao, msg):
    print(f"  {'OK' if condicao else 'FALHOU'} {msg}")
    if not condicao:
        falhas.append(msg)


def task_real():
    for play in yaml.safe_load(PB.read_text(encoding="utf-8")):
        for bloco in ("pre_tasks", "tasks"):
            for t in play.get(bloco, []) or []:
                if t.get("name") == NOME:
                    return t
    return None


def roda(task, overrides):
    """Devolve (código de saída, saída) do ansible rodando só a task, com a lista dada."""
    with tempfile.TemporaryDirectory() as tmp:
        pb = Path(tmp) / "pb.yml"
        pb.write_text(yaml.safe_dump([{
            "hosts": "localhost", "connection": "local", "gather_facts": False,
            "vars": {"unbound_host_overrides": overrides},
            "tasks": [task],
        }]), encoding="utf-8")
        cfg = Path(tmp) / "ansible.cfg"
        cfg.write_text("[defaults]\nlocalhost_warning = False\n", encoding="utf-8")
        r = subprocess.run(
            ["ansible-playbook", str(pb), "-i", "localhost,"],
            capture_output=True, text=True, cwd=tmp,
            env={"ANSIBLE_CONFIG": str(cfg), "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": tmp},
        )
        return r.returncode, r.stdout + r.stderr


task = task_real()
checar(task is not None, f"a task `{NOME}` existe no playbook")
if task is None:
    sys.exit("✗ sem a task, o resto não tem o que testar")

CURINGA = {"hostname": "*", "domain": "exemplo.test", "server": "192.0.2.10"}

# Estado atual do IaC: curinga no domínio do homelab, NAS em home.arpa.
rc, _ = roda(task, [CURINGA, {"hostname": "kamartaj", "domain": "home.arpa", "server": "192.0.2.15"}])
checar(rc == 0, "curinga em um domínio e NAS em home.arpa: passa")

# O erro de 20/11/2026.
rc, saida = roda(task, [CURINGA, {"hostname": "smb", "domain": "exemplo.test", "server": "192.0.2.15"}])
checar(rc != 0, "host específico no domínio do curinga: recusa")
checar("smb" in saida, "a mensagem nomeia o host que colide")

# Sem `hostname` o template assume `*`: é curinga também.
rc, _ = roda(task, [{"domain": "exemplo.test", "server": "192.0.2.10"},
                    {"hostname": "smb", "domain": "exemplo.test", "server": "192.0.2.15"}])
checar(rc != 0, "override sem hostname conta como curinga: recusa host no mesmo domínio")

# Sem curinga no domínio não há redirect, então não há colisão.
rc, _ = roda(task, [{"hostname": "smb", "domain": "exemplo.test", "server": "192.0.2.15"}])
checar(rc == 0, "host específico sem curinga no domínio: passa")

# Lista vazia: nada a conferir.
rc, _ = roda(task, [])
checar(rc == 0, "lista vazia: passa")

if falhas:
    sys.exit(f"✗ {len(falhas)} checagem(ns) falharam")
print("✓ gates-unbound-overrides")
