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

## TL;DR

Dois públicos, dois caminhos. **Contribuir com código:** `make hooks && make check`, e o resto está em
[`CONTRIBUTING.md`](./CONTRIBUTING.md) — nada abaixo é necessário. **Operar um homelab:** siga na ordem.

```bash
# 1. Gate de qualidade (uma vez por clone — o git não versiona .git/hooks/)
make hooks
$EDITOR .githooks/secret-patterns.local.txt    # hostnames e domínios da SUA topologia

# 2. Entradas do Terraform e do Ansible (fonte única; rodar de novo não repergunta)
make bootstrap ARGS=--dry-run                  # mostra o que faria
make bootstrap                                 # gera de verdade

# 3. Validar o repositório (o mesmo conjunto que a CI roda)
make check

# 4. Hardware virtual — PULE se as VMs e os CTs já existem
cd IaC/terraform/proxmox
terraform init
terraform plan
terraform apply

# 5. Sistema, cluster e stacks
cd IaC/ansible
ansible-galaxy collection install -r requirements.yml
ansible-playbook site.yml --ask-become-pass    # prod é o inventário default
```

O passo 2 gera, a partir de um arquivo de respostas gitignorado: os dois `hosts.ini`, os dois `vault.yml` cifrados,
o `terraform.tfvars`, o `secret-patterns.local.txt`, a chave SSH, a senha do vault em `IaC/ansible/.ansible_vault_pass`
e o `CREDENCIAIS.local.txt` com as senhas de uso humano. Ele **não provisiona nada**. Das 23 chaves de segredo que
ele trata, 12 são geradas sem pergunta alguma, 9 são pedidas com a URL do console impressa ao lado, e 2 ficam
pendentes — os hashes bcrypt do OPNsense, que só o próprio firewall calcula. Detalhe em
[`IaC/bootstrap/`](./IaC/bootstrap/README.md).

⚠️ **Faça backup de `IaC/ansible/.ansible_vault_pass`.** Sem essa senha os dois vaults são irrecuperáveis, e não existe segunda cópia em lugar nenhum.

⚠️ **Passo 4, se algum convidado já existe no Proxmox:** rode `terraform import` antes de qualquer `plan` ou
`apply`. Sem o import, o Terraform tenta **criar** um CT ou VM duplicado com o mesmo IP estático de um nó em
produção. O procedimento está em [`IaC/terraform/proxmox/`](./IaC/terraform/proxmox/README.md) e é leitura
obrigatória antes do primeiro apply.

⚠️ **Passo 5, contra um convidado recém-criado pelo Terraform:** ele ainda não tem o usuário de automação, só
acesso root por chave. Rode antes o playbook `00-node-bootstrap.yml`, que é a ponte de root para o usuário de
automação.

`site.yml` importa sete playbooks, nessa ordem: host Proxmox, host OpenMediaVault, sistema operacional, cluster
Swarm e as três de stacks (`03`, `04`, `05`). Ficam **fora** dele, e são disparados à mão: `00-node-bootstrap.yml`,
`06-backup.yml` e os de `playbooks/network/`.

Não rode `docker swarm init` nem `docker network create traefik_public` à mão: o `02-bootstrap-cluster.yml` faz
os dois, e configuração manual não parametrizada é perdida na próxima execução — ver
[IaC é a fonte da verdade](./CLAUDE.md).

---

## 📂 Estrutura do Repositório

A árvore comentada, com o papel de cada diretório e a regra de onde cada coisa entra, vive em [`CLAUDE.md`](./CLAUDE.md#-estrutura-do-repositório).

| diretório | o que guarda |
|---|---|
| `IaC/` | **toda** ferramenta de infraestrutura como código: `bootstrap/` (gera as entradas das outras duas), `ansible/` (sistema, cluster e stacks), `terraform/` (VMs e CTs no Proxmox). Ferramenta nova entra como subdiretório, nunca solta na raiz |
| `docker/` | as stacks de serviço; as ainda não promovidas ficam em `docker/_catalog/` |
| `prod-snapshot/` | scripts que coletam o estado real da produção para comparar com o descrito aqui |
| `docs/` | runbooks operacionais. ⚠️ Documento que enumera fraqueza ainda não corrigida — backlog de segurança, lista de pendências, plano de hardening — **não** entra aqui: vive fora do versionamento. Ver [`CLAUDE.md`](./CLAUDE.md#regra-documento-de-trabalho-vive-em-local) |

---

## 🌐 Serviços

São 26 stacks definidas: 4 em [`docker/`](./docker) e 22 em `docker/_catalog/`. Dessas, **14 são implantadas por
automação** — Portainer e Traefik pelo playbook `02`, e mais 12 pelos playbooks `03` a `05`, das quais 11 saem
direto de `_catalog/`. As 12 restantes não têm playbook: o deploy, quando existe, é manual, pelo formulário de
Stacks do Portainer. A tabela completa, com categoria, caminho e descrição, está em
[`docker/README.md`](./docker/README.md).

---

## 🛡️ Boas Práticas & Segurança

- **IaC é a fonte da verdade, e divergência é destruída**: ⚠️ configuração feita à mão (GUI do OPNsense, LuCI, interface do Proxmox, Portainer, `ssh` no host) que não esteja parametrizada aqui **é perdida** na próxima execução, por projeto — o playbook do OPNsense escreve `/conf/config.xml` inteiro em vez de fazer merge. Ajuste emergencial pela GUI vira variável ou template no mesmo dia. Estado de aplicação (banco, mídia, log, certificado emitido) não entra nessa regra: vive em `/srv/containers/` e em volume. Contrato completo em [`CLAUDE.md`](./CLAUDE.md#-iac-é-a-fonte-da-verdade--divergência-é-destruída-não-preservada).
- **Ansible Vault como fonte única, um por ambiente**: os segredos vivem em dois arquivos cifrados e gitignorados, `IaC/ansible/inventories/{prod,dev}/group_vars/all/vault.yml`. O Ansible os injeta no campo `env` da API do Portainer, que resolve os `${VAR}` do `compose.yml` — nenhum segredo entra no repositório. ⚠️ Variável de segredo **nunca** usa `| default('...')`: os playbooks abortam via `tasks/validate_vault.yml` se um segredo faltar, em vez de provisionar com valor público. Setup em [`IaC/ansible/README.md`](./IaC/ansible/README.md) → **Passo 0**.
- **Split produção/desenvolvimento**: dois ambientes independentes de inventário, variáveis e vault. O Portainer de produção acompanha a branch `main` (`refs/heads/main`); o de dev acompanha `development` (`refs/heads/development`).
- **Assimetria push vs. Ansible**: ⚠️ mudar um `compose.yml` é invisível para a stack em execução até o commit ser **enviado ao GitHub** — é de lá que o Portainer clona via GitOps. Já um arquivo de config *sidecar* (ex.: `docker/monitoring/swarm/loki-config.yml`, listado em `monitoring_sidecar_files`) chega ao host pelo **working tree local**, via Ansible, e não depende de push. Confundir os dois é o erro mais comum ao editar uma stack já rodando.
- ⚠️ **O sidecar `prometheus.yml.j2` referenciado em `monitoring_sidecar_files` ainda não existe**: o `prometheus.yml` do repositório é sintaxe Alloy River, não YAML do Prometheus, então o arquivo que o Prometheus lê hoje no host não está no git. Ver [`prod-snapshot/`](./prod-snapshot/README.md).
- **Roteamento e persistência**: nenhum serviço é exposto em HTTP puro — o Traefik redireciona para HTTPS com TLS 1.2/1.3 — e todo dado persistente fica sob `/srv/containers/<servico>`.

---

## ✅ Qualidade & Convenções

O gate roda em três alcances: o `pre-commit` varre as linhas *adicionadas*, `make check` varre a árvore inteira,
e a CI repete sem poder ser pulada. Ele cobre vazamento de segredo, links e `stack_compose_path` que resolvem,
sintaxe de compose, `ansible-lint`, `yamllint`, `shellcheck`, `terraform fmt` e o formato da mensagem de commit
([Conventional Commits](https://www.conventionalcommits.org/pt-br/)). As regras completas, com o motivo de cada
uma, estão no [contrato de contribuição](./CLAUDE.md#-contrato-de-contribuição-obrigatório) do `CLAUDE.md`, que
é a fonte única lida também pelos assistentes de IA (`AGENTS.md` é symlink para lá).

⚠️ Sem `make hooks` o gate existe no repositório e simplesmente não roda — é o modo de falha silencioso mais provável aqui.

⚠️ Nenhum dado real entra em arquivo versionado: exemplos usam as faixas da RFC 5737 e domínios `.example`, e a
tabela do que usar no lugar de cada dado está em [`CONTRIBUTING.md`](./CONTRIBUTING.md). Hostname e domínio da
sua topologia vão em `secret-patterns.local.txt`, que é gitignorado — publicar a lista de termos proibidos
entregaria o esquema de nomes. O backlog por fase e o checklist de pré-publicação também ficam fora do
versionamento: os dois enumeram o que ainda não está protegido, e num repositório aberto essa lista serve mais
a um atacante do que a um contribuidor.

Contribuições são bem-vindas — ver [`CONTRIBUTING.md`](./CONTRIBUTING.md). Falha de segurança vai por
[`SECURITY.md`](./SECURITY.md), **nunca** por issue pública. Licença [MIT](./LICENSE).

---

<p align="center"><i>Desenvolvido com foco em autonomia digital, privacidade e aprendizado contínuo. 🚀</i></p>
