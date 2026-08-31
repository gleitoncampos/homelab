#!/usr/bin/env python3
"""Renderiza um template Jinja do OPNsense com um fixture, para comparação.

Existe porque nada no repositório valida `.j2` hoje: o pre-commit não olha
Jinja, e `ansible-playbook --syntax-check` não parseia o corpo do template.
O único validador do opnsense_config.xml.j2 era aplicá-lo no firewall.

Sai != 0 em qualquer falha. NUNCA escreve arquivo vazio: um arquivo de zero
byte comparado com outro de zero byte produz diff vazio, que se lê como verde.
"""
import argparse
import sys
import uuid
from pathlib import Path

try:
    import yaml
    from jinja2 import Environment, FileSystemLoader, StrictUndefined
except ImportError as exc:
    sys.exit(f"dependência ausente: {exc}. Instale PyYAML e Jinja2.")

# Namespace fixo do ansible.builtin.to_uuid (UUID_NAMESPACE_ANSIBLE).
# Copiado literalmente: mudar este valor muda TODOS os UUIDs gerados.
NAMESPACE_ANSIBLE = "361e6d51-faec-444a-9079-341386da8e2e"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", required=True, type=Path)
    ap.add_argument("--fixture", required=True, type=Path)
    ap.add_argument("--common", type=Path, default=None)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    # Apaga qualquer --out de uma execução anterior ANTES de qualquer validação:
    # sem isso, uma falha abaixo deixa o arquivo velho no lugar, e um diff que
    # não encadeie com `&&` compara render antigo e lê como verde.
    args.out.unlink(missing_ok=True)

    for p in (args.template, args.fixture, args.common):
        if p is not None and not p.is_file():
            return err(f"arquivo não encontrado: {p}")

    # _common/opnsense.yml é a política (tunables, aliases, regras, plugins,
    # constantes RFC). O fixture traz só o que ela não tem: topologia e segredo,
    # com valores de documentação. Carregar os dois aqui é o que faz o
    # `make render-opnsense` validar o arquivo que o playbook realmente aplica —
    # antes, ele validava uma cópia, e a cópia podia divergir em silêncio.
    variaveis = {}
    for p in (args.common, args.fixture):
        if p is None:
            continue
        dados = yaml.safe_load(p.read_text(encoding="utf-8"))
        if not isinstance(dados, dict):
            return err(f"não é um mapa YAML: {p}")
        variaveis.update(dados)

    env = Environment(
        loader=FileSystemLoader(str(args.template.parent)),
        undefined=StrictUndefined,   # variável não definida ABORTA
        trim_blocks=True,            # casa o default do ansible.builtin.template (trim_blocks=yes)
        lstrip_blocks=False,         # idem: o módulo NÃO sobrescreve lstrip_blocks, default é no
        keep_trailing_newline=True,
    )
    # `to_uuid` é filtro do ansible.builtin, não do Jinja puro — mesmo caso do
    # `regex_escape` em render_monitoring.py e do test `match` em
    # render_proxmox_ifaces.py. _filter.xml.j2 o usa para dar UUID ESTÁVEL às
    # regras geradas a partir de `politica`: UUIDv5 sobre o namespace fixo do
    # Ansible, então a mesma entrada devolve o mesmo UUID em todo render. Sem
    # registrá-lo aqui o teste falharia por um motivo que não existe na
    # execução real; registrá-lo com outra semântica seria pior, porque o
    # render passaria e o firewall receberia UUID diferente do testado.
    env.filters["to_uuid"] = lambda valor: str(
        uuid.uuid5(uuid.UUID(NAMESPACE_ANSIBLE), str(valor))
    )

    try:
        saida = env.get_template(args.template.name).render(**variaveis)
    except Exception as exc:                        # noqa: BLE001 — queremos toda falha
        return err(f"render falhou: {type(exc).__name__}: {exc}")

    if not saida.strip():
        return err("render produziu saída vazia")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(saida, encoding="utf-8")
    print(f"✓ render: {args.out} ({len(saida)} bytes)")
    return 0


def err(msg: str) -> int:
    print(f"✗ {msg}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
