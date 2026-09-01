#!/usr/bin/env python3
"""Protege a coleta de métricas de gateway do OPNsense.

`opnsense_gateway_groups` tem DOIS consumidores desde 31/08/2026: o
`config.xml.j2`, que escreve os `<gateway_group>` que governam o failover, e o
`gateway_metrics.sh.j2`, que publica o rótulo de grupo para o Grafana. Uma
fonte, duas saídas — e nada, sem este teste, obriga as duas a concordarem.

Divergir aqui não dá erro em lugar nenhum: o firewall segue fazendo failover
pelo config, e o painel segue desenhando com o rótulo antigo. O gráfico fica
plausível e errado, que é o pior resultado possível para um painel.

CAMADA 1 — o agendamento NÃO depende do config.xml.
  A primeira versão agendava a coleta como `<job>` no config.xml — caminho
  nativo do OPNsense e visível na GUI — e isso fazia um coletor de métricas
  depender da reescrita COMPLETA do firewall para subir ou mudar de intervalo.
  O drop-in de `/usr/local/etc/cron.d/` desacopla. Este teste afirma que o
  desacoplamento continua: nenhuma menção ao coletor no config renderizado.

CAMADA 2 — o cron chama o script que o playbook instala.
  Caminho divergente entre o `dest` da tarefa e o comando do cron roda um
  arquivo que não existe, uma vez por minuto, calado.

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
TAREFA_CRON = "Agendar a coleta de métricas de gateway"

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
def camada_1_desacoplado(config_xml):
    print("== CAMADA 1: o agendamento não depende do config.xml ==")
    nomes = [t.get("name") for t in _tasks(PLAYBOOK)]
    for alvo in (TAREFA_SCRIPT, TAREFA_CRON):
        checar(alvo in nomes, f"tarefa presente: {alvo!r}")

    # ⚠️ A invariante inteira desta camada. Um `<job>` de cron no config faria
    # o coletor renascer acoplado ao apply completo — que é o que o desenho
    # atual existe para evitar, e que nesta base está travado por outros itens.
    texto = ElementTree.tostring(config_xml, encoding="unicode")
    for marca in ("gatewaymetrics", "gateway-metrics", "gateway_metrics"):
        checar(marca not in texto,
               f"o config.xml renderizado NÃO menciona {marca!r}")


# ---------------------------------------------------------------------------
def camada_2_cron(variaveis):
    print("\n== CAMADA 2: o cron chama o script instalado ==")

    alvo = variaveis.get("opnsense_gateway_metrics_script",
                         "/usr/local/bin/gateway-metrics")
    cron = render("gateway_metrics.cron.j2", variaveis)

    linhas = [ln for ln in cron.splitlines()
              if ln.strip() and not ln.lstrip().startswith("#")]
    checar(len(linhas) == 1, f"o drop-in tem uma linha de job (achadas {len(linhas)})")
    if not linhas:
        return
    campos = linhas[0].split()
    # minuto hora dia mes semana quem comando — a coluna `who` existe no
    # formato do /etc/crontab e NÃO no crontab de usuário. Errar isso faz o
    # cron interpretar `root` como o comando.
    checar(len(campos) >= 7, f"formato com coluna `who` (campos: {len(campos)})")
    if len(campos) >= 7:
        checar(campos[5] == "root", f"roda como root (veio {campos[5]!r})")
        checar(campos[6] == alvo,
               f"o comando é o script instalado {alvo!r} (veio {campos[6]!r})")

    # O `dest` da tarefa do playbook tem de ser o mesmo caminho.
    tarefa = next((x for x in _tasks(PLAYBOOK)
                   if x.get("name") == TAREFA_SCRIPT), None)
    if tarefa:
        dest = (tarefa.get("ansible.builtin.template") or {}).get("dest", "")
        checar(alvo in dest,
               f"o playbook instala o script em {alvo!r} (dest: {dest!r})")


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

    # O tier sai DUAS vezes: rótulo em `group_info` e VALOR em
    # `opnsense_gateway_tier`. Não é redundância — o PromQL não converte
    # rótulo em valor, e "qual WAN está ativa" é um `min by (group)` do tier.
    # As duas formas precisam concordar, senão o painel mostra uma prioridade
    # e o firewall obedece outra.
    como_valor = {(m.group(1), m.group(2), m.group(3)) for m in re.finditer(
        r'opnsense_gateway_tier\{group="([^"]+)",gateway="([^"]+)"\}\s+(\d+)',
        script)}
    checar(como_valor == esperado,
           f"o tier como VALOR bate com o tier como rótulo "
           f"(faltando {sorted(esperado - como_valor)}, "
           f"sobrando {sorted(como_valor - esperado)})")

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

    camada_1_desacoplado(config_xml)
    camada_2_cron(variaveis)
    camada_3_grupos_concordam(variaveis, config_xml)

    print()
    if falhas:
        print(f"  {len(falhas)} FALHA(S)")
        return 1
    print("  MÉTRICAS DE GATEWAY OK (agendamento desacoplado do config; "
          "cron chama o script; config e script concordam sobre os grupos)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
