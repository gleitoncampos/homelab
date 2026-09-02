#!/usr/bin/env python3
"""Protege a IDENTIDADE dos equipamentos de rede contra renomeação silenciosa.

`opnsense_hostname` e `openwrt_hostname` vinham de `| default('opnsense')` e
`| default('openwrt-ap')` nos templates, e não existiam em `group_vars` nenhum.
Um apply renomearia o firewall e o AP sem uma linha de aviso: hostname entra no
arquivo de configuração como qualquer outro campo.

⚠️ A parte que engana: tirar o `| default()` do template NÃO fecha o buraco.
`10-defaults.yml` é VERSIONADO e sempre entrega um valor, então o
StrictUndefined nunca dispara — sem `99-local.yml`, o render sai limpo com o
nome de documentação e o equipamento é renomeado assim mesmo.

Por isso a proteção tem três peças, e este teste cobre as três:

CAMADA 1 — os templates não têm valor de reserva.
  Reintroduzir `| default()` devolve o defeito original.

CAMADA 2 — o assert existe nos dois playbooks, com as três condições.
  É ele o gate EFETIVO. Este teste prova que está lá e mantém a forma — não
  reimplementa a lógica dele.

CAMADA 3 — o valor de `10-defaults.yml` continua sendo de documentação.
  O assert recusa nome terminado em `.example`. Trocar o versionado por algo
  plausível (`opnsense`, `router`) desarma o assert SEM tocar nele: as duas
  peças passariam verdes e o equipamento seria renomeado. Esta camada é o que
  liga uma coisa à outra.

Sai 0 só quando as três passam.
"""
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError as exc:
    sys.exit(f"dependência ausente: {exc}. Instale PyYAML.")

RAIZ = Path(__file__).resolve().parents[3]
ANSIBLE = RAIZ / "IaC" / "ansible"
DEFAULTS = ANSIBLE / "inventories" / "prod" / "group_vars" / "network" / "10-defaults.yml"

# (variável, template, playbook)
ALVOS = [
    ("opnsense_hostname",
     ANSIBLE / "templates" / "network" / "opnsense" / "_system.xml.j2",
     ANSIBLE / "playbooks" / "network" / "01-opnsense-setup.yml"),
    ("openwrt_hostname",
     ANSIBLE / "templates" / "network" / "openwrt_system.j2",
     ANSIBLE / "playbooks" / "network" / "02-openwrt-setup.yml"),
]

falhas = []


def checar(condicao, mensagem):
    print(f"  {'OK' if condicao else 'FALHOU'} {mensagem}")
    if not condicao:
        falhas.append(mensagem)


def tasks(caminho):
    saida = []
    for doc in yaml.safe_load_all(caminho.read_text(encoding="utf-8")):
        if not isinstance(doc, list):
            continue
        for play in doc:
            if not isinstance(play, dict):
                continue
            for chave in ("pre_tasks", "tasks", "post_tasks", "handlers"):
                for t in play.get(chave) or []:
                    if isinstance(t, dict):
                        saida.append(t)
    return saida


def camada_1():
    print("== CAMADA 1: os templates não têm valor de reserva ==")
    for var, tpl, _ in ALVOS:
        checar(tpl.is_file(), f"template existe: {tpl.name}")
        if not tpl.is_file():
            continue
        texto = tpl.read_text(encoding="utf-8")
        usa = re.search(r"\{\{\s*" + var + r"\s*(\|[^}]*)?\}\}", texto)
        checar(usa is not None, f"{tpl.name} usa {{{{ {var} }}}}")
        # `| default(` em QUALQUER lugar da mesma expressão devolve o defeito.
        com_default = re.search(
            r"\{\{\s*" + var + r"\s*\|\s*default\s*\(", texto)
        checar(com_default is None,
               f"{tpl.name} NÃO tem `| default()` em {var}")


def camada_2():
    print("\n== CAMADA 2: o assert está nos dois playbooks ==")
    for var, _, pb in ALVOS:
        alvo = None
        for t in tasks(pb):
            bloco = yaml.safe_dump(t, allow_unicode=True)
            if "ansible.builtin.assert" in t and var in bloco and "example" in bloco:
                alvo = t
                break
        checar(alvo is not None,
               f"{pb.name} tem o assert de identidade de {var}")
        if alvo is None:
            continue
        cond = " ".join(str(c) for c in alvo["ansible.builtin.assert"].get("that", []))
        checar(f"{var} is defined" in cond,
               f"  {pb.name}: confere que {var} está definido")
        checar("length > 0" in cond,
               f"  {pb.name}: confere que {var} não é vazio")
        checar(".endswith('.example')" in cond and "not (" in cond,
               f"  {pb.name}: recusa nome terminado em `.example`")
        # ⚠️ pre_task do AP sem `tags: always` é gate que uma tag desarma.
        if pb.name.startswith("02-"):
            checar(alvo.get("tags") == "always" or "always" in (alvo.get("tags") or []),
                   f"  {pb.name}: o assert tem `tags: always`")


def camada_3():
    print("\n== CAMADA 3: o versionado continua sendo valor de documentação ==")
    dados = yaml.safe_load(DEFAULTS.read_text(encoding="utf-8"))
    for var, _, _ in ALVOS:
        valor = dados.get(var)
        checar(valor is not None, f"{DEFAULTS.name} define {var}")
        if valor is None:
            continue
        checar(str(valor).endswith(".example"),
               f"{var} = {valor!r} termina em `.example` (senão o assert "
               f"passa a aprovar o valor versionado)")


def main():
    camada_1()
    camada_2()
    camada_3()
    print()
    if falhas:
        print(f"  {len(falhas)} FALHA(S)")
        return 1
    print("  IDENTIDADE OK (sem default no template; assert nos dois playbooks; "
          "valor versionado continua sendo de documentação)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
