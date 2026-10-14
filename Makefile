# Entrada única para validar o repositório.
#
# Os comandos estavam espalhados por sete READMEs, o que garante que uma cópia
# fique para trás. A CI chama estes alvos em vez de repetir os comandos no YAML
# dela — duplicar seria recriar o mesmo problema num lugar novo.
#
# Alvos que precisam de rede ou de credencial (deploy, snapshot) estão
# separados dos que só leem o repositório, para que `make check` seja sempre
# seguro de rodar.

SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

ANSIBLE_DIR := IaC/ansible
TF_DIR      := IaC/terraform/proxmox
# Só as stacks no ar. O catálogo não passa em `docker stack config` porque
# várias definições ainda estão incompletas — é o que significa estar no
# catálogo.
COMPOSES    := $(shell git ls-files 'docker/*/swarm/compose.yml' | grep -v '_catalog')
SHELLS      := $(shell git ls-files "*.sh")
HOOK_SCRIPTS := .githooks/pre-commit .githooks/commit-msg .githooks/lib.sh
PLAYBOOKS   := $(wildcard $(ANSIBLE_DIR)/playbooks/*.yml $(ANSIBLE_DIR)/playbooks/network/*.yml)

# Um alvo por ferramenta, para que a saída diga qual falhou.
.PHONY: help hooks check lint lint-yaml lint-ansible lint-shell lint-tf ssh-keys \
        validate-compose validate-ansible validate-patterns check-common-copies \
        secrets links snapshot omv-fs discover clean render-opnsense render-monitoring \
        render-proxmox-ifaces render-openwrt-led render-openwrt-network render-opnsense-kea render-opnsense-wan6 render-opnsense-dnsbl render-opnsense-pf-estados render-opnsense-dns-bypass gates-node-bootstrap gates-swarm-ct gates-pve-exporter gates-monitoring \
        gates-ordem-vlan gateway-metrics inventario-diretorio gates-identidade \
        gates-tailscale gates-cpu-pinning validate-alloy render-traefik \
        gates-repositorios

help: ## Lista os alvos
	@echo "Alvos disponíveis:"
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "Comece por:  make hooks && make check"

hooks: ## Ativa o gate de pre-commit neste clone (obrigatório, uma vez)
	@git config core.hooksPath .githooks
	@echo "✓ core.hooksPath = .githooks"
	@if [ ! -f .githooks/secret-patterns.local.txt ]; then \
	  cp .githooks/secret-patterns.local.txt.example .githooks/secret-patterns.local.txt; \
	  echo "✓ secret-patterns.local.txt criado a partir do .example"; \
	  echo "  ⚠️  Edite-o com os hostnames e domínios da SUA topologia."; \
	else \
	  echo "✓ secret-patterns.local.txt já existe"; \
	fi

bootstrap: ## Gera vault, inventário, tfvars e chave SSH a partir de uma fonte única
	@python3 IaC/bootstrap/bootstrap.py $(ARGS)

ssh-keys: ## Instala a chave gerada pelo bootstrap em todo host do inventário (ENV=prod|dev)
	@bash IaC/bootstrap/distribuir_chave.sh $(or $(ENV),prod)

test-bootstrap: ## Testa os renderizadores do bootstrap contra os consumidores reais
	@python3 IaC/bootstrap/test_render.py

check: lint validate-compose validate-ansible validate-patterns check-common-copies links test-bootstrap render-opnsense render-monitoring render-proxmox-ifaces render-openwrt-led render-openwrt-network render-opnsense-kea render-opnsense-wan6 render-opnsense-dnsbl render-opnsense-pf-estados render-opnsense-dns-bypass gates-node-bootstrap gates-swarm-ct gates-pve-exporter gates-monitoring gates-ordem-vlan gateway-metrics inventario-diretorio gates-identidade gates-tailscale gates-cpu-pinning validate-alloy render-traefik gates-repositorios ## Tudo que a CI roda
	@echo
	@echo "✓ check completo"

lint: lint-yaml lint-ansible lint-shell lint-tf ## Só os linters

# Cada linha de receita roda num shell próprio: um `exit 0` na checagem de
# ausência NÃO pula a linha seguinte. Por isso cada alvo é um comando só.
# Ferramenta ausente avisa e passa; ferramenta presente que falha derruba o
# alvo. Assim o mesmo `make check` roda no laptop sem tudo instalado e na CI,
# onde tudo está.

lint-yaml: ## yamllint (config em .yamllint)
	@if command -v yamllint >/dev/null; then \
	  yamllint . && echo "✓ yamllint"; \
	else echo "– yamllint ausente, pulado"; fi

lint-ansible: ## ansible-lint, perfil production (config em .ansible-lint)
	@if command -v ansible-lint >/dev/null; then \
	  cd $(ANSIBLE_DIR) && ansible-lint --nocolor -q && echo "✓ ansible-lint"; \
	else echo "– ansible-lint ausente, pulado"; fi

lint-shell: ## shellcheck nos scripts e nos hooks
	@if command -v shellcheck >/dev/null; then \
	  shellcheck -S warning $(SHELLS) $(HOOK_SCRIPTS) && echo "✓ shellcheck"; \
	else \
	  for f in $(SHELLS) $(HOOK_SCRIPTS); do bash -n "$$f" || exit 1; done; \
	  echo "– shellcheck ausente; só 'bash -n' rodou"; \
	fi

lint-tf: ## terraform fmt
	@if command -v terraform >/dev/null; then \
	  terraform -chdir=$(TF_DIR) fmt -check -recursive && echo "✓ terraform fmt"; \
	else echo "– terraform ausente, pulado"; fi

validate-compose: ## docker stack config nas stacks no ar
	@if command -v docker >/dev/null; then \
	  for f in $(COMPOSES); do \
	    docker stack config -c "$$f" >/dev/null || { echo "✗ $$f"; exit 1; }; \
	  done; \
	  echo "✓ docker stack config ($(words $(COMPOSES)) stacks)"; \
	else echo "– docker ausente, pulado"; fi

validate-ansible: ## ansible-playbook --syntax-check
	@if command -v ansible-playbook >/dev/null; then \
	  cd $(ANSIBLE_DIR) && for p in $(patsubst $(ANSIBLE_DIR)/%,%,$(PLAYBOOKS)); do \
	    ansible-playbook --syntax-check "$$p" >/dev/null || { echo "✗ $$p"; exit 1; }; \
	  done; \
	  echo "✓ syntax-check ($(words $(PLAYBOOKS)) playbooks)"; \
	else echo "– ansible ausente, pulado"; fi

validate-patterns: ## Valida os arquivos de padrões (separadores, isenção não vira catch-all, regex casa amostra conhecida-ruim)
	@bash .githooks/validate-patterns.sh

# prod/ e dev/group_vars/all/00-common.yml são CÓPIAS de _common/all.yml, não
# symlinks (o git não preserva symlink de forma confiável entre clones — ver
# o cabeçalho do próprio all.yml). Cópia manual diverge sem avisar; já
# divergiu uma vez nesta base. Nada mais aqui compara as três, então falhar
# rápido é este alvo.
check-common-copies: ## Falha se alguma cópia divergir da sua fonte (00-common.yml e AGENTS.md)
	@fail=0; \
	src=$(ANSIBLE_DIR)/inventories/_common/all.yml; \
	for env in prod dev; do \
	  dst=$(ANSIBLE_DIR)/inventories/$$env/group_vars/all/00-common.yml; \
	  if ! diff -u "$$src" "$$dst"; then \
	    echo "✗ $$dst diverge de $$src (ver diff acima)"; fail=1; \
	  fi; \
	done; \
	if ! diff -u CLAUDE.md AGENTS.md; then \
	  echo "✗ AGENTS.md diverge de CLAUDE.md (ver diff acima)"; fail=1; \
	fi; \
	for f in AGENTS.md $(ANSIBLE_DIR)/inventories/prod/group_vars/all/00-common.yml \
	         $(ANSIBLE_DIR)/inventories/dev/group_vars/all/00-common.yml; do \
	  if [ -L "$$f" ]; then \
	    echo "✗ $$f voltou a ser symlink — o Portainer não clona repositório com link simbólico"; fail=1; \
	  fi; \
	done; \
	[ "$$fail" -eq 0 ] && echo "✓ cópias idênticas às fontes, e nenhuma delas é symlink" || exit 1

# O baseline (tests/baseline/opnsense-fase0.xml) fica congelado a partir da
# fase 0 do plano OPNsense Plano A — ele MESMO é regenerado uma vez, na tarefa
# que move homenet/privateaddress do template para variável (fase 0). Depois
# disso, HEAD~1 nunca mais é a referência: comparar contra o commit anterior
# transformaria toda regressão estrutural em "esperado". Esta é a exceção
# documentada, não um convite a repetir a regeneração depois.
render-monitoring: ## Renderiza os templates da stack de monitoramento com 2 hosts por grupo
	@python3 $(ANSIBLE_DIR)/tests/render_monitoring.py

render-traefik: ## Renderiza as rotas do Traefik e trava o vínculo entre a rota e a variável que renomeia o equipamento
	@python3 $(ANSIBLE_DIR)/tests/render_traefik.py

render-proxmox-ifaces: ## Renderiza proxmox_interfaces.j2 e valida o gate de sub-rede (gateway na /24 do address)
	@python3 $(ANSIBLE_DIR)/tests/render_proxmox_ifaces.py

render-openwrt-led: ## Renderiza o daemon led-health do AP e trava START e ritmos de piscada
	@python3 $(ANSIBLE_DIR)/tests/render_openwrt_led.py

render-openwrt-network: ## Renderiza a rede do AP e trava o que tranca o equipamento (interface do dropbear, trunk, ordem do failsafe)
	@python3 $(ANSIBLE_DIR)/tests/render_openwrt_network.py

render-opnsense-kea: ## Confere o DHCPv4 do Kea no render do OPNsense (interfaces, subnets, gateway, reservas)
	@python3 $(ANSIBLE_DIR)/tests/render_opnsense_kea.py

render-opnsense-wan6: ## Confere o modo IPv6 da WAN PPPoE (dhcp6 com prefixo delegado)
	@python3 $(ANSIBLE_DIR)/tests/render_opnsense_wan6.py

render-opnsense-dnsbl: ## Confere que a DNSBL do Unbound é gerada no apply e atualizada por cron
	@python3 $(ANSIBLE_DIR)/tests/render_opnsense_dnsbl.py

render-opnsense-pf-estados: ## Confere que o timeout adaptativo do pf não derruba conexão estabelecida
	@python3 $(ANSIBLE_DIR)/tests/render_opnsense_pf_estados.py

render-opnsense-dns-bypass: ## Confere que os managers do Swarm escapam do DNS forçado (DNS-01 do Traefik)
	@python3 $(ANSIBLE_DIR)/tests/render_opnsense_dns_bypass.py

render-opnsense: ## Renderiza o template do OPNsense e compara com o baseline congelado
	@set -euo pipefail; \
	command -v xmllint >/dev/null || { echo "✗ xmllint ausente (pacote libxml2-utils)"; exit 1; }; \
	python3 -c 'import jinja2, yaml' 2>/dev/null || { echo "✗ Jinja2/PyYAML ausentes"; exit 1; }; \
	tpl=$(ANSIBLE_DIR)/templates/network/opnsense/config.xml.j2; \
	[ -f "$$tpl" ] || { echo "✗ template ausente: $$tpl"; exit 1; }; \
	tmp="$$(mktemp -d)"; trap 'rm -rf "$$tmp"' EXIT; \
	python3 $(ANSIBLE_DIR)/tests/render_opnsense.py \
	  --template "$$tpl" \
	  --common $(ANSIBLE_DIR)/inventories/_common/opnsense.yml \
	  --fixture $(ANSIBLE_DIR)/tests/fixtures/opnsense.yml \
	  --out "$$tmp/raw.xml"; \
	[ -s "$$tmp/raw.xml" ] || { echo "✗ render vazio"; exit 1; }; \
	xmllint --c14n "$$tmp/raw.xml" | xmllint --format - > "$$tmp/novo.xml"; \
	[ -s "$$tmp/novo.xml" ] || { echo "✗ normalização produziu arquivo vazio"; exit 1; }; \
	base=$(ANSIBLE_DIR)/tests/baseline/opnsense-fase0.xml; \
	[ -s "$$base" ] || { echo "✗ baseline ausente ou vazio: $$base"; exit 1; }; \
	diff -u --label baseline --label novo "$$base" "$$tmp/novo.xml" > "$$tmp/obtido.diff" || true; \
	: 'A ordem de concatenacao decide a ordem dos hunks, e ela NAO pode vir do'; \
	: 'glob: a collation de locale ignora pontuacao, e sob en_US.UTF-8 um'; \
	: 'fase-1a2.diff ordena ANTES de fase-1a.diff — observado nesta base. O'; \
	: 'sintoma e a comparacao falhar sem que nenhum fragmento esteja errado.'; \
	: 'Nao adianta prefixar LC_ALL=C no cat: quem expande o glob e o SHELL,'; \
	: 'antes de o cat existir. Por isso a ordem sai de um sort byte a byte.'; \
	esperado=$$(for f in $$(ls $(ANSIBLE_DIR)/tests/expected/*.diff 2>/dev/null | LC_ALL=C sort); do cat "$$f"; done); \
	if [ -z "$$esperado" ]; then \
	  if [ -s "$$tmp/obtido.diff" ]; then \
	    echo "✗ diff inesperado (nenhum expected/*.diff registrado):"; \
	    head -40 "$$tmp/obtido.diff"; exit 1; \
	  fi; \
	else \
	  printf '%s\n' "$$esperado" | diff -u - "$$tmp/obtido.diff" || \
	    { echo "✗ diff difere do aprovado em expected/"; exit 1; }; \
	fi; \
	echo "✓ render-opnsense"

gates-node-bootstrap: ## Confere que o 00-node-bootstrap entra como root apesar do ansible_user do inventário
	@python3 $(ANSIBLE_DIR)/tests/gates_node_bootstrap.py

gates-swarm-ct: ## Confere CTs do Swarm privilegiados (IPVS) e keyctl só em CT não privilegiado
	@python3 $(ANSIBLE_DIR)/tests/gates_swarm_ct.py

gates-pve-exporter: ## Confere o usuário só leitura do exporter do Proxmox no playbook de tokens
	@python3 $(ANSIBLE_DIR)/tests/gates_pve_exporter.py

gates-monitoring: ## Confere a stack de monitoramento (sem InfluxDB/Statping, pve-exporter no manager, exportador do AP)
	@python3 $(ANSIBLE_DIR)/tests/gates_monitoring.py

gates-ordem-vlan: ## Confere os gates da ordem de aplicação da migração de VLANs
	@python3 $(ANSIBLE_DIR)/tests/gates_ordem_vlan.py

gates-identidade: ## Confere que o apply não pode renomear firewall e AP
	@python3 $(ANSIBLE_DIR)/tests/gates_identidade.py

gates-tailscale: ## Confere que o apply não pode apagar a VPN de acesso remoto
	@python3 $(ANSIBLE_DIR)/tests/gates_tailscale.py

gates-cpu-pinning: ## Afirma que o pinning da VM do firewall recusa VM com mais de 1 vCPU
	@python3 $(ANSIBLE_DIR)/tests/gates_cpu_pinning.py

# ⚠️ As tasks de repositório do Proxmox são uma UNIDADE: a primeira esvazia
# /etc/apt/sources.list, as três últimas é que declaram os repositórios em
# deb822. Como a seleção é por --tags, marcar só parte do bloco deixa o
# hipervisor SEM repositório Debian nenhum — em silêncio, porque o playbook
# reporta sucesso e o sintoma só aparece no próximo apt install.
#
# O gate também mantém `upgrade: dist` FORA da tag: trocar a declaração de um
# repositório e atualizar o sistema inteiro têm riscos diferentes e não podem
# voltar a viajar na mesma task.
gates-repositorios: ## Afirma que a tag repositorios cobre o bloco inteiro e não arrasta o dist-upgrade
	@python3 $(ANSIBLE_DIR)/tests/gates_repositorios.py

# ⚠️ A configuração do Alloy NÃO é YAML e nenhum linter deste repositório a
# enxergava. O arquivo versionado carregava um erro de sintaxe — uma vírgula
# faltando num mapa — que fazia o Alloy recusar o arquivo inteiro na subida.
# Passou despercebido porque a produção rodava uma cópia antiga no host, e o
# arquivo do repositório nunca chegou a ser aplicado.
#
# A versão da imagem sai do compose, não é fixada aqui: uma fonte, dois
# consumidores. Docker ausente vira aviso, não falha — mesma convenção dos
# linters, para que um clone de terceiro não trave.
validate-alloy: ## Valida config.alloy com o binário do Alloy, na versão do compose
	@if ! command -v docker >/dev/null 2>&1; then \
	  echo -e "\033[33m  ! docker ausente — validação do Alloy pulada\033[0m"; exit 0; fi; \
	img=$$(grep -oE 'grafana/alloy:[^"[:space:]]+' docker/monitoring/swarm/compose.yml | head -1); \
	if [ -z "$$img" ]; then echo "✗ não achei a imagem do Alloy no compose"; exit 1; fi; \
	docker run --rm -v "$(CURDIR)/docker/monitoring/swarm:/c:ro" --entrypoint /bin/alloy "$$img" \
	  validate /c/config.alloy && echo "✓ config.alloy válido ($$img)"

inventario-diretorio: ## Confere que `-i <dir>` lê o inventário real, e não o .example
	@python3 $(ANSIBLE_DIR)/tests/inventario_diretorio.py

gateway-metrics: ## Confere a coleta de métricas de gateway do OPNsense
	@python3 $(ANSIBLE_DIR)/tests/gateway_metrics.py

secrets: ## Varre a árvore inteira, não só o índice (o hook cobre o índice)
	@bash .githooks/scan-tree.sh

links: ## Confere links relativos de markdown e stack_compose_path
	@bash .githooks/scan-refs.sh

snapshot: ## Coleta o estado real da produção (precisa de SSH e credencial)
	@cd prod-snapshot && ./collect.sh

omv-fs: ## Propõe a lista de filesystems de dados do NAS para o group_vars local
	@cd prod-snapshot && ./collect-omv-fs.py $(ARGS)

discover: ## Descobre hosts na rede e sugere o hosts.ini
	@cd prod-snapshot && ./discover.sh

clean: ## Remove artefato local de execução
	@rm -rf .ansible/tmp .pytest_cache
	@echo "✓ limpo"
