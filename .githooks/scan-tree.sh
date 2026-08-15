#!/usr/bin/env bash
#
# scan-tree.sh — varre a ÁRVORE INTEIRA contra os padrões sensíveis.
#
# O `pre-commit` olha só as linhas adicionadas no índice, que é o certo para um
# hook: rápido, e não pune você por um problema que já estava lá. Este script é
# o complemento — roda sobre todo arquivo rastreado, e é o que a CI executa,
# porque num PR o que importa é o estado final, não o incremento.
#
# ⚠️ Na CI o `secret-patterns.local.txt` não existe (é gitignorado), então só os
# padrões genéricos rodam lá. Hostname e domínio ficam por conta do hook local.
# Para fechar isso, injete os padrões da topologia num GitHub Secret e escreva o
# arquivo antes de chamar este script.
#
# Saída 0 se limpo; 1 com a lista se não.

set -uo pipefail
REPO="$(git rev-parse --show-toplevel)"
cd "$REPO" || exit 1

# shellcheck source=.githooks/lib.sh
. "$REPO/.githooks/lib.sh"

warn_missing_local "$REPO"

mapfile -t FILES < <(git ls-files)
[[ ${#FILES[@]} -eq 0 ]] && exit 0

fail=0
while IFS= read -r line; do
  split_pattern "$line" || continue
  for f in "${FILES[@]}"; do
    [[ -f "$f" ]] || continue
    [[ -n "${P_EXEMPT// }" ]] && grep -qE "$P_EXEMPT" <<<"$f" && continue
    hits="$(grep -inE -e "$P_REGEX" -- "$f" 2>/dev/null || true)"
    if [[ -n "$hits" ]]; then
      echo "✗ $P_LABEL — em $f"
      printf '%s\n' "$hits" | head -2 | cut -c1-110 | sed 's/^/    /'
      fail=1
    fi
  done
done < <(pattern_lines "$REPO")

(( fail )) || echo "✓ nenhum padrão sensível na árvore rastreada"
exit $fail
