#!/usr/bin/env python3
"""Renderiza as rotas do Traefik para back-ends fora do Swarm e trava o
acoplamento entre o nome da rota e a variável que renomeia o equipamento.

Motivo de existir: o `compose.yml` do Traefik monta
`/srv/containers/traefik/config` como file provider desde sempre, e o
repositório nunca teve conteúdo para ele. As rotas de hipervisor, NAS,
firewall e AP existiam só no host, escritas à mão. Reconstruir do zero subia
um proxy com o diretório vazio — sem erro, porque file provider vazio é
configuração válida — e derrubava de uma vez o acesso a tudo que não é
container.

⚠️ O gate central é o CONTROLE NEGATIVO do nome: a fixture dá ao firewall e ao
AP um `*_hostname` DIFERENTE do nome de inventário. Se o template voltar a
usar o nome de inventário, o teste fica vermelho. É a única forma de provar
que renomear o equipamento pelo IaC leva a rota junto — a alternativa
silenciosa é um Host() que ninguém atende, e regra que não casa nada é regra
válida para o Traefik.
"""
import re
import sys
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

RAIZ = Path(__file__).resolve().parents[3]
SWARM = RAIZ / "docker" / "traefik" / "swarm"
PLAYBOOK = RAIZ / "IaC" / "ansible" / "playbooks" / "02-bootstrap-cluster.yml"
COMUM = RAIZ / "IaC" / "ansible" / "inventories" / "_common" / "all.yml"

# Faixa de documentação da RFC 5737 e domínio .example — nunca dado real.
# Dois hosts em proxmox: um laço com um só host esconde colisão de chave.
GRUPOS = {
    "opnsense": ["fw-um"],
    "openwrt": ["ap-um"],
    "proxmox": ["hv-um", "hv-dois"],
    "omv": ["nas-um"],
}
# ⚠️ O nome de inventário e o `*_hostname` são DE PROPÓSITO diferentes.
IDENTIDADE = {"fw-um": "firewall-renomeado", "ap-um": "ponto-de-acesso", "nas-um": "nas-renomeado"}
ENDERECOS = {
    "fw-um": "192.0.2.1", "ap-um": "192.0.2.2",
    "hv-um": "192.0.2.40", "hv-dois": "192.0.2.41",
    "nas-um": "192.0.2.20",
}
# O AP é alcançado pela gerência, não pelo `ansible_host` (endereço antigo).
GERENCIA_AP = "192.0.2.3"
CONTEXTO = {
    "groups": GRUPOS,
    "hostvars": {
        "fw-um": {"ansible_host": ENDERECOS["fw-um"],
                  "opnsense_hostname": IDENTIDADE["fw-um"]},
        "ap-um": {"ansible_host": ENDERECOS["ap-um"],
                  "openwrt_mgmt_ip": GERENCIA_AP,
                  "openwrt_hostname": IDENTIDADE["ap-um"]},
        "hv-um": {"ansible_host": ENDERECOS["hv-um"]},
        "hv-dois": {"ansible_host": ENDERECOS["hv-dois"]},
        "nas-um": {"ansible_host": ENDERECOS["nas-um"],
                   "omv_hostname": IDENTIDADE["nas-um"]},
    },
    "homelab_domain": "casa.example",
    "traefik_certresolver": "cloudflare",
    "traefik_porta_proxmox": 8006,
    "traefik_porta_omv": 80,
    "traefik_porta_openwrt": 80,
}

falhas = []


def checar(condicao, mensagem):
    if condicao:
        print(f"  OK {mensagem}")
    else:
        print(f"  FALHOU {mensagem}")
        falhas.append(mensagem)


def render(**extra):
    env = Environment(
        loader=FileSystemLoader(str(SWARM)),
        undefined=StrictUndefined,
        trim_blocks=True,
        keep_trailing_newline=True,
    )
    return env.get_template("rotas.yml.j2").render(**{**CONTEXTO, **extra})


print("\n== rotas.yml.j2 ==")
texto = render()

try:
    doc = yaml.safe_load(texto)
    checar(True, "YAML válido")
except yaml.YAMLError as exc:
    checar(False, f"YAML válido ({exc})")
    print()
    print("  1 FALHA(S)")
    sys.exit(1)

http = (doc or {}).get("http") or {}
routers = http.get("routers") or {}
services = http.get("services") or {}
transportes = http.get("serversTransports") or {}

# ⚠️ `serversTransports` fora de `http:` é o modo de falha da indentação: o
# Traefik aceita o arquivo e ignora a chave, e o back-end volta a falhar por
# certificado autoassinado longe da causa.
checar("serversTransports" not in doc,
       "serversTransports está DENTRO de http:, não na raiz")

esperados = {
    IDENTIDADE["fw-um"], IDENTIDADE["ap-um"], "hv-um", "hv-dois", IDENTIDADE["nas-um"],
}
checar(set(routers) == esperados,
       f"uma rota por equipamento dos quatro grupos ({len(esperados)})")
if set(routers) != esperados:
    print(f"     obtido: {sorted(routers)}")

# O CONTROLE NEGATIVO. Se o template voltar a usar o nome de inventário, estes
# dois nomes aparecem no render e o gate fica vermelho.
for inventario in ("fw-um", "ap-um", "nas-um"):
    checar(inventario not in texto,
           f"o nome de inventário '{inventario}' NÃO aparece no render "
           f"(a rota segue *_hostname, não o inventário)")

for nome, ident in IDENTIDADE.items():
    regra = (routers.get(ident) or {}).get("rule", "")
    checar(regra == f"Host(`{ident}.casa.example`)",
           f"Host() de {nome} usa a identidade '{ident}'")

for nome, roteador in routers.items():
    checar(roteador.get("service") in services,
           f"router {nome} aponta para um service que existe")
    checar(roteador.get("entryPoints") == ["websecure"],
           f"router {nome} publica só em websecure")
    checar((roteador.get("tls") or {}).get("certResolver") == "cloudflare",
           f"router {nome} usa o certresolver da variável")

# A rota do AP vai à gerência; com o `ansible_host` antigo dava timeout
# (14/10/2026).
_ap = ((services.get(IDENTIDADE["ap-um"]) or {}).get("loadBalancer") or {}).get("servers") or [{}]
checar(_ap[0].get("url") == f"http://{GERENCIA_AP}:80",
       "a rota do AP aponta para openwrt_mgmt_ip")

# ⚠️ Hipervisor e firewall apresentam certificado autoassinado; NAS e AP são
# HTTP puro. Um `insecureSkipVerify` sobrando é confiança concedida sem
# necessidade, e é o tipo de coisa que ninguém revisa depois.
inseguros = set(transportes)
checar(inseguros == {IDENTIDADE["fw-um"], "hv-um", "hv-dois"},
       "insecureSkipVerify só no firewall e nos hipervisores")
if inseguros != {IDENTIDADE["fw-um"], "hv-um", "hv-dois"}:
    print(f"     obtido: {sorted(inseguros)}")

# O transporte nomeado só restringe a confiança se não houver um GLOBAL por
# cima: `--serverstransport.insecureSkipVerify=true` no compose valia para
# todo back-end e anulava a asserção acima (15/10/2026).
_compose = (RAIZ / "docker/traefik/swarm/compose.yml").read_text()
checar(not re.search(r"^\s*-\s*--serverstransport\.insecureskipverify=true", _compose, re.I | re.M),
       "compose do Traefik sem insecureSkipVerify global")

for nome, servico in services.items():
    lb = servico.get("loadBalancer") or {}
    st = lb.get("serversTransport")
    checar(st is None or st in transportes,
           f"service {nome} referencia um serversTransport declarado")
    urls = [s.get("url", "") for s in (lb.get("servers") or [])]
    checar(len(urls) == 1 and urls[0].startswith(("http://", "https://")),
           f"service {nome} tem exatamente um back-end com esquema explícito")

# Grupo ausente do inventário não pode quebrar o render: o `| default([])` de
# cada laço é o que permite rodar o playbook num ambiente sem NAS, por exemplo.
try:
    vazio = render(groups={}, hostvars={})
    doc_vazio = yaml.safe_load(vazio) or {}
    checar(not ((doc_vazio.get("http") or {}).get("routers")),
           "inventário sem nenhum dos quatro grupos rende arquivo sem rota, e não erro")
except Exception as exc:  # noqa: BLE001 - o ponto do teste é não estourar
    checar(False, f"inventário vazio rende sem erro ({exc})")

print("\n== playbook e inventário ==")
playbook_txt = PLAYBOOK.read_text(encoding="utf-8")
comum_txt = COMUM.read_text(encoding="utf-8")

# O gate EFETIVO da identidade é o assert do playbook; aqui se afirma que ele
# continua lá e mantém a forma. `10-defaults.yml` é versionado e SEMPRE entrega
# valor, então StrictUndefined nunca dispara — sem este assert, o render sai
# com `fw.example` e ninguém vê.
for var in ("opnsense_hostname", "openwrt_hostname"):
    checar(
        re.search(rf"endswith\('\.example'\)[\s\S]{{0,80}}", playbook_txt) is not None
        and f"hostvars[item].{var}" in playbook_txt,
        f"o playbook recusa {var} terminado em .example",
    )

checar("traefik_sidecar_files" in playbook_txt,
       "o playbook renderiza a partir de traefik_sidecar_files")

# Uma fonte, dois consumidores: o destino declarado no inventário tem de cair
# dentro do diretório que o compose monta como file provider.
compose_txt = (SWARM / "compose.yml").read_text(encoding="utf-8")
montagem = re.search(r"-\s+(/srv/containers/traefik/config):/config", compose_txt)
checar(montagem is not None, "o compose monta /srv/containers/traefik/config em /config")
destinos = re.findall(r"^\s*dest:\s*\"(/srv/containers/traefik/config/[^\"]+)\"",
                      comum_txt, re.M)
checar(len(destinos) >= 1,
       "traefik_sidecar_files escreve dentro do diretório montado")

# ⚠️ TRÊS níveis acima de playbooks/ é a raiz do repositório. Com dois, o
# caminho vira `IaC/docker`, que não existe, e a cópia falha por arquivo
# inexistente — defeito que ficou anos sem aparecer porque a stack já estava
# no ar e ninguém reexecutou o play.
checar('docker_dir: "{{ playbook_dir }}/../../../docker"' in playbook_txt,
       "docker_dir aponta para a raiz do repositório (três níveis)")

print()
if falhas:
    print(f"  {len(falhas)} FALHA(S)")
    sys.exit(1)
print("  TODOS OS GATES DAS ROTAS DO TRAEFIK PASSARAM")
