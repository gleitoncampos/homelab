#!/usr/bin/env python3
"""Renderiza os templates da stack de monitoramento fora do Ansible e checa
o que o gate não vê.

Motivo: `yamllint` ignora `.j2` e `docker stack config` só olha `compose.yml`.
Os dois templates desta stack são carregados — um vira a configuração do
Prometheus, o outro a do Alloy do CT de rsyslog — e nada os validava. Um laço
Jinja em volta de um bloco `local.file_match` chegou a gerar dois componentes
com o mesmo rótulo, o que faz o Alloy recusar a configuração inteira; o
defeito era invisível com um host por grupo, que é como a produção está hoje.

Por isso a fixture tem DOIS hosts em cada grupo: é a única forma de o laço
mostrar o problema.
"""
import re
import sys
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

RAIZ = Path(__file__).resolve().parents[3]
SWARM = RAIZ / "docker" / "monitoring" / "swarm"

# Faixas de documentação da RFC 5737 e domínios .example — nunca dado real.
GRUPOS = {
    "homelab_managers": ["mgr-um", "mgr-dois"],
    "omv": ["nas-um", "nas-dois"],
    "openwrt": ["ap-um", "ap-dois"],
    "opnsense": ["fw-um"],
    "proxmox": ["hv-um", "hv-dois"],
}
ENDERECOS = {
    "mgr-um": "192.0.2.10", "mgr-dois": "192.0.2.11",
    "nas-um": "192.0.2.20", "nas-dois": "192.0.2.21",
    "ap-um": "192.0.2.30", "ap-dois": "192.0.2.31",
    "fw-um": "192.0.2.1",
    "hv-um": "192.0.2.40", "hv-dois": "192.0.2.41",
}
CONTEXTO = {
    "groups": GRUPOS,
    "hostvars": {h: {"ansible_host": ip} for h, ip in ENDERECOS.items()},
    "prometheus_scrape_interval": "15s",
    "nut_ups_name": "nobreak",
    "loki_push_url": "http://192.0.2.10:3100/loki/api/v1/push",
}

falhas = []


def checar(condicao, mensagem):
    if condicao:
        print(f"  OK {mensagem}")
    else:
        print(f"  FALHOU {mensagem}")
        falhas.append(mensagem)


def render(nome):
    # trim_blocks/lstrip_blocks iguais ao padrão do Ansible para `.j2`.
    env = Environment(
        loader=FileSystemLoader(str(SWARM)),
        undefined=StrictUndefined,
        trim_blocks=True,
        keep_trailing_newline=True,
    )
    # `regex_escape` é filtro do Ansible, não do Jinja puro. Sem registrá-lo
    # aqui o teste falharia por motivo que não existe na execução real.
    env.filters["regex_escape"] = re.escape
    return env.get_template(nome).render(**CONTEXTO)


print("\n== prometheus.yml.j2 ==")
prom_txt = render("prometheus.yml.j2")
try:
    prom = yaml.safe_load(prom_txt)
    checar(True, "YAML válido")
except yaml.YAMLError as exc:  # pragma: no cover - só aparece quando quebra
    prom = None
    checar(False, f"YAML válido ({exc})")

if prom:
    jobs = [j["job_name"] for j in prom["scrape_configs"]]
    checar(len(jobs) == len(set(jobs)), f"job_name sem duplicata: {jobs}")
    checar("nut" in jobs, "job do nobreak presente")
    alvos = " ".join(
        t for j in prom["scrape_configs"] for t in j["static_configs"][0]["targets"]
    )
    for host in GRUPOS["omv"] + GRUPOS["openwrt"] + GRUPOS["proxmox"]:
        checar(ENDERECOS[host] in alvos, f"{host} virou alvo")
    rotulos = re.findall(r"replacement:\s*(\S+)", prom_txt)
    checar(
        all(r.startswith('"') for r in rotulos),
        "todo `replacement:` está entre aspas (hostname não vira booleano)",
    )

print("\n== config-rsyslog.alloy.j2 ==")
alloy_txt = render("config-rsyslog.alloy.j2")
# Rótulo de componente do Alloy: `tipo.subtipo "rotulo" {`
componentes = re.findall(r"^([a-z_.]+)\s+\"([^\"]+)\"\s*\{", alloy_txt, re.M)
repetidos = {c for c in componentes if componentes.count(c) > 1}
checar(not repetidos, f"nenhum componente com rótulo repetido ({len(componentes)} componentes)")
if repetidos:
    print(f"     repetidos: {sorted(repetidos)}")
checar(
    alloy_txt.count("/var/log/rsyslog/opnsense/*.log") == 1,
    "glob do OPNsense aparece uma vez só (linha não é lida em dobro)",
)
for host in GRUPOS["openwrt"] + GRUPOS["omv"] + GRUPOS["proxmox"]:
    checar(f"{host}.log" in alloy_txt, f"caminho de log de {host} presente")
# Não se checa vírgula sobrando antes de `)`: a configuração que roda em
# produção tem uma em `array.concat(...)` e o Alloy a aceita há meses. Uma
# asserção que reprova o known-good é asserção errada.

print()
if falhas:
    print(f"  {len(falhas)} FALHA(S)")
    sys.exit(1)
print("  TODOS OS RENDERS DE MONITORAMENTO PASSARAM")
