#!/usr/bin/env bash
#
# validate-patterns.sh — valida a FORMA dos arquivos de padrões, não o
# conteúdo deles.
#
# `IaC/bootstrap/test_render.py` já testa que o bootstrap RENDERIZA um padrão
# válido a partir de hostname/domínio arbitrários — mas isso exercita um
# arquivo GERADO em memória, nunca abre `.githooks/secret-patterns.txt` nem
# `secret-patterns.local.txt.example`, e só conta os separadores `%%`, sem
# nunca compilar o terceiro campo (a isenção). Foi exatamente essa lacuna que
# deixou passar um `|` órfão no campo de isenção do padrão "IP privado": dois
# arquivos nunca abertos por teste nenhum, e a propriedade que importava
# (a isenção não vira "casa com tudo") nunca testada.
#
# Para cada linha de padrão, em cada arquivo:
#   1. exatamente dois separadores `%%` (três campos, nem mais nem menos);
#   2. o campo de regex compila sob `grep -E` (nunca erro de sintaxe);
#   3. o campo de isenção, se não vazio, também compila sob `grep -E`;
#   4. o campo de isenção NÃO casa com um caminho canário
#      (zz-canary-not-exempt) que nenhum padrão real tem motivo para isentar.
#      Esta é a checagem DECISIVA: um `|` líder ou à direita, um `.*`
#      desgarrado, qualquer forma de alternativa vazia — todas fazem o campo
#      casar com QUALQUER caminho, e o canário pega isso sem precisar saber
#      qual engine de regex está rodando (a mesma linha quebrada engana o
#      `ugrep` de um jeito e o GNU grep de outro; o canário não depende disso).
#
# Roda contra os dois arquivos VERSIONADOS — `secret-patterns.txt` e
# `secret-patterns.local.txt.example`. `secret-patterns.local.txt` (real,
# gitignorado) também é validado quando existe neste clone, mas sua ausência
# não é erro: falta legitimamente em clone novo e na CI.

set -uo pipefail
REPO="$(git rev-parse --show-toplevel)"
cd "$REPO" || exit 1

CANARIO="zz-canary-not-exempt"
fail=0

check_file() {
  local file="$1" line n_sep rest p_label p_regex p_exempt errfile
  [[ -f "$file" ]] || return 0
  errfile="$(mktemp)"

  while IFS= read -r line; do
    [[ -z "$line" || "$line" == \#* ]] && continue

    n_sep=$(grep -o '%%' <<<"$line" | wc -l)
    if [[ "$n_sep" -ne 2 ]]; then
      echo "✗ $file: linha com $n_sep separador(es) '%%' (esperado 2): $line"
      fail=1
      continue
    fi

    p_label="${line%%\%\%*}"; rest="${line#*%%}"
    p_regex="${rest%%\%\%*}"; p_exempt="${rest#*%%}"
    [[ "$p_exempt" == "$rest" ]] && p_exempt=""

    : > "$errfile"
    grep -E -- "$p_regex" /dev/null >/dev/null 2>"$errfile"
    if [[ $? -eq 2 ]]; then
      echo "✗ $file: regex de '$p_label' não compila sob grep -E — $(cat "$errfile")"
      fail=1
    fi

    if [[ -n "$p_exempt" ]]; then
      : > "$errfile"
      grep -E -- "$p_exempt" /dev/null >/dev/null 2>"$errfile"
      if [[ $? -eq 2 ]]; then
        echo "✗ $file: isenção de '$p_label' não compila sob grep -E — $(cat "$errfile")"
        fail=1
      elif grep -qE -- "$p_exempt" <<<"$CANARIO"; then
        echo "✗ $file: isenção de '$p_label' casa com o canário ($CANARIO) — provável alternativa vazia (ex.: '|' líder ou à direita), a isenção está casando com QUALQUER caminho"
        fail=1
      fi
    fi
  done < <(grep -vE '^[[:space:]]*(#|$)' "$file")

  rm -f "$errfile"
}

check_file ".githooks/secret-patterns.txt"
check_file ".githooks/secret-patterns.local.txt.example"
check_file ".githooks/secret-patterns.local.txt"

(( fail )) || echo "✓ arquivos de padrões: separadores, regex e isenção compilam, isenção não casa com o canário"
exit $fail
