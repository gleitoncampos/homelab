#!/usr/bin/env python3
"""O 00-node-bootstrap entra como root mesmo com `ansible_user` no inventário.

`remote_user: root` no play PERDE para `ansible_user` de host: em prod o
hosts.ini declara `ansible_user=<system_user>`, a conta que este playbook
existe para criar — `Permission denied` como gleiton (13/10/2026). Só
`vars: ansible_user: root` no play vence a variável de host.
"""
import sys
from pathlib import Path

import yaml

PB = Path(__file__).resolve().parents[1] / "playbooks" / "00-node-bootstrap.yml"
play = yaml.safe_load(PB.read_text(encoding="utf-8"))[0]
ok = (play.get("vars") or {}).get("ansible_user") == "root"
print(f"  {'OK' if ok else 'FALHOU'} play declara vars.ansible_user = root")
if not ok:
    sys.exit("\n✗ gates-node-bootstrap")
print("\n✓ gates-node-bootstrap")
