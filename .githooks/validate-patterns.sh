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
#      Esta é a checagem DECISIVA do lado da ISENÇÃO: um `|` líder ou à
#      direita, um `.*` desgarrado, qualquer forma de alternativa vazia —
#      todas fazem o campo casar com QUALQUER caminho, e o canário pega isso
#      sem precisar saber qual engine de regex está rodando (a mesma linha
#      quebrada engana o `ugrep` de um jeito e o GNU grep de outro; o canário
#      não depende disso);
#   5. o campo de REGEX casa com uma amostra conhecida-ruim, cadastrada em
#      `secret-patterns.samples.txt` (para `secret-patterns.txt`) ou em
#      `secret-patterns.local.txt.samples.example` (para
#      `secret-patterns.local.txt.example`). Esta é a checagem DECISIVA do
#      lado do REGEX — a mesma família de defeito do item 4, só que o
#      "compila sob grep -E" do item 2 não pega: um regex trocado por algo
#      sintaticamente válido mas que não casa NADA (ex.: um grupo de
#      alternância que perdeu metade num merge malfeito) passava por todo o
#      resto deste script e por `make secrets` igual, com o padrão inteiro
#      desarmado em silêncio. Pattern sem amostra cadastrada É um erro nos
#      dois arquivos versionados: amostra existe para não faltar.
#
# Roda contra os dois arquivos VERSIONADOS — `secret-patterns.txt` e
# `secret-patterns.local.txt.example`. `secret-patterns.local.txt` (real,
# gitignorado) também é validado quando existe neste clone, mas sua ausência
# não é erro: falta legitimamente em clone novo e na CI. A checagem de amostra
# do item 5 é MAIS BRANDA para este arquivo: como seus padrões (hostname e
# domínio reais) são inerentemente específicos da topologia de quem clonou,
# a amostra correspondente vive em `secret-patterns.local.samples.txt`
# (gitignorado, ao lado) — sem ele, este script AVISA e pula o item 5 só
# para os padrões locais, nunca falha por causa disso.

set -uo pipefail
REPO="$(git rev-parse --show-toplevel)"
cd "$REPO" || exit 1

CANARIO="zz-canary-not-exempt"
fail=0

# Busca a amostra do rótulo em um arquivo `rótulo%%amostra`. Só o PRIMEIRO
# `%%` separa (a amostra pode conter `%` sozinho sem problema). Ecoa a
# amostra em stdout e retorna 0 se achou; retorna 1 (sem saída) se o rótulo
# não consta do arquivo, ou se o arquivo de amostras nem existe.
lookup_sample() {
  local samples_file="$1" label="$2" sline
  [[ -f "$samples_file" ]] || return 1
  while IFS= read -r sline; do
    [[ -z "$sline" || "$sline" == \#* ]] && continue
    if [[ "${sline%%\%\%*}" == "$label" ]]; then
      printf '%s' "${sline#*%%}"
      return 0
    fi
  done < "$samples_file"
  return 1
}

# check_file FILE SAMPLES_FILE STRICT
#   STRICT=1: amostra ausente (arquivo ou rótulo) É falha — usado nos dois
#             arquivos versionados, onde a amostra é fabricada e não custa
#             nada manter completa.
#   STRICT=0: amostra ausente vira AVISO, não falha — usado só para o
#             `secret-patterns.local.txt` real, cuja amostra (hostname/domínio
#             da topologia de quem clonou) é opcional e vive num arquivo
#             gitignorado à parte.
check_file() {
  local file="$1" samples_file="$2" strict="$3"
  local line n_sep rest p_label p_regex p_exempt errfile sample warned_missing_samples=0
  [[ -f "$file" ]] || return 0
  errfile="$(mktemp)"

  if [[ "$strict" -eq 1 && ! -f "$samples_file" ]]; then
    echo "✗ $file: arquivo de amostras ausente ($samples_file) — cada padrão precisa de uma"
    fail=1
  fi

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

    if sample="$(lookup_sample "$samples_file" "$p_label")"; then
      if ! grep -qE -- "$p_regex" <<<"$sample"; then
        echo "✗ $file: regex de '$p_label' NÃO casa com a amostra conhecida-ruim ('$sample' em $samples_file) — o regex pode ter sido trocado por algo que não casa nada, e o padrão ficaria desarmado em silêncio"
        fail=1
      fi
    elif [[ -f "$samples_file" ]]; then
      # Arquivo de amostras existe, mas este rótulo específico não está nele.
      if [[ "$strict" -eq 1 ]]; then
        echo "✗ $file: sem amostra para '$p_label' em $samples_file — padrão novo sem amostra é padrão não testado"
        fail=1
      else
        printf '  \033[33m!\033[0m %s: sem amostra para '"'"'%s'"'"' em %s — padrão local não verificado quanto a desarme silencioso\n' "$file" "$p_label" "$samples_file"
      fi
    elif [[ "$strict" -eq 0 && "$warned_missing_samples" -eq 0 ]]; then
      printf '  \033[33m!\033[0m %s: sem %s — padrões locais não verificados quanto a desarme silencioso\n' "$file" "$samples_file"
      warned_missing_samples=1
    fi
  done < <(grep -vE '^[[:space:]]*(#|$)' "$file")

  rm -f "$errfile"
}

check_file ".githooks/secret-patterns.txt" ".githooks/secret-patterns.samples.txt" 1
check_file ".githooks/secret-patterns.local.txt.example" ".githooks/secret-patterns.local.txt.samples.example" 1
check_file ".githooks/secret-patterns.local.txt" ".githooks/secret-patterns.local.samples.txt" 0

(( fail )) || echo "✓ arquivos de padrões: separadores, regex, isenção e amostra conhecida-ruim de cada padrão"
exit $fail
