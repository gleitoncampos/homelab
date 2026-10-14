#!/usr/bin/env python3
"""Confere o playbook do NFC do AP (network/02-openwrt-nfc.yml).

O que cada gate impede (14/10/2026):
- teto do NDEF antes da escrita: o xinfc imprime "Success." sem gravar nada
  quando o NDEF não cabe, e acima de 144 bytes a escrita alcançaria a zona de
  trava/configuração em chips da família NTAG213;
- no_log na escrita: a linha de comando leva SSID e senha;
- modo igual ao dos SSIDs de visitante: um WSC com modo diferente do AP faz o
  Android oferecer uma rede que não autentica;
- binário fora do git: ele é compilado do fonte, no commit fixado.
"""
import re
import subprocess
import sys
from pathlib import Path

import yaml

ANSIBLE = Path(__file__).resolve().parents[1]
REPO = ANSIBLE.parents[1]
falhas = []


def checar(condicao, msg):
    print(f"  {'OK' if condicao else 'FALHOU'} {msg}")
    if not condicao:
        falhas.append(msg)


play = yaml.safe_load((ANSIBLE / "playbooks/network/02-openwrt-nfc.yml").read_text(encoding="utf-8"))[0]
tarefas = play["tasks"]
nomes = [t.get("name", "") for t in tarefas]


def indice(trecho):
    return next((i for i, t in enumerate(tarefas) if trecho in str(t)), None)


escrita = indice("/usr/bin/xinfc-wsc {{ openwrt_nfc_i2c_bus }}")
teto = indice("openwrt_nfc_max_ndef")
conferir = indice("New NDEF is 0 bytes")
checar(escrita is not None, "existe a task que grava o chip")
if escrita is not None:
    t = tarefas[escrita]
    checar(t.get("no_log") is True, "a gravação tem no_log (a linha de comando leva a senha)")
    r = t["ansible.builtin.raw"]
    checar("wifi_guest_ssid | quote" in r and "wifi_guest_password | quote" in r,
           "SSID e senha vão com `| quote`")
    checar("wifi_main" not in str(t), "só a rede de visitantes vai para o chip")
    checar("_nfc_gravar" in str(t.get("when", "")), "só grava quando o carimbo mudou")
    checar(teto is not None and teto < escrita, "o teto do NDEF é conferido antes da gravação")
    checar(conferir is not None and conferir > escrita, "a gravação é conferida depois (Success e NDEF != 0)")

for env in ("prod", "dev"):
    d = yaml.safe_load((ANSIBLE / f"inventories/{env}/group_vars/network/10-defaults.yml").read_text(encoding="utf-8"))
    checar(int(d.get("openwrt_nfc_max_ndef", 999)) <= 144, f"{env}: openwrt_nfc_max_ndef <= 144")
    checar(re.fullmatch(r"[0-9a-f]{40}", str(d.get("xinfc_git_ref", ""))) is not None,
           f"{env}: xinfc_git_ref é um commit completo")

sem_fio = (ANSIBLE / "templates/network/openwrt_wireless.j2").read_text(encoding="utf-8")
modos = set(re.findall(r"config wifi-iface 'wifi_guest_[^']+'(?:\n\t[^\n]*)*?\n\toption encryption '([^']+)'", sem_fio))
checar(modos == {play["vars"]["_nfc_modo"]},
       f"o modo do NFC ({play['vars']['_nfc_modo']}) é o dos SSIDs de visitante ({sorted(modos)})")

ign = subprocess.run(["git", "-C", str(REPO), "check-ignore", "-q", "IaC/ansible/files/xinfc/xinfc-wsc"])
checar(ign.returncode == 0, "o binário do xinfc é ignorado pelo git")
versionado = subprocess.run(["git", "-C", str(REPO), "ls-files", "IaC/ansible/files/xinfc/xinfc-wsc"],
                            capture_output=True, text=True).stdout.strip()
checar(versionado == "", "o binário do xinfc não está versionado")

if falhas:
    sys.exit(f"\n✗ gates-openwrt-nfc: {len(falhas)} falha(s)")
print("\n✓ gates-openwrt-nfc")
