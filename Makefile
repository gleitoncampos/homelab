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
.PHONY: help hooks check lint lint-yaml lint-ansible lint-shell lint-tf \
        validate-compose validate-ansible secrets links snapshot discover clean

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

test-bootstrap: ## Testa os renderizadores do bootstrap contra os consumidores reais
	@python3 IaC/bootstrap/test_render.py

check: lint validate-compose validate-ansible links test-bootstrap ## Tudo que a CI roda
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

secrets: ## Varre a árvore inteira, não só o índice (o hook cobre o índice)
	@bash .githooks/scan-tree.sh

links: ## Confere links relativos de markdown e stack_compose_path
	@bash .githooks/scan-refs.sh

snapshot: ## Coleta o estado real da produção (precisa de SSH e credencial)
	@cd prod-snapshot && ./collect.sh

discover: ## Descobre hosts na rede e sugere o hosts.ini
	@cd prod-snapshot && ./discover.sh

clean: ## Remove artefato local de execução
	@rm -rf .ansible/tmp .pytest_cache
	@echo "✓ limpo"
