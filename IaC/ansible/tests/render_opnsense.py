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
from pathlib import Path

try:
    import yaml
    from jinja2 import Environment, FileSystemLoader, StrictUndefined
except ImportError as exc:
    sys.exit(f"dependência ausente: {exc}. Instale PyYAML e Jinja2.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", required=True, type=Path)
    ap.add_argument("--fixture", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    # Apaga qualquer --out de uma execução anterior ANTES de qualquer validação:
    # sem isso, uma falha abaixo deixa o arquivo velho no lugar, e um diff que
    # não encadeie com `&&` compara render antigo e lê como verde.
    args.out.unlink(missing_ok=True)

    for p in (args.template, args.fixture):
        if not p.is_file():
            return err(f"arquivo não encontrado: {p}")

    variaveis = yaml.safe_load(args.fixture.read_text(encoding="utf-8"))
    if not isinstance(variaveis, dict):
        return err(f"fixture não é um mapa YAML: {args.fixture}")

    env = Environment(
        loader=FileSystemLoader(str(args.template.parent)),
        undefined=StrictUndefined,   # variável não definida ABORTA
        trim_blocks=True,            # casa o default do ansible.builtin.template (trim_blocks=yes)
        lstrip_blocks=False,         # idem: o módulo NÃO sobrescreve lstrip_blocks, default é no
        keep_trailing_newline=True,
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
