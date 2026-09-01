#!/usr/bin/env python3
"""Protege o inventário contra a forma DIRETÓRIO ler dados de mentira.

⚠️ O Ansible ignora `.ini` POR PADRÃO ao varrer um diretório de inventário —
está na lista de fábrica, junto de `.bak` e `.retry`. Consequência nesta base,
medida em 01/09/2026: `-i inventories/prod` pulava o `hosts.ini` real e lia o
`hosts.ini.example`, cuja extensão não é ignorada.

O resultado NÃO foi um erro. Foi:

    play #1 (opnsense): ...
      pattern: ['opnsense']
      hosts (0):

e, pior que o zero, `node1` existindo com `ansible_host` = o literal
`<IP-DO-MANAGER>` do modelo. Playbook contra esse host falha com erro de
conexão, que se lê como problema de rede e não como inventário errado.

A correção vive em `ansible.cfg`: `inventory_ignore_extensions` sem `.ini` e
com `.example`. Ela é invisível — nada no dia a dia denuncia que voltou ao
default, porque a forma ARQUIVO continua funcionando. Daí este teste.

CAMADA 1 — a opção existe e tem a forma certa.
  `.example` presente, `.ini` ausente. E a lista SUBSTITUI o default do
  Ansible, não acrescenta: um item de fábrica esquecido aqui passa a parsear
  arquivo que era pulado, então os de fábrica também são conferidos.

CAMADA 2 — o comportamento, com inventário sintético.
  Monta um diretório com `hosts.ini` (host real) e `hosts.ini.example`
  (placeholder), roda `ansible-inventory` na forma DIRETÓRIO e afirma que o
  host real apareceu e que o placeholder NÃO venceu. É esta camada que pega a
  regressão — a camada 1 sozinha só descreve o remédio.

Sai 0 só quando as duas passam.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[3]
ANSIBLE = RAIZ / "IaC" / "ansible"
CFG = ANSIBLE / "ansible.cfg"

# Copiada do default de fábrica do Ansible, MENOS `.ini`. Ver o docstring.
DE_FABRICA = {".pyc", ".pyo", ".swp", ".bak", "~", ".rpm", ".md", ".txt",
              ".rst", ".orig", ".cfg", ".retry"}

falhas = []


def checar(condicao, mensagem):
    print(f"  {'OK' if condicao else 'FALHOU'} {mensagem}")
    if not condicao:
        falhas.append(mensagem)


def ler_opcao():
    for linha in CFG.read_text(encoding="utf-8").splitlines():
        limpa = linha.strip()
        if limpa.startswith("#") or "=" not in limpa:
            continue
        chave, _, valor = limpa.partition("=")
        if chave.strip() == "inventory_ignore_extensions":
            return {v.strip() for v in valor.split(",") if v.strip()}
    return None


def camada_1():
    print("== CAMADA 1: a opção existe e tem a forma certa ==")
    itens = ler_opcao()
    checar(itens is not None,
           "`inventory_ignore_extensions` está no ansible.cfg")
    if itens is None:
        return
    checar(".example" in itens,
           "`.example` está na lista (senão o modelo é lido como inventário)")
    checar(".ini" not in itens,
           "`.ini` NÃO está na lista (senão o inventário real é pulado)")
    faltando = DE_FABRICA - itens
    checar(not faltando,
           f"os itens de fábrica seguem na lista (faltando: {sorted(faltando)})")


def camada_2():
    print("\n== CAMADA 2: a forma DIRETÓRIO lê o inventário real ==")
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "prod"
        d.mkdir()
        # O real. Endereço de documentação (RFC 5737) — nunca o do lab.
        (d / "hosts.ini").write_text(
            "[alvo]\nhost-real ansible_host=192.0.2.10\n", encoding="utf-8")
        # O modelo, com placeholder — exatamente o formato que enganou.
        (d / "hosts.ini.example").write_text(
            "[alvo]\nhost-modelo ansible_host=<IP-DE-EXEMPLO>\n", encoding="utf-8")

        env = dict(os.environ, ANSIBLE_CONFIG=str(CFG))
        proc = subprocess.run(
            ["ansible-inventory", "-i", str(d), "--list"],
            capture_output=True, text=True, check=False, env=env, cwd=str(ANSIBLE))
        if proc.returncode != 0:
            checar(False, f"ansible-inventory rodou ({proc.stderr.strip()[:160]})")
            return
        try:
            dados = json.loads(proc.stdout)
        except json.JSONDecodeError:
            checar(False, "a saída é JSON (avisos de parse contaminaram o stdout)")
            return

        hosts = dados.get("_meta", {}).get("hostvars", {})
        checar("host-real" in hosts,
               f"o host do hosts.ini apareceu (achados: {sorted(hosts)})")
        checar("host-modelo" not in hosts,
               "o host do .example NÃO apareceu")
        endereco = hosts.get("host-real", {}).get("ansible_host")
        checar(endereco == "192.0.2.10",
               f"o endereço veio do arquivo real (veio {endereco!r})")


def main():
    checar(CFG.is_file(), f"ansible.cfg existe em {CFG}")
    if not CFG.is_file():
        return 1
    camada_1()
    camada_2()
    print()
    if falhas:
        print(f"  {len(falhas)} FALHA(S)")
        return 1
    print("  INVENTÁRIO OK (a forma diretório lê o arquivo real, "
          "e o modelo não é parseado)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
