#!/usr/bin/env python3
"""Protege o gate de sub-rede do Proxmox em DUAS camadas, rodável na CI sem host.

O gate EFETIVO contra o hipervisor é o `assert` do playbook
`00-proxmox-setup.yml` (task "Abortar se o gateway e o endereço do nó caírem em
/24 diferentes"), avaliado no apply contra os facts do host. Este playbook NÃO
recarrega a rede — não há `ifreload -a` nem `systemctl restart networking` aqui;
a rota default só é montada quando a rede subir (próximo reboot ou restart
manual). É por isso que o assert do playbook é a última linha de defesa:
escrever um gateway fora da /24 do address em /etc/network/interfaces só quebra
o acesso remoto mais tarde, longe daqui.

Este teste NÃO é o gate. Ele faz duas coisas distintas, e nenhuma delas
substitui o assert do playbook:

CAMADA 1 — asserção ESTRUTURAL sobre o playbook (verifica_gate_no_playbook).
  Abre `00-proxmox-setup.yml`, faz parse YAML, localiza a task do assert pelo
  `name` e AFIRMA que o `that:` dela ainda contém as três condições do gate.
  Se alguém APAGAR ou DESCARACTERIZAR o assert no playbook, esta camada FALHA e
  o `make check` fica vermelho. Sem ela o teste validaria só uma cópia da
  lógica, e apagar o gate real deixaria o `make check` verde — o defeito que
  esta camada existe para fechar ("lint limpo != correto", CLAUDE.md).

CAMADA 2 — render do template (verifica_render_template).
  Renderiza `proxmox_interfaces.j2` com fixtures de documentação (RFC 5737) e
  confere que o template produz `address` e `gateway` a partir das variáveis.
  NÃO reimplementa a decisão do assert — quem garante que a regra existe é a
  Camada 1. Aqui só se prova que o template lê as variáveis certas e emite as
  stanzas esperadas, para cada fixture.

  fixture BOA        — prefix 24, address e gateway na mesma /24
  fixture DIVERG_24  — prefix 24, /24 divergentes
  fixture PREFIX_25  — octetos iguais, prefix 25

  As três provam o RENDER (o template não decide, só escreve o que recebe);
  a decisão de aprovar/reprovar é do assert do playbook, coberto pela Camada 1.

Sai 0 só quando AS DUAS camadas passam. Sai != 0 se qualquer checagem falhar —
inclusive se a Camada 1 não encontrar o assert ou suas condições no playbook.
"""
import re
import sys
from pathlib import Path

try:
    import yaml
    from jinja2 import Environment, FileSystemLoader, StrictUndefined
except ImportError as exc:  # noqa: BLE001 — dependência ausente é erro claro
    sys.exit(f"dependência ausente: {exc}. Instale PyYAML e Jinja2.")

RAIZ = Path(__file__).resolve().parents[3]
TEMPLATES = RAIZ / "IaC" / "ansible" / "templates" / "proxmox"
TEMPLATE = "proxmox_interfaces.j2"
PLAYBOOK = RAIZ / "IaC" / "ansible" / "playbooks" / "00-proxmox-setup.yml"

# `name` exato da task do gate no playbook. Se o playbook renomear a task, este
# teste FALHA — de propósito: é o ponto onde a Camada 1 se prende ao alvo real.
GATE_TASK_NAME = "Abortar se o gateway e o endereço do nó caírem em /24 diferentes"

# Faixas de documentação da RFC 5737 — nunca dado real.
# ansible_interfaces é lista fictícia: o gate é sobre address/gateway/prefixo,
# não sobre as bridge-ports. proxmox_wan_interface é fora da bridge e não entra
# no gate.
BASE = {
    "ansible_interfaces": ["lo", "eth0", "eth1", "vmbr0"],
    "proxmox_wan_interface": "eth2",
}

# BOA: prefix 24, address e gateway na MESMA /24 (RFC 5737 TEST-NET-1).
FIXTURE_BOA = {
    **BASE,
    "ansible_host": "192.0.2.10",
    "proxmox_gateway_ip": "192.0.2.1",
    "proxmox_lan_prefix": 24,
}

# DIVERG_24: prefix 24, /24 divergentes — address em TEST-NET-1, gateway em
# TEST-NET-2. O eixo dos octetos.
FIXTURE_DIVERG_24 = {
    **BASE,
    "ansible_host": "192.0.2.10",
    "proxmox_gateway_ip": "198.51.100.1",
    "proxmox_lan_prefix": 24,
}

# PREFIX_25: os TRÊS OCTETOS são iguais (192.0.2.x), mas o prefixo é 25 — o
# eixo do prefixo.
FIXTURE_PREFIX_25 = {
    **BASE,
    "ansible_host": "192.0.2.10",
    "proxmox_gateway_ip": "192.0.2.1",
    "proxmox_lan_prefix": 25,
}

falhas = []


def checar(condicao, mensagem):
    if condicao:
        print(f"  OK {mensagem}")
    else:
        print(f"  FALHOU {mensagem}")
        falhas.append(mensagem)


def render(contexto):
    # trim_blocks/StrictUndefined iguais ao default do Ansible para `.j2` e aos
    # renders existentes (render_monitoring.py, render_opnsense.py).
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES)),
        undefined=StrictUndefined,
        trim_blocks=True,
        keep_trailing_newline=True,
    )
    # `match` é test do Ansible (re.match), não do Jinja puro — mesmo caso do
    # `regex_escape` em render_monitoring.py. O template o usa em
    # `select('match', '^(en|eth)')`. Sem registrá-lo aqui o teste falharia por
    # motivo que não existe na execução real.
    env.tests["match"] = lambda value, pattern: re.match(pattern, str(value)) is not None
    return env.get_template(TEMPLATE).render(**contexto)


def extrair(texto):
    """Extrai `address` e `gateway` do render. Retorna (address, gateway)."""
    m_addr = re.search(r"^\s*address\s+(\S+)", texto, re.M)
    m_gw = re.search(r"^\s*gateway\s+(\S+)", texto, re.M)
    address = m_addr.group(1) if m_addr else None
    gateway = m_gw.group(1) if m_gw else None
    return address, gateway


# ---------------------------------------------------------------------------
# CAMADA 1 — asserção estrutural sobre o playbook
# ---------------------------------------------------------------------------
def _normalizar(texto):
    """Colapsa espaços e quebras de linha em espaço único.

    O `that:` do gate usa `>-` multilinha, então cada condição vem quebrada em
    várias linhas com indentação. Normalizar deixa a busca por substring estável
    a reformatação de espaço/quebra, sem depender de casar a string exata.
    """
    return re.sub(r"\s+", " ", texto)


def _achar_task_do_gate(playbook_path):
    """Localiza a task do gate no YAML do playbook, pelo `name`.

    Varre pre_tasks e tasks de todos os plays. Retorna o dict da task ou None.
    """
    docs = list(yaml.safe_load_all(playbook_path.read_text(encoding="utf-8")))
    for doc in docs:
        if not isinstance(doc, list):
            continue
        for play in doc:
            if not isinstance(play, dict):
                continue
            for chave in ("pre_tasks", "tasks", "post_tasks", "handlers"):
                for task in play.get(chave) or []:
                    if isinstance(task, dict) and task.get("name") == GATE_TASK_NAME:
                        return task
    return None


def verifica_gate_no_playbook():
    """Prova que o assert de sub-rede AINDA existe e tem a forma certa.

    Esta é a camada que fecha o buraco: se o assert for removido do playbook, a
    task não é achada e o teste FALHA; se o `that:` perder qualquer uma das três
    condições, o teste também FALHA. As substrings-chave abaixo representam,
    cada uma, uma invariante do gate — não são casamento de string exata, e sim
    a marca mínima de que a condição correspondente continua presente.
    """
    print("== CAMADA 1: gate presente e com a forma certa no playbook ==")

    checar(PLAYBOOK.is_file(), f"playbook existe: {PLAYBOOK.name}")
    if not PLAYBOOK.is_file():
        return

    task = _achar_task_do_gate(PLAYBOOK)
    checar(task is not None,
           f"task do gate encontrada pelo name: {GATE_TASK_NAME!r}")
    if task is None:
        # Sem a task, o gate foi removido/renomeado — as checagens seguintes não
        # têm o que inspecionar. A falha acima já derruba o rc.
        return

    assert_block = task.get("ansible.builtin.assert") or task.get("assert")
    checar(isinstance(assert_block, dict) and "that" in assert_block,
           "a task é um ansible.builtin.assert com bloco `that:`")
    if not (isinstance(assert_block, dict) and "that" in assert_block):
        return

    that = assert_block["that"]
    # `that:` é lista de condições; cada item pode ser multilinha (`>-`). Junta
    # tudo num texto normalizado e busca as marcas de cada invariante.
    that_itens = that if isinstance(that, list) else [that]
    corpo = _normalizar(" ".join(str(item) for item in that_itens))

    # Invariante 1 — prefixo precisa ser 24 (a checagem por 3 octetos só vale
    # nesse prefixo; outro prefixo desarmaria o gate, por isso é reprovado).
    checar("proxmox_lan_prefix" in corpo and "== 24" in corpo,
           "condição de prefixo presente (proxmox_lan_prefix ... == 24)")

    # Invariante 2 — proxmox_gateway_ip precisa ser IP puro, sem "/prefixo": um
    # "192.0.2.1/24" passaria pela comparação de octetos e viraria gateway
    # inválido no /etc/network/interfaces. O `'/' not in ...` barra isso antes.
    checar(_normalizar("'/' not in proxmox_gateway_ip") in corpo,
           "condição de barra ('/' not in proxmox_gateway_ip) presente")

    # Invariante 3 — comparação de 3 octetos entre ansible_host e
    # proxmox_gateway_ip: a marca é o `.split('/')[0].split('.')[:3]` aplicado
    # aos dois lados. É o núcleo do gate — os dois endereços na mesma /24.
    marca_octetos = _normalizar(".split('/')[0].split('.')[:3]")
    checar(corpo.count(marca_octetos) >= 2
           and "ansible_host" in corpo
           and "proxmox_gateway_ip" in corpo,
           "comparação de 3 octetos entre ansible_host e proxmox_gateway_ip "
           "presente (.split('/')[0].split('.')[:3] nos dois lados)")


# ---------------------------------------------------------------------------
# CAMADA 2 — render do template (o template não decide, só escreve)
# ---------------------------------------------------------------------------
def verifica_render_template():
    """Prova que proxmox_interfaces.j2 emite address/gateway a partir das vars.

    Sem reimplementar a decisão do gate (isso é da Camada 1). Aqui só se
    confirma, para cada fixture, que a vmbr0 sai como static e que o
    address/gateway renderizados batem com o que foi passado — o template lê as
    variáveis certas.
    """
    print("\n== CAMADA 2: render do proxmox_interfaces.j2 ==")

    for rotulo, fx in (
        ("BOA (prefix 24, mesma /24)", FIXTURE_BOA),
        ("DIVERG_24 (prefix 24, /24 divergentes)", FIXTURE_DIVERG_24),
        ("PREFIX_25 (octetos iguais, prefix 25)", FIXTURE_PREFIX_25),
    ):
        print(f"  -- fixture {rotulo} --")
        txt = render(fx)
        checar("iface vmbr0 inet static" in txt,
               f"[{rotulo}] vmbr0 renderizada como static")
        addr, gw = extrair(txt)
        # O template escreve `address {{ ansible_host }}/{{ proxmox_lan_prefix }}`
        # e `gateway {{ proxmox_gateway_ip }}`. Conferimos que o valor emitido
        # deriva da variável — o template não inventa nem altera o endereço.
        esperado_addr = f"{fx['ansible_host']}/{fx['proxmox_lan_prefix']}"
        checar(addr == esperado_addr,
               f"[{rotulo}] address renderizado == {esperado_addr} (veio {addr})")
        checar(gw == fx["proxmox_gateway_ip"],
               f"[{rotulo}] gateway renderizado == {fx['proxmox_gateway_ip']} "
               f"(veio {gw})")


def main():
    verifica_gate_no_playbook()
    verifica_render_template()

    print()
    if falhas:
        print(f"  {len(falhas)} FALHA(S)")
        return 1
    print("  GATE DE SUB-REDE DO PROXMOX OK "
          "(assert presente no playbook com as 3 condições; template renderiza)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
