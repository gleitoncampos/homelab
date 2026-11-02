#!/usr/bin/env python3
"""Gate do openipmi em host sem BMC (tasks/node_exporter.yml).

O pacote de coletores do node_exporter puxa o `openipmi` por Recommends; em
placa sem BMC o serviço falha em todo boot. Confere:

  - a detecção do BMC vem do hardware (`/sys/class/ipmi`), não de variável;
  - o mascaramento só roda com o init script presente E sem BMC;
  - mascara e desabilita, sem remover o pacote (o Recommends o traria de volta);
  - a ordem: detectar antes de mascarar.
"""
import sys
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parents[2]
TASKS = yaml.safe_load((RAIZ / "ansible" / "tasks" / "node_exporter.yml").read_text(encoding="utf-8"))
NOMES = [t.get("name") for t in TASKS]
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

bmc = task("Detectar interface IPMI (BMC) no hardware")
checar(bmc.get("ansible.builtin.find", {}).get("paths") == "/sys/class/ipmi",
       "detecção do BMC por /sys/class/ipmi")

mascarar = task("Mascarar o openipmi em host sem BMC")
svc = mascarar.get("ansible.builtin.systemd_service", {})
checar(svc.get("name") == "openipmi" and svc.get("masked") is True and svc.get("enabled") is False,
       "openipmi mascarado e desabilitado")
quando = mascarar.get("when", [])
checar("openipmi_bmc.matched == 0" in quando, "mascara só sem BMC")
checar("openipmi_init.stat.exists" in quando, "mascara só com o init script presente")

checar(not any(
    "openipmi" in str(t.get("ansible.builtin.apt", {}).get("name", ""))
    and t.get("ansible.builtin.apt", {}).get("state") == "absent"
    for t in TASKS), "não remove o pacote openipmi")

checar(NOMES.index("Detectar interface IPMI (BMC) no hardware") < NOMES.index("Mascarar o openipmi em host sem BMC")
       if "Mascarar o openipmi em host sem BMC" in NOMES and "Detectar interface IPMI (BMC) no hardware" in NOMES
       else False, "detecta o BMC antes de mascarar")

if falhas:
    print(f"gates-openipmi: {len(falhas)} falha(s)")
    sys.exit(1)
print("gates-openipmi: OK")
