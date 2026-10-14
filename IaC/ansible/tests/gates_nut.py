#!/usr/bin/env python3
"""Confere a ordem do 07-nut-ups.yml: o nut.target só sobe com o upsmon.conf no lugar.

O `nut.target` puxa o `nut-monitor`. Subindo antes do template do
upsmon.conf, o upsmon morria com "Can't open /etc/nut/upsmon.conf", o
systemd estourava o limite de partidas (5 em 1 s) e a task "Habilitar e
iniciar o upsmon" levava "Start request repeated too quickly" (13/10/2026,
primeira execução no nó primário).
"""
import sys
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parents[1]
play = yaml.safe_load((RAIZ / "playbooks" / "07-nut-ups.yml").read_text(encoding="utf-8"))[0]
falhas = []


def checar(condicao, msg):
    print(f"  {'OK' if condicao else 'FALHOU'} {msg}")
    if not condicao:
        falhas.append(msg)


def achatar(tarefas):
    for t in tarefas or []:
        yield t
        yield from achatar(t.get("block"))


tarefas = list(achatar(play.get("tasks")))
sobe = [i for i, t in enumerate(tarefas)
        if (t.get("ansible.builtin.systemd") or {}).get("name") == "nut.target"
        and (t.get("ansible.builtin.systemd") or {}).get("state") == "started"]
conf = [i for i, t in enumerate(tarefas) if t.get("name", "").startswith("Configurar o upsmon")]
checar(len(sobe) == 1 and len(conf) == 1, "uma task sobe o nut.target e uma grava o upsmon.conf")
checar(bool(sobe and conf) and sobe[0] > conf[0], "nut.target só sobe depois do upsmon.conf gravado")

# Sem diretório explícito, o configure grava nas units um tmpfiles em
# `/nut-common-tmpfiles.conf` (14/10/2026). E o stamp precisa mudar quando
# os argumentos mudam, senão a flag nova nunca chega ao binário.
args = play.get("vars", {}).get("nut_configure_args", [])
checar(any(a.startswith("--with-systemdtmpfilesdir=/") for a in args),
       "configure com --with-systemdtmpfilesdir absoluto")
checar("nut_configure_args" in str(play.get("vars", {}).get("nut_build_id", "")),
       "nut_build_id deriva dos argumentos do configure")
texto = str(tarefas)
checar("nut_configure_args" in texto and "nut_build_id" in texto,
       "configure usa nut_configure_args e o stamp grava nut_build_id")
checar("tmpfiles-nut.conf.j2" not in texto, "sem tmpfiles próprio duplicando o do NUT")

if falhas:
    sys.exit(f"\n✗ gates-nut: {len(falhas)} falha(s)")
print("\n✓ gates-nut")
