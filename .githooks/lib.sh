#!/usr/bin/env bash
#
# lib.sh — leitura dos padrões de varredura, compartilhada pelos hooks.
#
# Existe porque `pre-commit` e `commit-msg` precisam da MESMA lista: conteúdo de
# arquivo e mensagem de commit são publicados juntos, e proteger só um dos dois
# é proteção nenhuma. Duplicar a lista garantiria que os dois divergissem.
#
# Não é executável — é sourced.

# Emite uma linha `rótulo%%regex%%isentos` por padrão, do arquivo genérico
# (versionado) e do local (gitignorado, com a topologia). Comentário e linha em
# branco saem aqui, para que quem consome não precise repetir o filtro.
pattern_lines() {
  local repo="$1"
  cat "$repo/.githooks/secret-patterns.txt" \
      "$repo/.githooks/secret-patterns.local.txt" 2>/dev/null \
    | grep -vE '^[[:space:]]*(#|$)'
}

# Avisa quando o arquivo local não existe. Aviso e não erro: em clone de
# terceiro ele legitimamente não existe, e o genérico ainda vale. Mas precisa
# ser visível — proteção que existe e não roda foi o modo de falha do
# `core.hooksPath`.
warn_missing_local() {
  local repo="$1"
  [[ -f "$repo/.githooks/secret-patterns.local.txt" ]] && return 0
  printf '  \033[33m!\033[0m %s\n' \
    "sem secret-patterns.local.txt — hostname e domínio NÃO estão sendo verificados"
  printf '      cp .githooks/secret-patterns.local.txt.example .githooks/secret-patterns.local.txt\n'
}

# Divide uma linha de padrão nos três campos. Separador `%%` e não `|`, porque
# os próprios regex usam `|` para alternância.
# Uso:  split_pattern "$line" && echo "$P_LABEL $P_REGEX $P_EXEMPT"
split_pattern() {
  local line="$1" rest
  P_LABEL="${line%%\%\%*}"; rest="${line#*%%}"
  P_REGEX="${rest%%\%\%*}"; P_EXEMPT="${rest#*%%}"
  [[ "$P_EXEMPT" == "$rest" ]] && P_EXEMPT=""
  [[ -n "$P_REGEX" ]]
}
