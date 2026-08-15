# 🏠 HomeLab & Self-Hosted Infrastructure

<p align="center">
  <img src="https://img.shields.io/badge/Debian-A81D33?style=for-the-badge&logo=debian&logoColor=white" alt="Debian" />
  <img src="https://img.shields.io/badge/Docker_Swarm-2496ED?style=for-the-badge&logo=docker&logoColor=white" alt="Docker Swarm" />
  <img src="https://img.shields.io/badge/Traefik-24A148?style=for-the-badge&logo=traefik&logoColor=white" alt="Traefik" />
  <img src="https://img.shields.io/badge/Portainer-13BEF9?style=for-the-badge&logo=portainer&logoColor=white" alt="Portainer" />
  <img src="https://img.shields.io/badge/Grafana-F46800?style=for-the-badge&logo=grafana&logoColor=white" alt="Grafana" />
  <img src="https://img.shields.io/badge/Renovate-17A2B8?style=for-the-badge&logo=renovatebot&logoColor=white" alt="Renovate" />
</p>

Repositório central de configurações, automações e arquivos de implantação (**Docker Swarm / Docker Compose**) da infraestrutura do meu HomeLab pessoal.

---

## 🎯 A Motivação: Emancipação Digital & Self-Hosting

Tudo começou quando, por questões de segurança após fazer uma compra no AliExpress, precisei gravar um vídeo detalhado do *unboxing*. Pouco tempo depois, recebi um alerta do **Google Photos** avisando que meu armazenamento de 15 GB havia atingido 80% da capacidade — acompanhado de uma oferta "imperdível" para assinar um plano pago de 100 GB.

Após alguns segundos de reflexão, ficou evidente que pagar uma assinatura mensal contínua não fazia sentido:
- Não costumo tirar fotos ou gravar vídeos com frequência.
- Os anos que levei para ocupar 12 GB seriam multiplicados até chegar aos 100 GB offered, mas eu pagaria a mensalidade durante todo esse período.

Esse gatilho reacendeu a paixão por infraestrutura e redes, abrindo as portas para um universo fascinante: o **Self-Hosting**. O objetivo principal deste repositório é documentar a transição para serviços auto-hospedados, garantindo **soberania de dados, privacidade e total controle da infraestrutura**.

---

## 🏗️ Arquitetura da Infraestrutura

A infraestrutura foi desenhada priorizando estabilidade, facilidade de manutenção e segurança:

- **Sistema Operacional Base**: [Debian 12/13](https://www.debian.org/) (Bookworm/Trixie) em instâncias dedicadas.
- **Orquestração de Containers**: **Docker Swarm** para gerenciamento de serviços em alta disponibilidade e facilidade de deploy.
- **Ingress & TLS**: [Traefik v3](https://traefik.io/) como Reverse Proxy centralizado, integrado à API da Cloudflare para emissão automática de certificados SSL/TLS wildcard via Let's Encrypt (DNS-01 Challenge).
- **Gerenciamento Visual**: [Portainer](https://www.portainer.io/) para controle visual das Stacks, Containers e redes Swarm.
- **Observabilidade & Métricas**: LGTM Stack ([Grafana](https://grafana.com/), [Loki](https://grafana.com/oss/loki/), [Prometheus](https://prometheus.io/), [Grafana Alloy](https://grafana.com/docs/alloy/latest/)) para centralização de logs e métricas.
- **Manutenção de Dependências**: [Renovate Bot](https://docs.renovatebot.com/) para auditoria e atualização automatizada de imagens Docker.

---

## 📂 Estrutura do Repositório

A árvore comentada, com o papel de cada diretório, vive em
[`CLAUDE.md`](./CLAUDE.md#-estrutura-do-repositório) — junto das regras que
governam onde cada coisa entra. Mantê-la em um lugar só evita a divergência
que já aconteceu entre os dois arquivos.

| diretório | o que guarda |
|---|---|
| `IaC/` | **toda** ferramenta de infraestrutura como código. Hoje `ansible/` (sistema, cluster e stacks) e `terraform/` (VMs e containers no Proxmox); amanhã Packer, Vagrant ou o que vier — sempre como subdiretório, nunca solto na raiz |
| `docker/` | as stacks que estão **no ar**. As demais ficam em `docker/_catalog/` |
| `prod-snapshot/` | ferramentas que coletam o estado real da produção para comparar com o que está descrito aqui |
| `docs/` | roadmap e pré-requisitos de publicação |

A regra do `IaC/` existe para que a raiz responda *o que este repositório é*, e
não *quais ferramentas ele usa* — com cada ferramenta solta no topo, a leitura
vira um inventário de tecnologias. Ver
[`CLAUDE.md`](./CLAUDE.md#regra-toda-ferramenta-de-iac-vive-sob-iac).

---

## 🌐 Catálogo de Serviços Self-Hosted

Os serviços estão organizados por domínio funcional dentro da pasta [`docker/`](./docker):

### 1. Ingress, Segurança & Gerenciamento
| Serviço | Descrição | Link da Configuração |
| :--- | :--- | :--- |
| **Traefik** | Reverse Proxy dinâmico com roteamento HTTPS e certificados wildcard Cloudflare | [`docker/traefik`](./docker/traefik) |
| **Portainer** | Gestão visual do Docker Swarm, Stacks e volumes + Rescue Scripts | [`docker/portainer`](./docker/portainer) |

### 2. Monitoramento, Observabilidade & Dashboards
| Serviço | Descrição | Link da Configuração |
| :--- | :--- | :--- |
| **Monitoring Stack** | Grafana + Loki + Prometheus + Alloy para métricas do host e logs de containers | [`docker/monitoring`](./docker/monitoring) |
| **Statping** | Monitoramento de disponibilidade (uptime) e página de status dos serviços | [`docker/statping`](./docker/_catalog/statping) |
| **Glance** | Dashboard minimalista para centralizar links e widgets do homelab | [`docker/glance`](./docker/_catalog/glance) |
| **Organizr** | Hub e portal de acesso unificado para múltiplos serviços self-hosted | [`docker/organizr`](./docker/_catalog/organizr) |

### 3. Armazenamento, Fotos & Documentos (Substitutos Google)
| Serviço | Descrição | Link da Configuração |
| :--- | :--- | :--- |
| **Nextcloud** | Substituição direta do Google Photos e Google Drive com sincronização mobile/desktop | [`docker/nextcloud`](./docker/_catalog/nextcloud) |
| **Paperless-ngx** | Indexação, busca textual por OCR e organização de documentos físicos escaneados | [`docker/paperless-ngx`](./docker/_catalog/paperless-ngx) |
| **Obsidian Sync** | Sincronização CouchDB/LiveSync para notas de base de conhecimento | [`docker/obsidian`](./docker/_catalog/obsidian) |
| **Stirling-PDF** | Suíte completa para divisão, fusão, OCR e conversão de arquivos PDF | [`docker/stirling-pdf`](./docker/_catalog/stirling-pdf) |
| **Linkwarden** | Gerenciador de marcadores e arquivador de páginas web | [`docker/linkwarden`](./docker/_catalog/linkwarden) |
| **ByteStash** | Repositório de trechos de código e anotações técnicas | [`docker/bytestash`](./docker/_catalog/bytestash) |
| **IT-Tools** | Coleção offline de utilitários para sysadmins e desenvolvedores | [`docker/it-tools`](./docker/_catalog/it-tools) |
| **Composerize** | Ferramenta para converter comandos `docker run` em `docker-compose.yml` | [`docker/composerize`](./docker/_catalog/composerize) |

### 4. Gestão de Tarefas, Finanças & Assinaturas
| Serviço | Descrição | Link da Configuração |
| :--- | :--- | :--- |
| **Vikunja** | Gestão de tarefas, listas e projetos (Kanban, GANTT e listas encadeadas) | [`docker/vikunja`](./docker/_catalog/vikunja) |
| **Actual Budget** | Sistema de orçamento pessoal baseado em orçamento base zero | [`docker/actual-budget`](./docker/_catalog/actual-budget) |
| **Firefly III** | Gerenciador financeiro avançado para contas, relatórios e transações | [`docker/firefly3`](./docker/_catalog/firefly3) |
| **Wallos** | Acompanhamento de custos com assinaturas de serviços recorrentes | [`docker/wallos`](./docker/_catalog/wallos) |
| **Thrifty** | Controle de despesas diárias ágil e leve | [`docker/thrifty`](./docker/_catalog/thrifty) |
| **PriceBuddy** | Rastreador de variação de preços e promoções | [`docker/pricebuddy`](./docker/_catalog/pricebuddy) |

### 5. Automação, Ambientes Isolados & Mídia
| Serviço | Descrição | Link da Configuração |
| :--- | :--- | :--- |
| **Cronicle** | Agendador centralizado de tarefas, scripts e rotinas periódicas | [`docker/cronicle`](./docker/_catalog/cronicle) |
| **JDownloader** | Gerenciador de downloads remotos com controle web | [`docker/jdownloader`](./docker/_catalog/jdownloader) |
| **KASM Workspaces** | Execução de navegadores e desktops em containers isolados via web | [`docker/kasm`](./docker/_catalog/kasm) |
| **ZoneMinder** | Sistema de videomonitoramento NVR e análise de câmeras de segurança | [`docker/zoneminder`](./docker/_catalog/zoneminder) |

---

## 🚀 Guia Rápidos de Implantação

### 1. Provisionamento do Host (Debian 12/13)
Do control node, com o inventário preenchido:

```bash
cd ansible
ansible-galaxy collection install -r requirements.yml
ansible-playbook -i inventories/prod/hosts.ini playbooks/01-bootstrap-os.yml
```

O playbook instala Docker CE, aplica o hardening de SSH e UFW e cria a árvore
em `/srv/containers`. Para um nó recém-criado, sem o usuário administrativo
ainda, rode antes `playbooks/00-node-bootstrap.yml` com `-e target=<host>`.

### 2. Inicialização do Cluster Swarm
Inicialize o Docker Swarm na máquina principal (Manager):

```bash
docker swarm init
```

Crie a rede overlay externa necessária para o Traefik e Portainer:

```bash
docker network create --driver=overlay traefik_public
```

### 3. Deploy do Ingress & Portainer
1. Suba a stack do **Traefik** para habilitar o roteamento de domínios.
2. Suba o **Portainer** para gerenciar as demais Stacks via interface gráfica. Consulte as instruções detalhadas em [`docker/README.md`](./docker/README.md).

---

## 🛡️ Boas Práticas & Segurança

- **Segredos e Variáveis de Ambiente**: Arquivos `.env` contendo senhas, chaves de API Cloudflare e tokens são mantidos fora do controle de versão via `.gitignore`.
- **Ansible Vault como fonte única, um por ambiente**: Os segredos consumidos pela automação vivem em dois arquivos criptografados — `IaC/ansible/inventories/prod/group_vars/all/vault.yml` e `IaC/ansible/inventories/dev/group_vars/all/vault.yml` (ambos gitignorados). O Ansible injeta os valores no campo `env` da API do Portainer, que resolve os `${VAR}` do `compose.yml` — assim nenhum segredo entra no repositório. Setup em [`IaC/ansible/README.md`](./IaC/ansible/README.md) → **Passo 0**. Variável de segredo **nunca** usa `| default('...')`: os playbooks abortam via `tasks/validate_vault.yml` se um segredo faltar, em vez de provisionar com valor público.
- **Split produção/desenvolvimento**: o inventário Ansible tem dois ambientes independentes, `IaC/ansible/inventories/prod/` e `IaC/ansible/inventories/dev/`, cada um com seu próprio inventário, variáveis e vault. O Portainer de **produção** acompanha a branch `main` deste repositório (`github_repo_branch: refs/heads/main`); o Portainer de **dev** acompanha a branch `develop` (`refs/heads/develop`). Isso significa que uma mudança em `compose.yml` só chega a cada ambiente depois do `push` para a branch correspondente — ver assimetria abaixo.
- **Assimetria push vs. Ansible**: mudar um `compose.yml` (ou qualquer arquivo dentro de `docker/`) é invisível para a stack já em execução até o commit ser enviado ao GitHub — é de lá que o Portainer clona via GitOps. Já uma mudança num arquivo de config *sidecar* (ex.: `docker/monitoring/swarm/loki-config.yml`, listado em `monitoring_sidecar_files` em `inventories/_common/all.yml`) chega ao host pelo **working tree local** via Ansible (`ansible.builtin.template`/`copy`) na próxima execução do playbook, e **não depende de push**. ⚠️ O sidecar `prometheus.yml.j2` referenciado em `monitoring_sidecar_files` **ainda não existe** — o `prometheus.yml` atual do repo é sintaxe Alloy River, não YAML do Prometheus; templatá-lo é trabalho pendente (ver [`docs/ROADMAP.md`](./docs/ROADMAP.md)). Confundir os dois caminhos é o erro mais comum ao editar uma stack já rodando.
- **Roteamento HTTPS**: Nenhum serviço é exposto em HTTP puro. O Traefik força o redirecionamento automático para HTTPS com TLS 1.2/1.3.
- **Persistência de Dados**: Os dados persistentes de cada aplicação devem ser armazenados sob a pasta padrão `/srv/containers/<nome-do-servico>`.

---

## 📌 Próximos Passos

O backlog completo, por fase, está em [`docs/ROADMAP.md`](./docs/ROADMAP.md).
Antes do primeiro push público, veja [`docs/PRE-DEPLOY.md`](./docs/PRE-DEPLOY.md).

---

<p align="center">
  <i>Desenvolvido com foco em autonomia digital, privacidade e aprendizado contínuo. 🚀</i>
</p>
