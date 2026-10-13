#!/usr/bin/env python3
"""Confere que o timeout adaptativo do pf não derruba conexão estabelecida.

`adaptivestart`/`adaptiveend` vão para o pf como NÚMERO DE ESTADOS, não
porcentagem (filter.inc: `set timeout { adaptive.start N, adaptive.end M }`).
O template tinha 80/95: a tabela fica em ~76 estados em repouso, e qualquer
pico (reload, apt baixando pacote) passava de 80 e encolhia todo timeout até
zero em 95 — sessões SSH ociosas expiravam em segundos e o pf bloqueava o
resto do fluxo nos dois sentidos (13/10/2026, todas as quedas do dia). O
firewall antigo não tinha os campos (adaptativo desligado).
"""
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]

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
    sistema = ET.parse(saida).getroot().find("./system")

maximo = int(sistema.findtext("maximumstates") or 0)
falhas = []
for campo in ("adaptivestart", "adaptiveend"):
    valor = (sistema.findtext(campo) or "").strip()
    # Vazio desliga o adaptativo; se voltar a existir, tem de ser fração alta
    # do limite, nunca um número da ordem do repouso da tabela.
    ok = valor == "" or (maximo and int(valor) >= maximo // 2)
    print(f"  {'OK' if ok else 'FALHOU'} system.{campo} vazio ou >= metade de maximumstates (veio {valor!r})")
    if not ok:
        falhas.append(campo)

if falhas:
    sys.exit(f"\n✗ render-opnsense-pf-estados: {len(falhas)} falha(s)")
print("\n✓ render-opnsense-pf-estados")
