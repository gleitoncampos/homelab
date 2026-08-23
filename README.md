# 🏠 HomeLab & Self-Hosted Infrastructure

<p align="center">
  <img src="https://img.shields.io/badge/Debian-A81D33?style=for-the-badge&logo=debian&logoColor=white" alt="Debian" /> <img src="https://img.shields.io/badge/Docker_Swarm-2496ED?style=for-the-badge&logo=docker&logoColor=white" alt="Docker Swarm" /> <img src="https://img.shields.io/badge/Traefik-24A148?style=for-the-badge&logo=traefik&logoColor=white" alt="Traefik" />
  <img src="https://img.shields.io/badge/Portainer-13BEF9?style=for-the-badge&logo=portainer&logoColor=white" alt="Portainer" /> <img src="https://img.shields.io/badge/Grafana-F46800?style=for-the-badge&logo=grafana&logoColor=white" alt="Grafana" /> <img src="https://img.shields.io/badge/Renovate-17A2B8?style=for-the-badge&logo=renovatebot&logoColor=white" alt="Renovate" />
</p>

Configurações, automações e arquivos de implantação da infraestrutura do meu HomeLab pessoal — serviços
auto-hospedados no lugar da nuvem proprietária, por soberania de dados e privacidade. Docker Swarm sobre
Debian 12/13, TLS wildcard via DNS-01 na Cloudflare, observabilidade em LGTM (Grafana, Loki, Prometheus,
Alloy). As premissas de arquitetura, com o motivo de cada uma, estão em [`CLAUDE.md`](./CLAUDE.md).

---

Como subir, a ordem das ferramentas e como aplicar o Ansible por alvo:
[`docs/instalacao.md`](./docs/instalacao.md).

---

## 📂 Estrutura do Repositório

A árvore comentada, com o papel de cada diretório e a regra de onde cada coisa entra, vive em [`CLAUDE.md`](./CLAUDE.md#-estrutura-do-repositório).

| diretório | o que guarda |
|---|---|
| `IaC/` | **toda** ferramenta de infraestrutura como código: `bootstrap/` (gera as entradas das outras duas), `ansible/` (sistema, cluster e stacks), `terraform/` (VMs e CTs no Proxmox). Ferramenta nova entra como subdiretório, nunca solta na raiz |
| `docker/` | as stacks de serviço. `docker/_catalog/` guarda a **definição** das demais — o que está lá não é sinônimo de não implantado: 11 dessas stacks sobem por playbook direto de `_catalog/`. Ver [`docker/_catalog/README.md`](./docker/_catalog/README.md) |
| `prod-snapshot/` | scripts que coletam o estado real da produção para comparar com o descrito aqui |
| `docs/` | [`instalacao.md`](./docs/instalacao.md) e runbooks operacionais. ⚠️ Documento que enumera fraqueza ainda não corrigida — backlog de segurança, lista de pendências, plano de hardening — **não** entra aqui: vive fora do versionamento. Ver [`CLAUDE.md`](./CLAUDE.md#regra-documento-de-trabalho-vive-em-local) |

---

## 🌐 Serviços

São 26 stacks definidas: 4 em [`docker/`](./docker) e 22 em `docker/_catalog/`. Dessas, **14 são implantadas por
automação** — Portainer e Traefik pelo playbook `02`, e mais 12 pelos playbooks `03` a `05`, das quais 11 saem
direto de `_catalog/`. As 12 restantes não têm playbook: o deploy, quando existe, é manual, pelo formulário de
Stacks do Portainer. A tabela completa, com categoria, caminho e descrição, está em
[`docker/README.md`](./docker/README.md).

---

## 🛡️ Boas Práticas & Segurança

- **IaC é a fonte da verdade, e divergência é destruída**: ⚠️ configuração feita à mão — GUI do OPNsense, LuCI,
  Proxmox, Portainer, `ssh` no host — que não esteja parametrizada aqui **é perdida** na próxima execução, por
  projeto. Estado de aplicação (banco, mídia, log, certificado) não entra na regra: vive em `/srv/containers/` e
  em volume. Contrato em [`CLAUDE.md`](./CLAUDE.md#-iac-é-a-fonte-da-verdade--divergência-é-destruída-não-preservada).
- **Ansible Vault, um por ambiente**: segredos em dois arquivos cifrados e gitignorados,
  `IaC/ansible/inventories/{prod,dev}/group_vars/all/vault.yml`, injetados no `env` da API do Portainer. ⚠️
  Variável de segredo **nunca** usa `| default('...')`: os playbooks abortam via `tasks/validate_vault.yml` em
  vez de provisionar com valor público.
- **Split produção/desenvolvimento**: o Portainer de produção acompanha `main`; o de dev, `development`.
- **Assimetria push vs. Ansible**: ⚠️ mudar um `compose.yml` é invisível para a stack até o commit ser
  **enviado ao GitHub** — é de lá que o Portainer clona. Já um arquivo *sidecar* (ex.: `loki-config.yml`) chega
  pelo working tree local, via Ansible. Confundir os dois é o erro mais comum ao editar uma stack rodando.
- **Os sidecars são entregues pelo Ansible, não pelo Portainer**: `03-stack-monitoring.yml` renderiza
  `prometheus.yml.j2` e copia os demais para `/srv/containers/`, a partir da lista `monitoring_sidecar_files`.
  Sem esse passo o Prometheus sobe contra um caminho que o Docker cria como diretório.
- **Roteamento e persistência**: nada em HTTP puro — o Traefik redireciona para HTTPS com TLS 1.2/1.3 — e todo
  dado persistente fica sob `/srv/containers/<servico>`.

---

## ✅ Qualidade & Convenções

O gate roda em três alcances: o `pre-commit` varre as linhas *adicionadas*; `make check` repete na árvore
inteira; a CI roda os dois e não é pulável. A lista completa está no
[`CLAUDE.md`](./CLAUDE.md#-gate-de-qualidade).

⚠️ **`make secrets` é alvo separado e não é dependência de `check`** — é ele que varre a árvore rastreada
contra vazamento de segredo. Rode os dois antes do PR.

⚠️ Sem `make hooks` o gate existe no repositório e não roda: o modo de falha mais silencioso daqui.

⚠️ Nenhum dado real em arquivo versionado: exemplos usam RFC 5737 e domínios `.example`. Hostname e domínio da
sua topologia vão em `secret-patterns.local.txt`, gitignorado — publicar a lista de termos proibidos entregaria
o esquema de nomes. Backlog e checklist de pré-publicação também ficam fora do versionamento: enumeram o que
ainda não está protegido, e num repositório aberto isso serve mais a um atacante que a um contribuidor.

Regras completas no [contrato de contribuição](./CLAUDE.md#-contrato-de-contribuição-obrigatório) — fonte única,
lida também pelos assistentes de IA (`AGENTS.md` é symlink). Commits seguem
[Conventional Commits](https://www.conventionalcommits.org/pt-br/).

Contribuições: [`CONTRIBUTING.md`](./CONTRIBUTING.md). Falha de segurança por [`SECURITY.md`](./SECURITY.md),
**nunca** por issue pública. Licença [MIT](./LICENSE).

---

<p align="center"><i>Desenvolvido com foco em autonomia digital, privacidade e aprendizado contínuo. 🚀</i></p>
