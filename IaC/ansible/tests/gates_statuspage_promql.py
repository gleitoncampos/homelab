#!/usr/bin/env python3
"""Gate semântico das expressões do painel "status pages oficiais".

O gates_monitoring.py confere a FORMA do dashboard (texto). Este avalia as
expressões dos painéis 19 e 20 com `promtool test rules` e séries sintéticas:
um termo trocado de lugar, um `== 1` a mais ou um `or` fora de ordem passam no
teste de texto e quebram aqui (os casos `c_*` põem duas séries na mesma instância,
como acontece quando o Prometheus reinicia e a série antiga fica visível até 5 min). Pula, avisando, se não houver docker (mesmo
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
    if os.environ.get("CI"):
        sys.exit("✗ docker ausente no CI — o gate semântico das expressões não pode ser pulado")
    print("  ! docker ausente — gate semântico das expressões pulado (localmente; no CI falha)")
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
        # Colisão por par adjacente: a série antiga e a nova visíveis juntas; vence a mais grave.
        "c_cr_ma": (['indicator="critical"', 'indicator="major"'], 1, 4),
        "c_ma_mi": (['indicator="major"', 'indicator="minor"'], 1, 3),
        "c_mi_mt": (['indicator="minor"', 'indicator="maintenance"'], 1, 2),
        "c_mt_n": (['indicator="maintenance"', 'indicator="none"'], 1, 1),
    }),
    "statuspage_cstate": ("cstate_summary_info", "summary", {
        "ok": ('summary="ok"', 1, 0), "notice": ('summary="notice"', 1, 1),
        "disr": ('summary="disrupted"', 1, 3), "down": ('summary="down"', 1, 4),
        "vazio": ('summary=""', 1, 5), "html": (None, 1, 5), "parado": (None, 0, 5),
        "c_dn_di": (['summary="down"', 'summary="disrupted"'], 1, 4),
        "c_di_no": (['summary="disrupted"', 'summary="notice"'], 1, 3),
        "c_no_ok": (['summary="notice"', 'summary="ok"'], 1, 1),
    }),
}

print("Forma da consulta por painel (stat = instantânea, linha do tempo = intervalo):")
for pid, (inst, rng) in {19: (True, False), 20: (False, True)}.items():
    _p = next(p for p in DASH["panels"] if p["id"] == pid)
    checar(all(t.get("instant") is inst and t.get("range") is rng for t in _p["targets"]),
           f"painel {pid}: instant={inst} e range={rng} em todos os alvos")

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
            for sl in ([] if sel is None else (sel if isinstance(sel, list) else [sel])):
                series.append({"series": f'{metrica}{{job="{job}",instance="{inst}",nome="{inst}",{sl}}}', "values": "1x6"})
            series.append({"series": f'up{{job="{job}",instance="{inst}",nome="{inst}"}}', "values": f"{up}x6"})
            esperado.append({"labels": f'{{instance="{inst}"}}', "value": sev})
        # Ruído: alvos que só existem em OUTRO job (parado e de pé) não podem entrar no resultado: o job irmão
        # do `CASOS`, o de componentes e um sem relação. Prova o filtro `job=` do termo DESCONHECIDO; o
        # promtool reprova amostra a mais. As séries levam `job` e `nome`, como no scrape real.
        for _j in sorted(set(CASOS) - {job}) + ["statuspage_componentes", "blackbox"]:
            for _i, _up in ((f"so_{_j}_parado", 0), (f"so_{_j}_de_pe", 1)):
                series.append({"series": f'up{{job="{_j}",instance="{_i}",nome="{_i}"}}', "values": f"{_up}x6"})
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
