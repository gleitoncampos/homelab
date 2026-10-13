#!/usr/bin/env python3
"""Confere que a DNSBL do Unbound tem dado, não só configuração.

O `config.xml` declara a DNSBL, mas o Unbound consulta
`/var/unbound/data/dnsbl.json`, que só a ação `configctl unbound dnsbl`
gera (baixa e compila as listas). A GUI a dispara no "Apply"; o IaC grava o
config.xml direto e nunca a chamava, e não havia cron para ela — firewall
reinstalado sem bloqueio nenhum, com a DNSBL "habilitada" (13/10/2026).
"""
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
PB = RAIZ / "playbooks" / "network" / "01-opnsense-setup.yml"

with tempfile.TemporaryDirectory() as tmp:
    saida = Path(tmp) / "config.xml"
    r = subprocess.run(
        [sys.executable, str(RAIZ / "tests" / "render_opnsense.py"),
         "--template", str(RAIZ / "templates/network/opnsense/config.xml.j2"),
         "--common", str(RAIZ / "inventories/_common/opnsense.yml"),
         "--fixture", str(RAIZ / "tests/fixtures/opnsense.yml"),
         "--out", str(saida)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        sys.exit(f"✗ render falhou: {r.stderr.strip()}")
    raiz = ET.parse(saida).getroot()

falhas = []


def checar(condicao, msg):
    print(f"  {'OK' if condicao else 'FALHOU'} {msg}")
    if not condicao:
        falhas.append(msg)


jobs = [j for j in raiz.iter("job") if (j.findtext("command") or "").strip() == "unbound dnsbl"]
checar(len(jobs) == 1, "existe um job de cron `unbound dnsbl`")
if jobs:
    checar(jobs[0].findtext("enabled") == "1", "o job da DNSBL está habilitado")
    checar(bool(jobs[0].get("uuid")), "o job da DNSBL tem uuid")

pb = PB.read_text(encoding="utf-8")
p_cfg = pb.find("Aplicar novo arquivo /conf/config.xml")
p_dnsbl = pb.find("configctl unbound dnsbl")
checar(p_dnsbl != -1, "o playbook roda `configctl unbound dnsbl`")
checar(-1 not in (p_cfg, p_dnsbl) and p_cfg < p_dnsbl, "a DNSBL é gerada depois de aplicar o config.xml")

if falhas:
    sys.exit(f"\n✗ render-opnsense-dnsbl: {len(falhas)} falha(s)")
print("\n✓ render-opnsense-dnsbl")
