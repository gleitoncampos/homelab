#!/usr/bin/env python3
"""Gate do hulk (playbooks/08-gpu-host.yml): servidor de GPU sob demanda.

Cada bloco trava uma decisão da spec (Frentes/homelab/spec-2026-11-15-hulk-gpu-sob-demanda.md):
o hulk fica FORA do grupo `homelab`, a rede estática só é gravada (nunca
aplicada) enquanto `gpu_rede_estatica` for false, o desligamento por
ociosidade exige 30 amostras seguidas, e a imagem de ferramenta tem tag fixa.
"""
import importlib.util
import re
import sys
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parents[1]
falhas = []


def checar(condicao, msg):
    print(f"  {'OK' if condicao else 'FALHOU'} {msg}")
    if not condicao:
        falhas.append(msg)


def ler(rel):
    caminho = RAIZ / rel
    checar(caminho.is_file(), f"existe {rel}")
    return caminho.read_text(encoding="utf-8") if caminho.is_file() else ""


# --- Inventário e variáveis (Tarefa 1) ---
exemplo = ler("inventories/prod/hosts.ini.example")
checar(re.search(r"^\[gpu\]\s*$", exemplo, re.M) is not None, "hosts.ini.example tem o grupo [gpu]")
checar(re.search(r"^hulk ansible_host=", exemplo, re.M) is not None, "hulk declarado no grupo gpu")
filhos = re.search(r"^\[homelab:children\]\n((?:[a-z_]+\n)+)", exemplo, re.M)
checar(filhos is not None and "gpu" not in filhos.group(1).split(),
       "gpu NÃO é filho de homelab (01-bootstrap-os e Swarm nunca tocam o hulk)")

d = yaml.safe_load(ler("inventories/prod/group_vars/gpu/10-defaults.yml")) or {}
checar(d.get("gpu_rede_estatica") is False, "gpu_rede_estatica nasce false (rede só gravada na fase B)")
checar("gpu_mac" not in d, "gpu_mac fora do arquivo versionado (o hook barra MAC; vive no 99-local.yml)")
checar(str(d.get("gpu_ip_estatico", "")).startswith("192.0.2."),
       "gpu_ip_estatico fictício (192.0.2.0/24) no arquivo versionado")
checar(d.get("gpu_prefixo") == 24 and isinstance(d.get("gpu_dns"), list), "prefixo 24 e DNS em lista")
checar(isinstance(d.get("gpu_clientes_cidrs"), list) and bool(d.get("gpu_clientes_cidrs")),
       "gpu_clientes_cidrs é lista não vazia")
checar(str(d.get("gpu_driver_pkg", "")).endswith("-open"), "gpu_driver_pkg termina em -open")
versao = re.search(r"nvidia-driver-(\d+)-open", str(d.get("gpu_driver_pkg", "")))
checar(versao is not None and int(versao.group(1)) >= 570, "driver >= 570 (Blackwell)")
checar(d.get("gpu_dir") == "/srv/containers/gpu", "gpu_dir = /srv/containers/gpu")
ferr = d.get("gpu_ferramentas", {})
checar(set(ferr) == {"ollama", "whisper"}, f"ferramentas: ollama e whisper (achado: {sorted(ferr)})")
for nome, f in ferr.items():
    tag = str(f.get("image", "")).rsplit(":", 1)[-1]
    checar(":" in str(f.get("image", "")) and tag != "latest" and not tag.startswith("latest"),
           f"{nome}: imagem com tag fixa (achado: {f.get('image')})")

# --- novos blocos entram aqui (uma tarefa por bloco) ---

if falhas:
    print(f"\n{len(falhas)} falha(s)")
    sys.exit(1)
print("\ngate do hulk OK")
