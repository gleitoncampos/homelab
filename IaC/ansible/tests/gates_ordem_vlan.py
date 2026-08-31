#!/usr/bin/env python3
"""Protege os gates da ORDEM DE APLICAÇÃO da migração de VLANs.

A migração tem elos que só funcionam em sequência (ver
`.local/docs/TODO-E-ROADMAP.md`, item 1.1.8). Um apply fora de ordem não dá
erro: ele grava configuração que não casa pacote nenhum, ou põe um
equipamento numa VLAN que ainda não existe do outro lado. Por isso a ordem
não vive numa tabela para alguém lembrar — vive como `assert` nos playbooks,
avaliado antes da primeira escrita.

Os gates EFETIVOS são os asserts dos playbooks. Este teste NÃO é o gate: ele
prova que os gates continuam lá e com a forma certa, e que o template de fato
cria o que o gate 1 pressupõe. Rodável na CI, sem host nenhum.

CAMADA 1 — asserção ESTRUTURAL sobre os playbooks.
  Faz parse do YAML de `01-opnsense-setup.yml` e `02-openwrt-setup.yml`,
  localiza cada task de gate pelo `name` e afirma que o corpo dela ainda
  carrega a marca da sua invariante. Apagar ou descaracterizar um gate deixa
  o `make check` VERMELHO. Sem esta camada o teste validaria só uma cópia da
  lógica, e remover o gate real passaria verde — o defeito que o CLAUDE.md
  chama de "teste verde não é o mesmo que alvo testado".

CAMADA 2 — render, para amarrar o gate 1 ao TEMPLATE.
  O gate 1 do playbook compara as interfaces que as regras citam contra a
  lista derivada de `vlans`. Isso pressupõe que `_interfaces.xml.j2` emite uma
  interface por entrada de `vlans` — se o template parar de emitir, o gate
  segue aprovando e passa a mentir. Esta camada renderiza o config e confere,
  no XML, que cada VLAN da fixture virou um `<vlan<tag>>` dentro de
  `<interfaces>`, e que toda VLAN com `dhcp_enabled` virou uma seção em
  `<dhcpd>`.

Sai 0 só quando AS DUAS camadas passam.
"""
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.etree import ElementTree

try:
    import yaml
except ImportError as exc:  # noqa: BLE001 — dependência ausente é erro claro
    sys.exit(f"dependência ausente: {exc}. Instale PyYAML.")

RAIZ = Path(__file__).resolve().parents[3]
ANSIBLE = RAIZ / "IaC" / "ansible"
PB_OPNSENSE = ANSIBLE / "playbooks" / "network" / "01-opnsense-setup.yml"
PB_OPENWRT = ANSIBLE / "playbooks" / "network" / "02-openwrt-setup.yml"
TEMPLATE = ANSIBLE / "templates" / "network" / "opnsense" / "config.xml.j2"
COMMON = ANSIBLE / "inventories" / "_common" / "opnsense.yml"
FIXTURE = ANSIBLE / "tests" / "fixtures" / "opnsense.yml"
RENDER = ANSIBLE / "tests" / "render_opnsense.py"

# `name` exato de cada task de gate. Renomear a task derruba este teste — de
# propósito: é o ponto onde a Camada 1 se prende ao alvo real.
GATE_ORFA = "Abortar se uma regra de filtro citar interface que o config não cria"
GATE_PASSAGEM = "Abortar se uma VLAN não declarar política de passagem completa"
GATE_MGMT = "Abortar se a VLAN de gerência ainda não existe ou não é alcançável"
SONDA_MGMT = "Sondar o gateway da VLAN de gerência a partir do nó de controle"

falhas = []


def checar(condicao, mensagem):
    if condicao:
        print(f"  OK {mensagem}")
    else:
        print(f"  FALHOU {mensagem}")
        falhas.append(mensagem)


def _normalizar(texto):
    """Colapsa espaço e quebra de linha em espaço único.

    Os `that:` e `vars:` dos gates usam `>-` multilinha, então cada expressão
    vem quebrada com indentação. Normalizar deixa a busca por substring
    estável a reformatação, sem depender de casar a string exata.
    """
    return re.sub(r"\s+", " ", texto)


def _tasks(playbook_path):
    """Devolve todas as tasks de todos os plays, em ordem, com o índice global."""
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
                        saida.append((chave, task))
    return saida


def _achar(playbook_path, nome):
    for _chave, task in _tasks(playbook_path):
        if task.get("name") == nome:
            return task
    return None


def _corpo_assert(task):
    """Texto normalizado do `that:` de uma task de assert, ou None."""
    bloco = task.get("ansible.builtin.assert") or task.get("assert")
    if not (isinstance(bloco, dict) and "that" in bloco):
        return None
    that = bloco["that"]
    itens = that if isinstance(that, list) else [that]
    return _normalizar(" ".join(str(item) for item in itens))


# ---------------------------------------------------------------------------
# CAMADA 1 — os gates continuam nos playbooks, com a forma certa
# ---------------------------------------------------------------------------
def verifica_gates_opnsense():
    print("== CAMADA 1a: gates de consistência VLAN <-> regras (01-opnsense) ==")
    checar(PB_OPNSENSE.is_file(), f"playbook existe: {PB_OPNSENSE.name}")
    if not PB_OPNSENSE.is_file():
        return

    # --- gate 1: regra citando interface que o config não cria -------------
    task = _achar(PB_OPNSENSE, GATE_ORFA)
    checar(task is not None, f"gate encontrado pelo name: {GATE_ORFA!r}")
    if task is not None:
        corpo = _corpo_assert(task)
        checar(corpo is not None, "gate 1 é um assert com bloco `that:`")
        if corpo:
            # A invariante é a diferença de conjuntos: interface citada por
            # regra que não está entre as que o config cria.
            checar("difference" in corpo
                   and "_ifaces_das_regras" in corpo
                   and "_ifaces_do_config" in corpo
                   and "length == 0" in corpo,
                   "gate 1 exige diferença VAZIA entre interfaces citadas e criadas")
        v = _normalizar(str(task.get("vars", {})))
        # As duas listas precisam sair do DADO, não de literal escrito à mão:
        # `vlans` de um lado, `opnsense_rules` do outro.
        checar("vlans" in v and "opnsense_rules" in v,
               "gate 1 deriva as duas listas de `vlans` e `opnsense_rules`")
        # A convenção de nome é o que amarra tudo: `vlan<tag>`, não `optN`.
        checar("vlan" in v and "'optN'" not in v,
               "gate 1 usa a convenção de nome `vlan<tag>`")

    # --- gate 2: VLAN sem regra de passagem irrestrita ---------------------
    task = _achar(PB_OPNSENSE, GATE_PASSAGEM)
    checar(task is not None, f"gate encontrado pelo name: {GATE_PASSAGEM!r}")
    if task is not None:
        corpo = _corpo_assert(task)
        checar(corpo is not None, "gate 2 é um assert com bloco `that:`")
        if corpo:
            checar("_sem_politica" in corpo
                   and "_politica_incompleta" in corpo
                   and corpo.count("length == 0") >= 2,
                   "gate 2 exige as duas listas vazias (sem política e incompleta)")
        v = _normalizar(str(task.get("vars", {})))
        # A invariante do gate 2 mudou de alvo quando a passagem passou a ser
        # DERIVADA de `politica`: procurar a regra daria verde no dia em que a
        # política sumisse e as regras ficassem de uma execução anterior. O
        # gate agora exige o CAMPO, e as três chaves dele.
        for chave in ("internet", "alcanca", "gerencia"):
            checar(f"'politica.{chave}', 'defined'" in v,
                   f"gate 2 exige a chave `{chave}` na política")
        checar("rejectattr('politica', 'defined')" in v,
               "gate 2 pega também a VLAN sem `politica` nenhuma")


def verifica_gate_openwrt():
    print("\n== CAMADA 1b: gate do elo 5 (02-openwrt) ==")
    checar(PB_OPENWRT.is_file(), f"playbook existe: {PB_OPENWRT.name}")
    if not PB_OPENWRT.is_file():
        return

    sonda = _achar(PB_OPENWRT, SONDA_MGMT)
    checar(sonda is not None, f"sonda encontrada pelo name: {SONDA_MGMT!r}")
    if sonda is not None:
        cmd = _normalizar(str(sonda.get("ansible.builtin.command")
                               or sonda.get("command") or ""))
        checar("openwrt_mgmt_gateway" in cmd,
               "a sonda mira o gateway da VLAN de gerência")
        # ⚠️ Roda do NÓ DE CONTROLE: quem precisa alcançar a VLAN depois da
        # troca é o controlador, não o AP. Sondar do AP provaria a coisa
        # errada — ele ainda está na rede antiga quando o gate roda.
        checar(sonda.get("delegate_to") == "localhost",
               "a sonda roda no nó de controle (delegate_to: localhost)")
        checar(sonda.get("failed_when") is False,
               "a sonda não falha sozinha (quem decide é o assert seguinte)")
        checar("register" in sonda, "a sonda registra o resultado")

    task = _achar(PB_OPENWRT, GATE_MGMT)
    checar(task is not None, f"gate encontrado pelo name: {GATE_MGMT!r}")
    if task is not None:
        corpo = _corpo_assert(task)
        checar(corpo is not None, "gate do elo 5 é um assert com bloco `that:`")
        if corpo:
            checar("rc == 0" in corpo,
                   "gate do elo 5 exige resposta da sonda (rc == 0)")

    # A ORDEM importa: o gate tem de vir ANTES de qualquer escrita, senão ele
    # avisa depois de o estrago estar feito. `pre_tasks` roda inteiro antes de
    # `tasks`, então basta afirmar a seção.
    secoes = {t.get("name"): chave for chave, t in _tasks(PB_OPENWRT)}
    checar(secoes.get(SONDA_MGMT) == "pre_tasks",
           "a sonda está em pre_tasks (antes de qualquer escrita)")
    checar(secoes.get(GATE_MGMT) == "pre_tasks",
           "o gate está em pre_tasks (antes de qualquer escrita)")


# ---------------------------------------------------------------------------
# CAMADA 2 — o template cria o que o gate 1 pressupõe
# ---------------------------------------------------------------------------
def verifica_render_cria_as_vlans():
    print("\n== CAMADA 2: o template cria uma interface por VLAN ==")

    fixture = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    vlans = fixture.get("vlans") or []
    checar(len(vlans) >= 2, "a fixture declara ao menos 2 VLANs")
    com_dhcp = [v for v in vlans if v.get("dhcp_enabled")]
    sem_dhcp = [v for v in vlans if not v.get("dhcp_enabled")]
    # As duas metades do `if vlan.dhcp_enabled` de _dhcp.xml.j2. Sem uma
    # fixture de cada lado, um dos ramos nunca é exercido.
    checar(com_dhcp and sem_dhcp,
           "a fixture tem VLAN COM e SEM dhcp_enabled (os dois ramos do laço)")

    with tempfile.TemporaryDirectory() as tmp:
        saida = Path(tmp) / "render.xml"
        proc = subprocess.run(
            [sys.executable, str(RENDER),
             "--template", str(TEMPLATE),
             "--common", str(COMMON),
             "--fixture", str(FIXTURE),
             "--out", str(saida)],
            capture_output=True, text=True, check=False,
        )
        checar(proc.returncode == 0,
               f"render executou (rc={proc.returncode}) {proc.stderr.strip()[:200]}")
        if proc.returncode != 0:
            return
        raiz = ElementTree.parse(saida).getroot()

    interfaces = raiz.find("interfaces")
    checar(interfaces is not None, "<interfaces> presente no render")
    dhcpd = raiz.find("dhcpd")
    checar(dhcpd is not None, "<dhcpd> presente no render")
    if interfaces is None or dhcpd is None:
        return

    for vlan in vlans:
        nome = f"vlan{vlan['tag']}"
        elemento = interfaces.find(nome)
        checar(elemento is not None,
               f"<interfaces><{nome}> criada pelo template")
        if elemento is None:
            continue
        # O `if` da interface tem de ser o MESMO device que a seção <vlans>
        # cria; divergir aqui produz interface apontando para device que não
        # existe, e o OPNsense sobe sem ela, calado.
        dev = elemento.findtext("if")
        checar(dev is not None and dev.endswith(f".{vlan['tag']}"),
               f"<{nome}><if> aponta para o device .{vlan['tag']} (veio {dev})")
        checar(elemento.findtext("ipaddr") == vlan["ip"],
               f"<{nome}><ipaddr> == {vlan['ip']}")

        # DHCP: presente se e só se `dhcp_enabled`. As duas direções importam
        # — um servidor DHCP na VLAN de gerência entrega endereço a quem se
        # plugar nela.
        tem_dhcp = dhcpd.find(nome) is not None
        checar(tem_dhcp == bool(vlan.get("dhcp_enabled")),
               f"<dhcpd><{nome}> presente == dhcp_enabled "
               f"({vlan.get('dhcp_enabled', False)})")

    # O Unbound precisa escutar em toda VLAN: o cliente recebe por DHCP o
    # gateway da PRÓPRIA VLAN como resolvedor, e ninguém atenderia lá.
    ativo = raiz.findtext(".//unboundplus/general/active_interface") or ""
    escutando = [i.strip() for i in ativo.split(",") if i.strip()]
    faltando = [f"vlan{v['tag']}" for v in vlans
                if f"vlan{v['tag']}" not in escutando]
    checar(not faltando,
           f"Unbound escuta em toda VLAN (faltando: {faltando or 'nenhuma'})")
    checar("lan" in escutando, "Unbound continua escutando na lan (rede de resgate)")

    return raiz, vlans


def verifica_politica_gera_regras(raiz, vlans):
    """Prova que cada `politica` vira o conjunto de regras que ela promete.

    Esta camada existe porque o gate 2 do playbook, depois de reescrito, só
    exige que o CAMPO exista. Quem garante que o campo tem EFEITO é aqui — sem
    isto, apagar o gerador de `_filter.xml.j2` deixaria o gate verde e as seis
    VLANs mudas, que é exatamente o estado que a migração inteira existe para
    sair.

    Não reimplementa o template: lê a política declarada e confere, no XML
    RENDERIZADO, a consequência prometida por ela.
    """
    print("\n== CAMADA 2b: `politica` vira regra ==")

    regras = [r for r in raiz.iter("rule")
              if (r.findtext("description") or "").count(" > ")]
    por_iface = {}
    for r in regras:
        por_iface.setdefault(r.findtext("interface"), []).append(r)

    universo = ["lan"] + [f"vlan{v['tag']}" for v in vlans]

    for vlan in vlans:
        eu = f"vlan{vlan['tag']}"
        pol = vlan["politica"]
        minhas = por_iface.get(eu, [])
        checar(bool(minhas), f"[{eu}] gerou regra de política")
        if not minhas:
            continue

        def alvos(acao, porta=""):
            return {r.findtext("destination_net") for r in minhas
                    if r.findtext("action") == acao
                    and (r.findtext("destination_port") or "") == porta}

        permitidas = {"lan" if d == "lan" else f"vlan{d}" for d in pol["alcanca"]}
        esperado_bloqueio = set(universo) - {eu} - permitidas

        checar(alvos("pass") >= permitidas,
               f"[{eu}] passa para tudo que `alcanca` declara: {sorted(permitidas)}")
        # As duas direções: bloqueia o que não foi liberado, E não bloqueia o
        # que foi. A segunda metade é a que pega um `reject` invertido, que
        # produziria um conjunto plausível e errado.
        checar(alvos("block") >= esperado_bloqueio,
               f"[{eu}] bloqueia o resto do lab: {sorted(esperado_bloqueio)}")
        checar(not (alvos("block") & permitidas),
               f"[{eu}] NÃO bloqueia o que `alcanca` liberou")

        checar(("any" in alvos("pass")) == bool(pol["internet"]),
               f"[{eu}] saída para a internet == internet:{pol['internet']}")
        checar(("(self)" in alvos("block", "Management_Ports"))
               == (not pol["gerencia"]),
               f"[{eu}] bloqueio de portas de gerência == not gerencia:"
               f"{pol['gerencia']}")

        # ⚠️ A ORDEM é o desenho inteiro, e ela não aparece em nenhuma das
        # checagens acima: com o bloqueio do lab DEPOIS da saída para a
        # internet, o `quick` faz a saída casar primeiro e a VLAN alcança tudo.
        # O conjunto de regras seria idêntico; só a `sequence` denuncia.
        def seqs(acao, destino):
            return [int(r.findtext("sequence")) for r in minhas
                    if r.findtext("action") == acao
                    and r.findtext("destination_net") == destino]

        seq_pass_lab = [s for d in permitidas for s in seqs("pass", d)]
        seq_block_lab = [s for d in esperado_bloqueio for s in seqs("block", d)]
        seq_any = seqs("pass", "any")
        if seq_pass_lab and seq_block_lab:
            checar(max(seq_pass_lab) < min(seq_block_lab),
                   f"[{eu}] liberação do lab vem ANTES do bloqueio do lab")
        if seq_block_lab and seq_any:
            checar(max(seq_block_lab) < min(seq_any),
                   f"[{eu}] bloqueio do lab vem ANTES da saída para a internet")


def main():
    verifica_gates_opnsense()
    verifica_gate_openwrt()
    resultado = verifica_render_cria_as_vlans()
    if resultado:
        verifica_politica_gera_regras(*resultado)

    print()
    if falhas:
        print(f"  {len(falhas)} FALHA(S)")
        return 1
    print("  GATES DA ORDEM DE APLICAÇÃO OK "
          "(asserts nos playbooks; template cria as VLANs e aplica a política)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
