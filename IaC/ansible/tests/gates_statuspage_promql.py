#!/usr/bin/env python3
"""Gate semântico das expressões do painel "status pages oficiais".

O gates_monitoring.py confere a FORMA do dashboard (texto). Este avalia as
expressões dos painéis 19 e 20 com `promtool test rules` e séries sintéticas:
um termo trocado de lugar, um `== 1` a mais ou um `or` fora de ordem passam no
teste de texto e quebram aqui. Pula, avisando, se não houver docker (mesmo
critério do validate-alloy).
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]
DASH = json.loads((REPO / "docker/monitoring/swarm/dashboards/blackbox.json").read_text(encoding="utf-8"))
COMPOSE = (REPO / "docker/monitoring/swarm/compose.yml").read_text(encoding="utf-8")

falhas = []


def checar(ok, msg):
    print(f"  {'OK' if ok else 'FALHOU'} {msg}")
    if not ok:
        falhas.append(msg)


if shutil.which("docker") is None:
    print("  ! docker ausente — gate semântico das expressões pulado")
    sys.exit(0)

_img = re.search(r"quay\.io/prometheus/prometheus:v[0-9.]+", COMPOSE)
if _img is None:
    sys.exit("✗ não achei a imagem do Prometheus no compose")
IMG = _img.group(0)

# Cada caso: (instância, séries sintéticas, severidade esperada). 5 = DESCONHECIDO.
# `html` e `parado` são os dois modos de falha que o painel não pode esconder:
# página que responde 200 sem ser JSON (up=1, zero séries) e alvo que não responde (up=0).
CASOS = {
    "statuspage_status": ("statuspage_status_info", "indicator", {
        "n": ('indicator="none"', 1, 0), "mt": ('indicator="maintenance"', 1, 1),
        "mi": ('indicator="minor"', 1, 2), "ma": ('indicator="major"', 1, 3),
        "cr": ('indicator="critical"', 1, 4),
        "vazio": ('indicator=""', 1, 5), "novo": ('indicator="outro"', 1, 5),
        "html": (None, 1, 5), "parado": (None, 0, 5),
    }),
    "statuspage_cstate": ("cstate_summary_info", "summary", {
        "ok": ('summary="ok"', 1, 0), "notice": ('summary="notice"', 1, 1),
        "disr": ('summary="disrupted"', 1, 3), "down": ('summary="down"', 1, 4),
        "vazio": ('summary=""', 1, 5), "html": (None, 1, 5), "parado": (None, 0, 5),
    }),
}

print("Expressões dos painéis 19 e 20 avaliadas pelo promtool:")
for pid in (19, 20):
    painel = next(p for p in DASH["panels"] if p["id"] == pid)
    for job, (metrica, _rotulo, casos) in CASOS.items():
        exprs = [t["expr"] for t in painel["targets"] if metrica in t["expr"]]
        checar(len(exprs) == 1, f"painel {pid}: exatamente uma expressão de {metrica} ({len(exprs)})")
        if len(exprs) != 1:
            continue
        series, esperado = [], []
        for inst, (sel, up, sev) in casos.items():
            if sel is not None:
                series.append({"series": f'{metrica}{{instance="{inst}",{sel}}}', "values": "1x6"})
            series.append({"series": f'up{{job="{job}",instance="{inst}"}}', "values": f"{up}x6"})
            esperado.append({"labels": f'{{instance="{inst}"}}', "value": sev})
        # Ruído: o MESMO nome parado em outro job não pode mexer no resultado.
        series.append({"series": f'up{{job="statuspage_componentes",instance="n"}}', "values": "0x6"})
        teste = {"evaluation_interval": "1m", "tests": [{
            "interval": "1m", "input_series": series,
            "promql_expr_test": [{"expr": exprs[0], "eval_time": "3m", "exp_samples": esperado}]}]}
        with tempfile.TemporaryDirectory() as d:
            os.chmod(d, 0o755)  # o usuário do container não é o dono do tmp
            (Path(d) / "t.yml").write_text(yaml.safe_dump(teste, allow_unicode=True), encoding="utf-8")
            os.chmod(Path(d) / "t.yml", 0o644)
            r = subprocess.run(["docker", "run", "--rm", "-v", f"{d}:/t:ro", "--entrypoint", "promtool", IMG,
                                "test", "rules", "/t/t.yml"], capture_output=True, text=True)
        checar(r.returncode == 0 and "SUCCESS" in r.stdout,
               f"painel {pid}: {metrica} -> severidade certa em {len(casos)} casos ({'ok' if r.returncode == 0 else (r.stdout + r.stderr).strip()[-300:]})")

if falhas:
    sys.exit(f"\n✗ gates-statuspage-promql: {len(falhas)} falha(s)")
print("\n✓ gates-statuspage-promql")
