#!/usr/bin/env python3
"""Renderiza os templates da stack de monitoramento fora do Ansible e checa
o que o gate não vê.

Motivo: `yamllint` ignora `.j2` e `docker stack config` só olha `compose.yml`.
Os templates desta stack são carregados e nada os validava. Um laço Jinja em
volta de um bloco `local.file_match` chegou a gerar dois componentes com o
mesmo rótulo, o que faz o Alloy recusar a configuração inteira; o defeito era
invisível com um host por grupo, que é como a produção está hoje.

Por isso a fixture tem DOIS hosts em cada grupo: é a única forma de o laço
mostrar o problema.

⚠️ O terceiro template validado aqui — `config-rsyslog.alloy.j2`, do coletor
intermediário — saiu em 07/09/2026, junto com o próprio coletor: os cinco
equipamentos passaram a enviar syslog direto para a stack, e um `tcpdump` de
40 s no CT não viu um pacote sequer.
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
# O AP saiu para a VLAN de gerência e o inventário segue no endereço antigo:
# o alvo tem de vir de `openwrt_mgmt_ip` (13/10/2026, alvo `down` por timeout).
GERENCIA_AP = {"ap-um": "192.0.2.130", "ap-dois": "192.0.2.131"}
CONTEXTO = {
    "groups": GRUPOS,
    "hostvars": {h: {"ansible_host": ip} for h, ip in ENDERECOS.items()},
    "prometheus_scrape_interval": "15s",
    "nut_ups_name": "nobreak",
    "loki_retention_period": "720h",
    "loki_chunk_encoding": "gzip",
}

# Em produção a variável vem do group_vars/network, que inclui o firewall:
# ele também a enxerga e NÃO pode ser raspado nela.
for _h in GRUPOS["openwrt"] + GRUPOS["opnsense"]:
    CONTEXTO["hostvars"][_h]["openwrt_mgmt_ip"] = GERENCIA_AP.get(_h, "192.0.2.199")

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
    for host in GRUPOS["omv"] + GRUPOS["proxmox"]:
        checar(ENDERECOS[host] in alvos, f"{host} virou alvo")
    ow_job = next(j for j in prom["scrape_configs"] if j["job_name"] == "openwrt")
    ow_alvos = ow_job["static_configs"][0]["targets"]
    rel = " ".join(r["regex"] for r in ow_job["relabel_configs"])
    for host in GRUPOS["openwrt"]:
        checar(f"{GERENCIA_AP[host]}:9100" in ow_alvos and ENDERECOS[host] not in " ".join(ow_alvos),
               f"{host} é alvo pelo endereço de gerência")
        checar(re.escape(GERENCIA_AP[host]) in rel, f"{host}: relabel do instance casa o endereço de gerência")
    fw_job = next(j for j in prom["scrape_configs"] if j["job_name"] == "opnsense")
    checar(f"{ENDERECOS['fw-um']}:9100" in fw_job["static_configs"][0]["targets"],
           "firewall segue raspado no próprio endereço")
    # ⚠️ A checagem acima ("virou alvo") passava mesmo quando os hipervisores
    # NÃO tinham node_exporter: eles já apareciam pelo job `pve`, que fala com a
    # API do PVE e cobre convidado e storage, não o sistema operacional do host.
    # Esta confere o job certo, na porta certa.
    node_job = next(j for j in prom["scrape_configs"]
                    if j["job_name"] == "node_exporter")
    node_alvos = " ".join(node_job["static_configs"][0]["targets"])
    for host in GRUPOS["omv"] + GRUPOS["proxmox"]:
        checar(f"{ENDERECOS[host]}:9100" in node_alvos,
               f"{host} é alvo do job node_exporter")

    rotulos = re.findall(r"replacement:\s*(\S+)", prom_txt)
    checar(
        all(r.startswith('"') for r in rotulos),
        "todo `replacement:` está entre aspas (hostname não vira booleano)",
    )

print("\n== loki-config.yml.j2 ==")
loki_txt = render("loki-config.yml.j2")
try:
    loki = yaml.safe_load(loki_txt)
    checar(True, "YAML válido")
    checar(loki["limits_config"]["retention_period"] == "720h",
           "retenção vem do inventário, não cravada no arquivo")
    checar(loki["ingester"]["chunk_encoding"] == "gzip",
           "compressão dos chunks declarada")
    checar(loki["schema_config"]["configs"][0]["store"] == "tsdb",
           "schema tsdb preservado")
except yaml.YAMLError as exc:  # pragma: no cover
    checar(False, f"YAML válido ({exc})")

# ----------------------------------------------------------------------
# O compose NÃO é template, mas depende de duas coisas que vivem fora dele:
# as variáveis que o playbook injeta, e os arquivos que o playbook escreve no
# host. As duas já quebraram a stack em produção, cada uma do seu jeito.
#
# ⚠️ Leia a diferença entre as três asserções abaixo, porque elas NÃO são a
# mesma checagem em três formas:
#
#   1. e 2. pegam DESACORDO entre compose e playbook — um lado tem, o outro
#      não. Não pegam os dois lados esquecendo a mesma coisa.
#   3. pega exatamente isso: uma exigência que só a IMAGEM conhece, e que
#      nenhum dos dois arquivos do repositório teria como deduzir sozinho.
#
# A distinção não é acadêmica. Foi (3) que aconteceu: nem o compose nem o
# playbook mencionavam `PVE_USER`, e por isso (1) e (2) ficariam verdes para
# sempre. Escrever só (1) e (2) e declarar o caso coberto seria o defeito que
# o guia chama de "teste verde não é o mesmo que alvo testado".
# ----------------------------------------------------------------------
print("\n== contrato entre compose.yml e o playbook ==")
compose_txt = (SWARM / "compose.yml").read_text(encoding="utf-8")
playbook_txt = (RAIZ / "IaC/ansible/playbooks/03-stack-monitoring.yml").read_text(encoding="utf-8")
comum_txt = (RAIZ / "IaC/ansible/inventories/_common/all.yml").read_text(encoding="utf-8")

# ⚠️ O pareamento é por bloco de `stack_name:`: o playbook já implantou MAIS DE
# UMA stack (`monitoring` e `statping`, este removido em 13/10/2026), cada uma
# com seu compose e seu `stack_env`. Varrer o arquivo inteiro de uma vez
# misturava as duas e acusava desacordo que não existia.
blocos = re.split(r"^\s*stack_name:\s*", playbook_txt, flags=re.M)[1:]
pares = []
for bloco in blocos:
    caminho = re.search(r"^\s*stack_compose_path:\s*\"([^\"]+)\"", bloco, re.M)
    if not caminho:
        continue
    nomes = set(re.findall(r"^\s*-\s+name:\s*\"([A-Z_][A-Z0-9_]*)\"", bloco, re.M))
    pares.append((caminho.group(1), nomes))

checar(len(pares) >= 1, f"blocos de deploy de stack encontrados no playbook ({len(pares)})")
for caminho, fornecidas in pares:
    arq = RAIZ / caminho
    if not arq.is_file():
        checar(False, f"compose referenciado pelo playbook existe ({caminho})")
        continue
    txt = arq.read_text(encoding="utf-8")
    usadas = set(re.findall(r"\$\{([A-Z_][A-Z0-9_]*)\}", txt))

    # (1) Usada no compose e nunca injetada: a stack sobe com o valor vazio,
    #     sem erro nenhum na subida.
    faltando = usadas - fornecidas
    checar(not faltando, f"{caminho}: toda variável usada é injetada ({len(usadas)} usadas)")
    if faltando:
        print(f"     usadas e não injetadas: {sorted(faltando)}")

    # (2) Injetada e nunca usada: configuração morta. Quem a edita pensa que
    #     mudou algo, e não mudou.
    sobrando = fornecidas - usadas
    checar(not sobrando, f"{caminho}: toda variável injetada é usada ({len(fornecidas)} injetadas)")
    if sobrando:
        print(f"     injetadas e não usadas: {sorted(sobrando)}")

# (3) Exigência da IMAGEM, que nenhum dos dois arquivos revela.
#     `pve_exporter/cli.py` usa a PRESENÇA de `PVE_USER` como interruptor
#     entre configurar por ambiente e ler `/etc/prometheus/pve.yml` — arquivo
#     que esta stack não monta. Sem a variável ele morre com
#     `FileNotFoundError`, mensagem que não menciona credencial nenhuma.
#
#     ⚠️ `config_from_env` aceita DOIS modos e o exportador não avisa qual
#     está em uso: `PVE_PASSWORD`, ou o par `PVE_TOKEN_NAME`/`PVE_TOKEN_VALUE`.
#     Declarar metade de um e metade do outro não dá erro de configuração —
#     dá 401 em toda coleta, com o serviço em `1/1`. Por isso a asserção cobra
#     um modo INTEIRO, e reprova a mistura.
#
#     Hoje o modo é senha, e é temporário: o alvo é token, e a credencial dele
#     não existe em hipervisor nenhum (item 1.2.16 do roadmap). Ao reverter,
#     troque `MODO_PVE` para "token" — a asserção acompanha.
MODO_PVE = "senha"
CAMPOS_PVE = {
    "senha": ("PVE_USER", "PVE_PASSWORD"),
    "token": ("PVE_USER", "PVE_TOKEN_NAME", "PVE_TOKEN_VALUE"),
}
bloco_pve = re.search(r"^  prometheus-pve-exporter:.*?(?=^  \S|\Z)", compose_txt, re.M | re.S)
checar(bloco_pve is not None, "serviço prometheus-pve-exporter presente no compose")
if bloco_pve:
    corpo = bloco_pve.group(0)
    # ⚠️ Casar a CHAVE, não a string solta: os comentários deste bloco citam os
    # nomes das variáveis várias vezes, e um `in` simples ficava verde com a
    # declaração apagada — a asserção era satisfeita pela prosa que a explica.
    def declarada(var):
        return re.search(rf"^\s+{var}:\s", corpo, re.M) is not None

    for var in CAMPOS_PVE[MODO_PVE]:
        checar(declarada(var), f"{var} declarada no exportador (modo {MODO_PVE})")

    outro = "token" if MODO_PVE == "senha" else "senha"
    intrusas = [v for v in CAMPOS_PVE[outro] if v not in CAMPOS_PVE[MODO_PVE] and declarada(v)]
    checar(not intrusas,
           f"nenhuma variável do modo {outro} misturada ao modo {MODO_PVE}")
    if intrusas:
        print(f"     intrusas: {sorted(intrusas)}")

# ----------------------------------------------------------------------
# As portas publicadas pela stack e os ouvintes declarados na configuração do
# Alloy são DOIS arquivos que precisam concordar, e nada os obrigava a isso.
#
# ⚠️ Os três modos de falha são todos silenciosos:
#   - porta publicada sem ouvinte  -> o pacote chega ao host e morre ali;
#   - ouvinte sem porta publicada  -> o processo escuta e ninguém alcança;
#   - dois FORMATOS na mesma porta -> o ouvinte descarta o que não casa, sem
#     erro em ponta nenhuma. Foi por isso que a coleta ficou dividida em duas
#     portas, e é a divisão que este bloco protege.
# ----------------------------------------------------------------------
print("\n== portas da stack x ouvintes do Alloy ==")
alloy_cfg = (SWARM / "config.alloy").read_text(encoding="utf-8")

bloco_alloy = re.search(r"^  alloy:.*?(?=^  \S|\Z)", compose_txt, re.M | re.S)
checar(bloco_alloy is not None, "serviço alloy presente no compose")

publicadas = set()
if bloco_alloy:
    for porta, proto in re.findall(r'-\s+"(\d+):\d+/(tcp|udp)"', bloco_alloy.group(0)):
        publicadas.add((porta, proto))

# Cada `listener { ... }` traz address, protocol e syslog_format.
ouvintes = set()
formato_por_porta = {}
for corpo in re.findall(r"listener\s*\{(.*?)\n  \}", alloy_cfg, re.S):
    end = re.search(r'address\s*=\s*"[^"]*:(\d+)"', corpo)
    proto = re.search(r'protocol\s*=\s*"(\w+)"', corpo)
    fmt = re.search(r'syslog_format\s*=\s*"(\w+)"', corpo)
    if not (end and proto):
        continue
    ouvintes.add((end.group(1), proto.group(1)))
    if fmt:
        formato_por_porta.setdefault(end.group(1), set()).add(fmt.group(1))

checar(bool(publicadas), f"portas publicadas encontradas no compose ({len(publicadas)})")
checar(bool(ouvintes), f"ouvintes encontrados no config.alloy ({len(ouvintes)})")

sem_ouvinte = publicadas - ouvintes
checar(not sem_ouvinte, "toda porta publicada tem ouvinte correspondente")
if sem_ouvinte:
    print(f"     publicadas e sem ouvinte: {sorted(sem_ouvinte)}")

sem_porta = ouvintes - publicadas
checar(not sem_porta, "todo ouvinte tem porta publicada")
if sem_porta:
    print(f"     escutando e não publicadas: {sorted(sem_porta)}")

# ⚠️ O invariante que mais importa: uma porta, um formato.
misturadas = {p: sorted(f) for p, f in formato_por_porta.items() if len(f) > 1}
checar(not misturadas, f"cada porta declara UM formato de syslog só ({len(formato_por_porta)} portas)")
if misturadas:
    print(f"     portas com formato misturado: {misturadas}")

# Todo bind mount de ARQUIVO sob /srv/containers precisa de um `dest`
# correspondente em `monitoring_sidecar_files`. Diretório não entra: é criado
# pela task de mkdir, ou é estado do serviço. Sem isto, um arquivo renomeado no
# repositório deixa o host com o nome velho e o Swarm recusa a tarefa com
# `bind source path does not exist` — foi o que pôs o Grafana em 0/1.
montados = {
    m for m in re.findall(r"-\s+(/srv/containers/[^\s:]+):", compose_txt)
    if Path(m).suffix
}
escritos = set(re.findall(r"^\s*dest:\s*\"?(/srv/containers/[^\s\"]+)\"?", comum_txt, re.M))
orfaos = montados - escritos
checar(not orfaos, f"todo arquivo montado do host é escrito pelo playbook ({len(montados)} arquivos)")
if orfaos:
    print(f"     montados e nunca escritos: {sorted(orfaos)}")

print()
if falhas:
    print(f"  {len(falhas)} FALHA(S)")
    sys.exit(1)
print("  TODOS OS RENDERS DE MONITORAMENTO PASSARAM")
