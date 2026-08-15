#!/usr/bin/env bash
#
# scan-refs.sh — referências que já quebraram de verdade nesta base.
#
# Chamado pelo `pre-commit` e por `make links`. Vive aqui, e não dentro do hook,
# para que a CI rode exatamente a mesma checagem: uma cópia no workflow acabaria
# divergindo, que é o modo de falha que este repositório já teve com seis cópias
# das regras de estilo.
#
# Saída 0 se tudo resolve; 1 com a lista se não.

set -uo pipefail
cd "$(git rev-parse --show-toplevel)" || exit 1

fail=0

# `stack_compose_path` é relativo à raiz: o Portainer clona do Git e resolve a
# partir dela. Um caminho errado só aparece na hora do deploy.
while read -r p; do
  [[ -z "$p" ]] && continue
  [[ -f "$p" ]] || { echo "✗ stack_compose_path aponta para arquivo inexistente: $p"; fail=1; }
done < <(git grep -hoE 'stack_compose_path: "[^"]+"' -- 'IaC/ansible/playbooks/*.yml' 2>/dev/null \
         | sed -E 's/.*"(.*)"/\1/' | sort -u)

# Links relativos de markdown. `.local/` fica de fora: não é publicado e aponta
# para arquivo que só existe nesta máquina.
if command -v python3 >/dev/null; then
  broken="$(python3 - <<'PY'
import re, pathlib
out = []
for f in pathlib.Path('.').rglob('*.md'):
    s = str(f)
    if '/.local/' in s or '/.git/' in s or s.startswith('.local/'):
        continue
    try:
        txt = f.read_text(errors='ignore')
    except OSError:
        continue
    for m in re.finditer(r'\]\((\.{1,2}/[^)#]+)', txt):
        if not (f.parent / m.group(1)).resolve().exists():
            out.append(f"{f}: {m.group(1)}")
print('\n'.join(out))
PY
)"
  if [[ -n "$broken" ]]; then
    echo "✗ link relativo quebrado:"
    printf '%s\n' "$broken" | sed 's/^/    /'
    fail=1
  fi
fi

(( fail )) || echo "✓ referências resolvem"
exit $fail
