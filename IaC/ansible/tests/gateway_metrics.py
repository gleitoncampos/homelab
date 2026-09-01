#!/usr/bin/env python3
"""Protege a coleta de métricas de gateway do OPNsense.

`opnsense_gateway_groups` tem DOIS consumidores desde 31/08/2026: o
`config.xml.j2`, que escreve os `<gateway_group>` que governam o failover, e o
`gateway_metrics.sh.j2`, que publica o rótulo de grupo para o Grafana. Uma
fonte, duas saídas — e nada, sem este teste, obriga as duas a concordarem.

Divergir aqui não dá erro em lugar nenhum: o firewall segue fazendo failover
pelo config, e o painel segue desenhando com o rótulo antigo. O gráfico fica
plausível e errado, que é o pior resultado possível para um painel.

CAMADA 1 — ORDEM no playbook.
  O `<job>` de cron que o config instala nomeia a AÇÃO `gatewaymetrics
  collect`. Se o arquivo de actions.d não for escrito ANTES do apply do
  config, o job existe, aparece na GUI e nunca roda. Falha silenciosa. Este
  teste afirma a ordem das tarefas no playbook.

CAMADA 2 — o NOME DA AÇÃO casa entre os dois arquivos.
  O configd deriva o nome da ação do NOME DO ARQUIVO de actions.d, e a
  subcomando da seção `[...]` dentro dele. Renomear qualquer um dos dois
  quebra o job sem aviso.

CAMADA 3 — os grupos rendem IGUAIS nos dois consumidores.
  Cada par (grupo, gateway, tier) tem de aparecer como `<item>GW|TIER</item>`
  no config E como uma linha `opnsense_gateway_group_info` no script.

Sai 0 só quando as três passam.
"""
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.etree import ElementTree

try:
    import yaml
    from jinja2 import Environment, FileSystemLoader, StrictUndefined
except ImportError as exc:  # noqa: BLE001 — dependência ausente é erro claro
    sys.exit(f"dependência ausente: {exc}. Instale PyYAML e Jinja2.")

RAIZ = Path(__file__).resolve().parents[3]
ANSIBLE = RAIZ / "IaC" / "ansible"
TPL_DIR = ANSIBLE / "templates" / "network" / "opnsense"
COMMON = ANSIBLE / "inventories" / "_common" / "opnsense.yml"
FIXTURE = ANSIBLE / "tests" / "fixtures" / "opnsense.yml"
RENDER = ANSIBLE / "tests" / "render_opnsense.py"
PLAYBOOK = ANSIBLE / "playbooks" / "network" / "01-opnsense-setup.yml"

TAREFA_SCRIPT = "Instalar o coletor de métricas de gateway"
TAREFA_ACAO = "Registrar a ação de coleta no configd do OPNsense"
TAREFA_CONFIG = ("Aplicar novo arquivo /conf/config.xml a partir do "
                 "Template Jinja2")

falhas = []


def checar(condicao, mensagem):
    if condicao:
        print(f"  OK {mensagem}")
    else:
        print(f"  FALHOU {mensagem}")
        falhas.append(mensagem)


def carregar_vars():
    dados = {}
    for caminho in (COMMON, FIXTURE):
        dados.update(yaml.safe_load(caminho.read_text(encoding="utf-8")))
    return dados


def render(nome, contexto):
    env = Environment(
        loader=FileSystemLoader(str(TPL_DIR)),
        undefined=StrictUndefined,
        trim_blocks=True,
        keep_trailing_newline=True,
    )
    return env.get_template(nome).render(**contexto)


def _tasks(playbook_path):
    docs = list(yaml.safe_load_all(playbook_path.read_text(encoding="utf-8")))
    saida = []
    for doc in docs:
        if not isinstance(doc, list):
            continue
        for play in doc:
            if not isinstance(play, dict):
                continue
            for chave in ("pre_tasks", "tasks", "post_tasks", "handlers"):
                for task in play.get(chave) or []:
                    if isinstance(task, dict):
                        saida.append(task)
    return saida


# ---------------------------------------------------------------------------
def camada_1_ordem():
    print("== CAMADA 1: a ação é registrada ANTES do apply do config ==")
    nomes = [t.get("name") for t in _tasks(PLAYBOOK)]
    for alvo in (TAREFA_SCRIPT, TAREFA_ACAO, TAREFA_CONFIG):
        checar(alvo in nomes, f"tarefa presente: {alvo!r}")
    if not all(a in nomes for a in (TAREFA_SCRIPT, TAREFA_ACAO, TAREFA_CONFIG)):
        return
    # Sem esta ordem o job de cron nomeia uma ação que ainda não existe. O
    # OPNsense não recusa: grava o job e falha a cada minuto, no log.
    checar(nomes.index(TAREFA_ACAO) < nomes.index(TAREFA_CONFIG),
           "actions.d é escrito ANTES do config.xml")
    checar(nomes.index(TAREFA_SCRIPT) < nomes.index(TAREFA_CONFIG),
           "o script é instalado ANTES do config.xml")


# ---------------------------------------------------------------------------
def camada_2_nome_da_acao(variaveis, config_xml):
    print("\n== CAMADA 2: o nome da ação casa entre actions.d e o cron ==")

    # O configd deriva o nome da ação do NOME DO ARQUIVO: o template se chama
    # actions_<acao>.conf.j2, então a ação é o miolo.
    arquivos = list(TPL_DIR.glob("actions_*.conf.j2"))
    checar(len(arquivos) == 1,
           f"exatamente um template de actions.d (achados: "
           f"{[a.name for a in arquivos]})")
    if len(arquivos) != 1:
        return
    acao = arquivos[0].name[len("actions_"):-len(".conf.j2")]

    corpo = render(arquivos[0].name, variaveis)
    secoes = re.findall(r"^\[([^\]]+)\]", corpo, re.M)
    checar(len(secoes) >= 1, f"actions.d declara ao menos uma seção {secoes}")
    if not secoes:
        return
    esperados = {f"{acao} {s}" for s in secoes}

    comandos = {j.findtext("command")
                for j in config_xml.iter("job")
                if (j.findtext("origin") or "") == acao}
    checar(bool(comandos), f"o config tem job com origin {acao!r}")
    checar(comandos <= esperados,
           f"o `command` do job está entre as ações registradas "
           f"{sorted(esperados)} (veio {sorted(comandos)})")

    # O `command:` do actions.d precisa apontar para o script que o playbook
    # instala; um caminho divergente roda um arquivo que não existe.
    alvo = variaveis.get("opnsense_gateway_metrics_script",
                         "/usr/local/bin/gateway-metrics")
    checar(f"command:{alvo}" in corpo,
           f"actions.d executa o script instalado ({alvo})")


# ---------------------------------------------------------------------------
def camada_3_grupos_concordam(variaveis, config_xml):
    print("\n== CAMADA 3: config e script concordam sobre os grupos ==")

    grupos = variaveis["opnsense_gateway_groups"]
    esperado = {(g["name"], m["gateway"], str(m["tier"]))
                for g in grupos for m in g["membros"]}
    checar(bool(esperado), "há grupos de gateway declarados")

    # --- lado do config: <gateway_group><item>GW|TIER</item> ---------------
    no_config = set()
    for gg in config_xml.iter("gateway_group"):
        nome = gg.findtext("name")
        for item in gg.iter("item"):
            gw, _, tier = (item.text or "").partition("|")
            no_config.add((nome, gw, tier))
    checar(no_config == esperado,
           f"o config.xml rende os pares do dado "
           f"(faltando {sorted(esperado - no_config)}, "
           f"sobrando {sorted(no_config - esperado)})")

    # --- lado do script: linhas opnsense_gateway_group_info ----------------
    script = render("gateway_metrics.sh.j2", variaveis)
    no_script = set()
    for m in re.finditer(
            r'opnsense_gateway_group_info\{group="([^"]+)",'
            r'gateway="([^"]+)",tier="([^"]+)"', script):
        no_script.add(m.groups())
    checar(no_script == esperado,
           f"o script rende os mesmos pares "
           f"(faltando {sorted(esperado - no_script)}, "
           f"sobrando {sorted(no_script - esperado)})")

    # O script é escrito num arquivo e executado pelo cron; sintaxe quebrada
    # falha silenciosamente uma vez por minuto, no log do configd.
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as fh:
        fh.write(script)
        caminho = fh.name
    proc = subprocess.run(["sh", "-n", caminho], capture_output=True,
                          text=True, check=False)
    Path(caminho).unlink(missing_ok=True)
    checar(proc.returncode == 0,
           f"o script renderizado passa em `sh -n` {proc.stderr.strip()[:160]}")

    # Escrita atômica: o node_exporter lê o diretório a cada raspagem, e um
    # arquivo pego pela metade derruba TODO o coletor textfile, não só estas
    # métricas. `mv` depois de escrever no temporário é o que evita isso.
    checar(re.search(r"mv\s+-f?\s*\"?\$TMP", script) is not None,
           "o script escreve em temporário e move (escrita atômica)")
    # Descoberta por glob, não por lista fixa: gateway novo aparece sozinho.
    checar("/var/run/dpinger_*.sock" in script,
           "o script descobre os gateways por glob, sem lista fixa")


def main():
    variaveis = carregar_vars()

    with tempfile.TemporaryDirectory() as tmp:
        saida = Path(tmp) / "config.xml"
        proc = subprocess.run(
            [sys.executable, str(RENDER),
             "--template", str(TPL_DIR / "config.xml.j2"),
             "--common", str(COMMON), "--fixture", str(FIXTURE),
             "--out", str(saida)],
            capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            print(f"  FALHOU render do config: {proc.stderr.strip()[:200]}")
            return 1
        config_xml = ElementTree.parse(saida).getroot()

    camada_1_ordem()
    camada_2_nome_da_acao(variaveis, config_xml)
    camada_3_grupos_concordam(variaveis, config_xml)

    print()
    if falhas:
        print(f"  {len(falhas)} FALHA(S)")
        return 1
    print("  MÉTRICAS DE GATEWAY OK (ordem no playbook; ação casa; "
          "config e script concordam sobre os grupos)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
