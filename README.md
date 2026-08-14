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

```text
.
├── README.md               # Documentação principal do HomeLab
├── CLAUDE.md               # Diretrizes de desenvolvimento, premissas e boas práticas
├── TODO.md                 # Backlog e roadmap detalhado de tarefas e melhorias
├── bootstrap.sh            # Script de provisionamento inicial do nó Debian
├── renovate.json           # Configuração de atualização automática de dependências
├── .gitignore              # Proteção contra commit acidental de segredos/chaves
├── ansible/                # Infraestrutura como Código (IaC) para automação do host e Swarm
└── docker/                 # Diretório central de Stacks de Serviços
    ├── README.md           # Guia de implantação de Stacks via Swarm & Portainer
    ├── traefik/            # Configuração do Ingress Controller & SSL
    ├── portainer/          # Painel de gestão Swarm + scripts de recuperação
    ├── monitoring/         # Grafana, Prometheus, Loki, Alloy e log rotation
    ├── nextcloud/          # Armazenamento em nuvem & fotos (substituto Google Photos/Drive)
    ├── paperless-ngx/      # Gestão de documentos com OCR
    ├── vikunja/            # Gestão de tarefas e projetos
    ├── actual-budget/      # Controle financeiro pessoal
    ├── firefly3/           # Gestão de finanças avançada
    ├── wallos/             # Rastreador de assinaturas e mensalidades
    ├── thrifty/            # Gerenciador de despesas leve
    ├── pricebuddy/         # Monitoramento de histórico de preços
    ├── obsidian/           # Sincronização de notas Obsidian
    ├── stirling-pdf/       # Suíte de manipulação de PDFs
    ├── linkwarden/         # Arquivamento de links e marcadores
    ├── bytestash/          # Gerenciador de snippets de código
    ├── composerize/        # Utilitário `docker run` -> Compose
    ├── it-tools/           # Coleção de utilitários para dev/sysadmin
    ├── statping/           # Status page & uptime monitoring
    ├── glance/             # Dashboard unificado de Homelab
    ├── organizr/           # Hub unificado de serviços
    ├── cronicle/           # Agendador de tarefas distribuído
    ├── jdownloader/        # Gerenciador de downloads headless
    ├── kasm/               # Ambientes de trabalho e navegadores isolados
    └── zoneminder/         # Sistema NVR e monitoramento de segurança
```

---

## 🌐 Catálogo de Serviços Self-Hosted

Os serviços estão organizados por domínio funcional dentro da pasta [`docker/`](file:///home/nuvidio/git/homelab/docker):

### 1. Ingress, Segurança & Gerenciamento
| Serviço | Descrição | Link da Configuração |
| :--- | :--- | :--- |
| **Traefik** | Reverse Proxy dinâmico com roteamento HTTPS e certificados wildcard Cloudflare | [`docker/traefik`](file:///home/nuvidio/git/homelab/docker/traefik) |
| **Portainer** | Gestão visual do Docker Swarm, Stacks e volumes + Rescue Scripts | [`docker/portainer`](file:///home/nuvidio/git/homelab/docker/portainer) |

### 2. Monitoramento, Observabilidade & Dashboards
| Serviço | Descrição | Link da Configuração |
| :--- | :--- | :--- |
| **Monitoring Stack** | Grafana + Loki + Prometheus + Alloy para métricas do host e logs de containers | [`docker/monitoring`](file:///home/nuvidio/git/homelab/docker/monitoring) |
| **Statping** | Monitoramento de disponibilidade (uptime) e página de status dos serviços | [`docker/statping`](file:///home/nuvidio/git/homelab/docker/statping) |
| **Glance** | Dashboard minimalista para centralizar links e widgets do homelab | [`docker/glance`](file:///home/nuvidio/git/homelab/docker/glance) |
| **Organizr** | Hub e portal de acesso unificado para múltiplos serviços self-hosted | [`docker/organizr`](file:///home/nuvidio/git/homelab/docker/organizr) |

### 3. Armazenamento, Fotos & Documentos (Substitutos Google)
| Serviço | Descrição | Link da Configuração |
| :--- | :--- | :--- |
| **Nextcloud** | Substituição direta do Google Photos e Google Drive com sincronização mobile/desktop | [`docker/nextcloud`](file:///home/nuvidio/git/homelab/docker/nextcloud) |
| **Paperless-ngx** | Indexação, busca textual por OCR e organização de documentos físicos escaneados | [`docker/paperless-ngx`](file:///home/nuvidio/git/homelab/docker/paperless-ngx) |
| **Obsidian Sync** | Sincronização CouchDB/LiveSync para notas de base de conhecimento | [`docker/obsidian`](file:///home/nuvidio/git/homelab/docker/obsidian) |
| **Stirling-PDF** | Suíte completa para divisão, fusão, OCR e conversão de arquivos PDF | [`docker/stirling-pdf`](file:///home/nuvidio/git/homelab/docker/stirling-pdf) |
| **Linkwarden** | Gerenciador de marcadores e arquivador de páginas web | [`docker/linkwarden`](file:///home/nuvidio/git/homelab/docker/linkwarden) |
| **ByteStash** | Repositório de trechos de código e anotações técnicas | [`docker/bytestash`](file:///home/nuvidio/git/homelab/docker/bytestash) |
| **IT-Tools** | Coleção offline de utilitários para sysadmins e desenvolvedores | [`docker/it-tools`](file:///home/nuvidio/git/homelab/docker/it-tools) |
| **Composerize** | Ferramenta para converter comandos `docker run` em `docker-compose.yml` | [`docker/composerize`](file:///home/nuvidio/git/homelab/docker/composerize) |

### 4. Gestão de Tarefas, Finanças & Assinaturas
| Serviço | Descrição | Link da Configuração |
| :--- | :--- | :--- |
| **Vikunja** | Gestão de tarefas, listas e projetos (Kanban, GANTT e listas encadeadas) | [`docker/vikunja`](file:///home/nuvidio/git/homelab/docker/vikunja) |
| **Actual Budget** | Sistema de orçamento pessoal baseado em orçamento base zero | [`docker/actual-budget`](file:///home/nuvidio/git/homelab/docker/actual-budget) |
| **Firefly III** | Gerenciador financeiro avançado para contas, relatórios e transações | [`docker/firefly3`](file:///home/nuvidio/git/homelab/docker/firefly3) |
| **Wallos** | Acompanhamento de custos com assinaturas de serviços recorrentes | [`docker/wallos`](file:///home/nuvidio/git/homelab/docker/wallos) |
| **Thrifty** | Controle de despesas diárias ágil e leve | [`docker/thrifty`](file:///home/nuvidio/git/homelab/docker/thrifty) |
| **PriceBuddy** | Rastreador de variação de preços e promoções | [`docker/pricebuddy`](file:///home/nuvidio/git/homelab/docker/pricebuddy) |

### 5. Automação, Ambientes Isolados & Mídia
| Serviço | Descrição | Link da Configuração |
| :--- | :--- | :--- |
| **Cronicle** | Agendador centralizado de tarefas, scripts e rotinas periódicas | [`docker/cronicle`](file:///home/nuvidio/git/homelab/docker/cronicle) |
| **JDownloader** | Gerenciador de downloads remotos com controle web | [`docker/jdownloader`](file:///home/nuvidio/git/homelab/docker/jdownloader) |
| **KASM Workspaces** | Execução de navegadores e desktops em containers isolados via web | [`docker/kasm`](file:///home/nuvidio/git/homelab/docker/kasm) |
| **ZoneMinder** | Sistema de videomonitoramento NVR e análise de câmeras de segurança | [`docker/zoneminder`](file:///home/nuvidio/git/homelab/docker/zoneminder) |

---

## 🚀 Guia Rápidos de Implantação

### 1. Provisionamento do Host (Debian 12/13)
Em um nó Debian recém-instalado, execute o script de bootstrap ([`bootstrap.sh`](file:///home/nuvidio/git/homelab/bootstrap.sh)) para instalar Docker CE, Syncthing e utilitários de sistema:

```bash
chmod +x bootstrap.sh
./bootstrap.sh
```

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
2. Suba o **Portainer** para gerenciar as demais Stacks via interface gráfica. Consulte as instruções detalhadas em [`docker/README.md`](file:///home/nuvidio/git/homelab/docker/README.md).

---

## 🛡️ Boas Práticas & Segurança

- **Segredos e Variáveis de Ambiente**: Arquivos `.env` contendo senhas, chaves de API Cloudflare e tokens são mantidos fora do controle de versão via `.gitignore`.
- **Ansible Vault como fonte única, um por ambiente**: Os segredos consumidos pela automação vivem em dois arquivos criptografados — `ansible/inventories/prod/group_vars/all/vault.yml` e `ansible/inventories/dev/group_vars/all/vault.yml` (ambos gitignorados). O Ansible injeta os valores no campo `env` da API do Portainer, que resolve os `${VAR}` do `compose.yml` — assim nenhum segredo entra no repositório. Setup em [`ansible/README.md`](file:///home/nuvidio/git/homelab/ansible/README.md) → **Passo 0**. Variável de segredo **nunca** usa `| default('...')`: os playbooks abortam via `tasks/validate_vault.yml` se um segredo faltar, em vez de provisionar com valor público.
- **Split produção/desenvolvimento**: o inventário Ansible tem dois ambientes independentes, `ansible/inventories/prod/` e `ansible/inventories/dev/`, cada um com seu próprio inventário, variáveis e vault. O Portainer de **produção** acompanha a branch `main` deste repositório (`github_repo_branch: refs/heads/main`); o Portainer de **dev** acompanha a branch `develop` (`refs/heads/develop`). Isso significa que uma mudança em `compose.yml` só chega a cada ambiente depois do `push` para a branch correspondente — ver assimetria abaixo.
- **Assimetria push vs. Ansible**: mudar um `compose.yml` (ou qualquer arquivo dentro de `docker/`) é invisível para a stack já em execução até o commit ser enviado ao GitHub — é de lá que o Portainer clona via GitOps. Já uma mudança num arquivo de config *sidecar* (ex.: `docker/monitoring/swarm/loki-config.yml`, listado em `monitoring_sidecar_files` em `inventories/_common/all.yml`) chega ao host pelo **working tree local** via Ansible (`ansible.builtin.template`/`copy`) na próxima execução do playbook, e **não depende de push**. ⚠️ O sidecar `prometheus.yml.j2` referenciado em `monitoring_sidecar_files` **ainda não existe** — o `prometheus.yml` atual do repo é sintaxe Alloy River, não YAML do Prometheus; templatá-lo é trabalho pendente (ver `TODO.md`). Confundir os dois caminhos é o erro mais comum ao editar uma stack já rodando.
- **Roteamento HTTPS**: Nenhum serviço é exposto em HTTP puro. O Traefik força o redirecionamento automático para HTTPS com TLS 1.2/1.3.
- **Persistência de Dados**: Os dados persistentes de cada aplicação devem ser armazenados sob a pasta padrão `/srv/containers/<nome-do-servico>`.

---

## 📌 Próximos Passos (Roadmap)

- [ ] **IaC (Infrastructure as Code)**: Automatizar a configuração de nós e Docker Swarm com Ansible / Terraform.
- [ ] **Estratégia de Backup 3-2-1**: Implementar rotinas automatizadas de backup dos volumes em `/srv/containers/` utilizando Restic ou BorgBackup com envio offsite.
- [ ] **Single Sign-On (SSO)**: Integrar um provedor de identidade (Authelia / Authentik) no Traefik para proteção uniforme dos serviços.

---

<p align="center">
  <i>Desenvolvido com foco em autonomia digital, privacidade e aprendizado contínuo. 🚀</i>
</p>
