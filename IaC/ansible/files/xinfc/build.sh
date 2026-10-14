#!/usr/bin/env bash
# =============================================================================
# Compila o xinfc-wsc (NFC do AP Xiaomi AX3000T) do FONTE, no commit fixado,
# com o toolchain do SDK oficial do OpenWrt. Saída: xinfc-wsc ao lado deste
# script (gitignorado — binário não entra no repositório) e xinfc-wsc.sha256.
#
#   uso: build.sh <git_url> <git_ref> <sdk_image>
#
# Por que do fonte e não o .apk pronto do autor: o pacote pronto é binário de
# terceiro; o fonte tem ~900 linhas e foi revisado (14/10/2026). Estático para
# não depender de libc do equipamento entre releases.
# =============================================================================
set -euo pipefail

url="${1:?git_url}"
ref="${2:?git_ref}"
img="${3:?sdk_image}"
dest="$(cd "$(dirname "$0")" && pwd)"

[[ "$ref" =~ ^[0-9a-f]{40}$ ]] || { echo "✗ git_ref precisa ser um commit completo (40 hex), não '$ref'" >&2; exit 1; }
command -v docker >/dev/null || { echo "✗ docker ausente" >&2; exit 1; }

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

git -C "$tmp" init -q src
git -C "$tmp/src" fetch -q --depth 1 "$url" "$ref"
git -C "$tmp/src" checkout -q FETCH_HEAD
[ "$(git -C "$tmp/src" rev-parse HEAD)" = "$ref" ] || { echo "✗ commit baixado difere de $ref" >&2; exit 1; }

docker run --rm -v "$tmp/src:/src:ro" -v "$tmp:/out" "$img" bash -c '
  set -e
  cc=$(ls -d staging_dir/toolchain-*/bin | head -1)/aarch64-openwrt-linux-musl-gcc
  export STAGING_DIR="$PWD/staging_dir"
  "$cc" -std=gnu11 -Os -s -static -Wall -Wextra -Wpedantic -Werror \
    -DXINFC_VERSION="\"'"${ref:0:7}"'\"" -I/src/include /src/src/xinfc-wsc.c -o /out/xinfc-wsc
'

install -m 0755 "$tmp/xinfc-wsc" "$dest/xinfc-wsc"
(cd "$dest" && sha256sum xinfc-wsc > xinfc-wsc.sha256)
echo "✓ $dest/xinfc-wsc ($(cut -c1-16 "$dest/xinfc-wsc.sha256"), commit ${ref:0:7})"
