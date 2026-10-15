#!/usr/bin/env python3
"""Confere a ordem do 07-nut-ups.yml: o nut.target só sobe com o upsmon.conf no lugar.

O `nut.target` puxa o `nut-monitor`. Subindo antes do template do
upsmon.conf, o upsmon morria com "Can't open /etc/nut/upsmon.conf", o
systemd estourava o limite de partidas (5 em 1 s) e a task "Habilitar e
iniciar o upsmon" levava "Start request repeated too quickly" (13/10/2026,
primeira execução no nó primário).
"""
import re
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

# Desligamento em dois tempos (14/10/2026: bateria de 2 min 31 s, o nó primário morreu
# 13 s depois do LB sem desligar): N s na bateria -> stopall dos convidados;
# LB -> o upsmon desliga o hipervisor; energia de volta -> startall.
TPL = RAIZ / "templates" / "nut"
upsmon = (TPL / "upsmon.conf.j2").read_text(encoding="utf-8")
checar("NOTIFYCMD" in upsmon and "SYSLOG+WALL+EXEC" in upsmon.split("NOTIFYFLAG ONBATT")[1].splitlines()[0]
       and "SYSLOG+WALL+EXEC" in upsmon.split("NOTIFYFLAG ONLINE")[1].splitlines()[0],
       "upsmon manda ONBATT e ONLINE para o upssched (NOTIFYCMD + EXEC)")
sched = (TPL / "upssched.conf.j2").read_text(encoding="utf-8")
checar("AT ONBATT * START-TIMER parar-convidados {{ nut_convidados_parar_apos_s }}" in sched
       and "AT ONLINE * CANCEL-TIMER parar-convidados" in sched
       and "AT ONLINE * EXECUTE religar-convidados" in sched,
       "upssched: timer na bateria, cancelado e religando quando a energia volta")
cmd = (TPL / "upssched-cmd.j2").read_text(encoding="utf-8")
_cmd_codigo = "\n".join(l for l in cmd.splitlines() if not l.lstrip().startswith("#"))
checar("pvesh" not in _cmd_codigo and '$fila/parar' in cmd and '$fila/religar' in cmd,
       "o upssched-cmd (usuário nut) só deixa pedido na fila, não chama o pvesh")
exe = (TPL / "iac-nut-convidados.sh.j2").read_text(encoding="utf-8")
checar("stopall" in exe and "startall" in exe and exe.index('-e "$estado"') < exe.index("startall"),
       "o executor root só religa o que ele mesmo parou (estado)")
tmp = (TPL / "tmpfiles-iac-nut.conf.j2").read_text(encoding="utf-8")
checar("{{ nut_iac_run_dir }} 0750 root nut" in tmp and "{{ nut_iac_run_dir }}/fila 0770 root nut" in tmp,
       "a fila é gravável pelo nut, o diretório do estado não")
caminho = (TPL / "iac-nut-convidados.path.j2").read_text(encoding="utf-8")
checar("PathExistsGlob={{ nut_iac_run_dir }}/fila/*" in caminho, "a .path vigia só a fila")
comum = yaml.safe_load((RAIZ / "inventories/_common/all.yml").read_text(encoding="utf-8"))
checar(comum["nut_iac_estado"].startswith(comum["nut_iac_run_dir"] + "/")
       and "/fila/" not in comum["nut_iac_estado"], "o estado fica fora da fila")
nomes = " ".join(str(t.get("name", "")) + str(t) for t in tarefas)
checar("iac-nut-convidados.path" in nomes and "upssched-cmd.j2" in nomes and "tmpfiles-iac-nut.conf.j2" in nomes,
       "o 07-nut-ups instala fila, upssched, executor e a .path")

# Terraform: ordem e tempo limite de desligamento em todo convidado, com o
# firewall parando por último (ordem 1 = sobe primeiro, desce por último).
tf = (RAIZ.parents[0] / "terraform/proxmox/main.tf").read_text(encoding="utf-8")
recursos = re.split(r'\n(?=resource "proxmox_virtual_environment_(?:vm|container)")', tf)
ordens = {}
for r in recursos[1:]:
    nome = re.match(r'resource "[^"]+" "([^"]+)"', r).group(1)
    m = re.search(r"startup \{\s*order\s*=\s*(\d+)\s*down_delay\s*=\s*(\d+)", r)
    checar(m is not None, f"terraform: {nome} tem startup com order e down_delay")
    if m:
        ordens[nome] = int(m.group(1))
if ordens:
    checar(ordens.get("opnsense") == min(ordens.values()), "terraform: o firewall tem a menor ordem (para por último)")

if falhas:
    sys.exit(f"\n✗ gates-nut: {len(falhas)} falha(s)")
print("\n✓ gates-nut")
