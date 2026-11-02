#!/usr/bin/env python3
"""Gate do openipmi em host sem BMC (tasks/node_exporter.yml).

O pacote de coletores do node_exporter puxa o `openipmi` por Recommends; em
placa sem BMC o serviço falha em todo boot. Confere:

  - a detecção do BMC é pelo SMBIOS (`dmidecode -t 38`), e não por
    `/sys/class/ipmi` — essa classe só existe depois de o próprio openipmi
    carregar o `ipmi_devintf`, e um host novo com BMC seria mascarado;
  - mascarar e limpar o failed só rodam com o init script presente, o
    dmidecode tendo respondido (rc 0) e sem `DMI type 38` na saída;
  - mascara e desabilita, sem remover o pacote (o Recommends o traria de volta);
  - a ordem: detectar antes de mascarar.
"""
import sys
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parents[2]
TASKS = yaml.safe_load((RAIZ / "ansible" / "tasks" / "node_exporter.yml").read_text(encoding="utf-8"))
NOMES = [t.get("name") for t in TASKS]
DETECTA = "Detectar BMC pelo SMBIOS (tipo 38, IPMI Device Information)"
MASCARA = "Mascarar o openipmi em host sem BMC"
LIMPA = "Limpar o estado failed do openipmi"
SEM_BMC = {"openipmi_init.stat.exists", "openipmi_bmc.rc == 0", "'DMI type 38' not in openipmi_bmc.stdout"}
falhas = []


def checar(condicao, msg):
    print(f"  {'OK' if condicao else 'FALHOU'} {msg}")
    if not condicao:
        falhas.append(msg)


def task(nome):
    achadas = [t for t in TASKS if t.get("name") == nome]
    checar(len(achadas) == 1, f"task '{nome}' existe uma vez")
    return achadas[0] if achadas else {}


init = task("Detectar o init script do openipmi")
checar(init.get("ansible.builtin.stat", {}).get("path") == "/etc/init.d/openipmi",
       "detecção do serviço pelo init script")

bmc = task(DETECTA)
checar(str(bmc.get("ansible.builtin.command", "")).strip() == "dmidecode -t 38",
       "detecção do BMC pelo dmidecode -t 38")
checar(bmc.get("check_mode") is False, "detecção roda também em --check")
checar(not any("/sys/class/ipmi" in str(t.get("ansible.builtin.find", {}).get("paths", "")) for t in TASKS),
       "nada decide BMC por /sys/class/ipmi")

mascarar = task(MASCARA)
svc = mascarar.get("ansible.builtin.systemd_service", {})
checar(svc.get("name") == "openipmi" and svc.get("masked") is True and svc.get("enabled") is False,
       "openipmi mascarado e desabilitado")
checar(SEM_BMC <= set(mascarar.get("when", [])), "mascara só com init presente, dmidecode ok e sem BMC")

limpar = task(LIMPA)
checar(SEM_BMC <= set(limpar.get("when", [])), "reset-failed só com init presente, dmidecode ok e sem BMC")
checar("openipmi_failed.stdout == 'failed'" in limpar.get("when", []), "reset-failed só com a unit em failed")

checar(not any(
    "openipmi" in str(t.get("ansible.builtin.apt", {}).get("name", ""))
    and t.get("ansible.builtin.apt", {}).get("state") == "absent"
    for t in TASKS), "não remove o pacote openipmi")

checar(DETECTA in NOMES and MASCARA in NOMES and NOMES.index(DETECTA) < NOMES.index(MASCARA),
       "detecta o BMC antes de mascarar")

if falhas:
    print(f"gates-openipmi: {len(falhas)} falha(s)")
    sys.exit(1)
print("gates-openipmi: OK")
