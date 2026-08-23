# Monitoring Stack

Grafana, Prometheus, Loki, Alloy, cAdvisor, node_exporter, InfluxDB e os
exporters do Proxmox e do nobreak — métricas e logs de containers e host num
único compose; só o Grafana é roteado pelo Traefik (ver
[`docker/README.md`](../README.md)).

## Os arquivos de configuração e quem os entrega

O compose monta os arquivos abaixo de `/srv/containers/`. Eles **não** chegam
pelo Portainer: o Portainer só clona o `compose.yml` do Git. Quem os coloca no
host é `playbooks/03-stack-monitoring.yml`, a partir da lista
`monitoring_sidecar_files` em `inventories/_common/all.yml`.

| arquivo | destino no host | renderizado? |
|---|---|---|
| `prometheus.yml.j2` | `/srv/containers/prometheus/prometheus.yml` | sim |
| `config.alloy` | `/srv/containers/alloy/config.alloy` | não |
| `loki-config.yml` | `/srv/containers/loki/loki-config.yml` | não |
| `datasources.yml` | `/srv/containers/grafana/datasources.yml` | não |
| `dashboards.yml` | `/srv/containers/grafana/dashboards.yml` | não |
| `dashboards/nut.json` | `/srv/containers/grafana/dashboards/nut.json` | não |

⚠️ **`prometheus.yml.j2` é template, e é assim que ele não carrega dado real.**
Todo endereço vem de `hostvars[...].ansible_host` e todo rótulo `instance` vem
do `inventory_hostname`. Acrescentar um alvo é acrescentar um host ao
inventário, nunca um IP ao arquivo.

`config-rsyslog.alloy.j2` é a configuração do **outro** Alloy — o que roda no
CT central de rsyslog, fora do Swarm, lendo os arquivos que o rsyslog já
gravou. Ele não está em `monitoring_sidecar_files` porque esse CT ainda não
está no inventário; o template existe para que a configuração pare de viver só
dentro do CT.

## Dashboards

Todos os dashboards são **provisionados por arquivo**, a partir de
`swarm/dashboards/`. Nenhum depende de alguém importar pela interface: um
Grafana recriado do zero volta com os sete.

| arquivo | dashboard | origem |
|---|---|---|
| `docker.json` | Docker monitoring | [grafana.com/193](https://grafana.com/grafana/dashboards/193-docker-monitoring/) |
| `node-exporter-full.json` | Node Exporter Full | [grafana.com/1860](https://grafana.com/grafana/dashboards/1860-node-exporter-full/) |
| `node-exporter-server-metrics.json` | Node Exporter Server Metrics v2 | [grafana.com/11756](https://grafana.com/grafana/dashboards/11756) |
| `openwrt.json` | OpenWRT | [grafana.com/18153](https://grafana.com/grafana/dashboards/18153-asus-openwrt-router/) |
| `opnsense.json` | OPNSense | [grafana.com/19366](https://grafana.com/grafana/dashboards/19366-opnsense/) |
| `proxmox.json` | Proxmox via Prometheus | [grafana.com/10347](https://grafana.com/grafana/dashboards/10347-proxmox-via-prometheus/) |
| `nut.json` | Nobreak (NUT) | [nut_exporter](https://github.com/DRuggeri/nut_exporter), reescrito — ver abaixo |

### O que muda entre exportar da interface e versionar

⚠️ **Dashboard exportado não vai direto para cá.** Três coisas precisam mudar,
e nenhuma é cosmética:

1. **O uid do datasource é gerado por instância.** Cinco destes carregavam o
   uid do Prometheus desta máquina — 315 referências ao todo. Num Grafana
   recriado do zero o uid é outro e **todo painel fica sem fonte**. Cada um
   passou a usar uma variável de dashboard do tipo `datasource`, que resolve
   sozinha para o Prometheus padrão.
2. **O `id` numérico é a chave da linha no banco daquela instalação**, e
   provisionar com id de outra colide. O `uid` é o que identifica de verdade e
   é o que se preserva — assim o arquivo **adota** o dashboard que já existe,
   em vez de criar um duplicado ao lado.
3. **Endereço real embutido em consulta.** O painel `Server src NAT Traffic` do
   OpenWrt tinha um IP da LAN cravado em `src="..."`, escolhido à mão por quem
   importou. Virou a variável `$src`, alimentada por
   `label_values(node_nat_traffic, src)` — some o dado real e o painel melhora,
   porque deixa de mostrar um host só.

O gate cobre isto: `make secrets` varre `dashboards/*.json` como varre qualquer
arquivo rastreado.

### Editar pela interface

`allowUiUpdates: true`, então editar na tela funciona. Mas a próxima execução
do Ansible reescreve o arquivo no host e, no ciclo seguinte de 60 s, o provider
repõe a versão do repositório. **Para guardar um ajuste, exporte o JSON e
commite** — mesmo contrato de "o repositório define o alvo" que vale para o
resto.

## Nobreak

`nut_exporter` fala o protocolo NUT (TCP 3493) com o `upsd` do nó Proxmox que
tem o cabo USB, configurado por
[`playbooks/07-nut-ups.yml`](../../IaC/ansible/playbooks/07-nut-ups.yml). O
dashboard `Nobreak (NUT)` é provisionado por arquivo, na pasta `Homelab`.

Duas coisas a saber antes de olhar o dashboard:

- **O dashboard foi adaptado ao equipamento, não copiado.** O de upstream pede
  `battery.runtime`, `battery.voltage.high` e `battery.voltage.low`, e este
  driver não publica nenhuma das três. Em vez de deixar painel vazio — que
  ensina a ignorar painel — o espaço passou a mostrar o que o driver publica e
  o upstream não desenhava: temperatura e potência.

  ⚠️ **Não existe estimativa de autonomia em minutos.** Alerta é por percentual
  de carga e pela flag `LB`, nunca por "faltam X minutos".

- **Cinco grandezas, cada uma com dois painéis**: um medidor para o valor de
  agora e uma linha para o histórico, com o sufixo `— histórico` no título. Na
  ordem em que aparecem, que é a de prioridade operacional: potência,
  temperatura, carga da bateria, tensão da rede e tensão da bateria.

  Dois painéis com o mesmo título — um deles desenhando outra grandeza — foi o
  defeito da primeira versão, e é por isso que o sufixo existe.

- **`ups.load` não tem painel, de propósito.** O firmware o calcula como
  `output.voltage × output.current` sobre os 1200 VA nominais: é o painel de
  potência com outra unidade, proporcional ponto a ponto. Conferido ao vivo —
  222,02 VA sobre 1200 dá 18,5 %, exatamente o `ups.load` reportado. Dois
  painéis proporcionais entre si não informam duas coisas; só criam a chance
  de discordarem por limiar desalinhado. A variável continua sendo coletada.

- ⚠️ **A potência é convertida para watt pelo fator de potência, e o fator vem
  das próprias métricas** — `ups.realpower.nominal / ups.power.nominal`, que
  neste modelo dá 840/1200 = 0,7. Nunca cravado no painel: trocado o nobreak, o
  fator acompanha sozinho.

  O que isso significa na leitura: `V × I` é potência **aparente** (VA), e o
  driver não mede o fator instante a instante — ele publica só o de placa.
  Então o número em watt carrega a premissa de que a carga do rack tem o mesmo
  fator de potência do projeto do nobreak. Para acompanhar consumo é o número
  útil; para dimensionar a capacidade do aparelho, quem manda é o VA.

- **Os limiares foram recalibrados, e sem isso dois painéis mentiam.** O
  original mede uma bateria de **24 V** (verde só a partir de 22 V): os 13,3 V
  de flutuação normal de uma bateria de 12 V apareciam em **vermelho**. E a
  faixa da rede era a de 115 V, então os 126 V medidos aqui já pintavam de
  amarelo.

  Os da potência são os **mesmos 60 % e 80 %** que o firmware usa para
  `ups.load`, expressos nos 840 W nominais: 504 W e 672 W.

## Antes de escrever alerta, leia isto

Comportamento observado num teste real de queda de energia, com o nobreak a
~20 % de carga. Não são hipóteses: são armadilhas que já estão no caminho de
quem for criar a primeira regra de alerta.

**⚠️ Nunca alerte em `CHRG` nem em `DISCHRG`.** Com a rede normal e a bateria
cheia, `ups.status` oscila entre `OL`, `OL CHRG` e `OL DISCHRG` a cada 5 a 10
segundos — dezesseis trocas em dois minutos, no teste. É o carregador pulsando,
comportamento normal desta classe de aparelho. Alerta é em **`OB`** (na
bateria) e **`LB`** (bateria baixa), que são estáveis.

**⚠️ `battery.charge` não é medidor de carga; é estimativa do firmware, e ela
atrasa.** Durante a queda o valor marcou 95,7 % com a bateria já entregando
corrente; só depois de a rede voltar é que caiu para 71,8 %, refletindo o que
tinha sido gasto. Serve para tendência ao longo de horas, **não** para decidir
quanto tempo resta. Somado à ausência de `battery.runtime`, a conclusão é a
mesma de sempre aqui: **autonomia em minutos não existe neste equipamento.**
O gatilho de desligamento é a flag `LB`, que quem observa é o `upsmon` — não
um limiar de percentual num dashboard.

**Métrica e log são caminhos independentes, e o log é o que pega evento curto.**
No teste, o `upsmon` registrou a queda em `17:31:15` e o Prometheus só marcou
`OB=1` em `17:31:56` — 41 segundos depois. Não é atraso: é resolução. O
`POLLFREQ` do `upsmon` é de 5 s e o `scrape_interval` do job é de 30 s.

⚠️ **Consequência: uma queda mais curta que o intervalo de scrape pode não
existir na métrica.** A primeira do teste durou ~20 s e só entrou no gráfico
porque calhou de atravessar um scrape. É por isso que os `NOTIFYFLAG ... SYSLOG`
do `upsmon.conf` não são enfeite — para piscada de energia, o log é a única
testemunha, e ele chega ao Loki pelo mesmo pipeline do resto do host.

**`ups.load` sobe quando o nobreak passa para a bateria** (20,1 % contra 17–19 %
na rede): é a eficiência do inversor. Um alerta de sobrecarga precisa de folga
para não disparar junto com toda queda de energia.

- **A lista de variáveis coletadas é enumerada** em `NUT_EXPORTER_VARIABLES`,
  não deixada vazia. Vazia, o exporter passaria a exportar toda variável
  numérica, e a série mudaria sozinha quando o driver ganhasse campos novos.
  São 14, e o dashboard usa 5 — as outras 9 são histórico para painel futuro,
  não desperdício acidental.
  ⚠️ O valor é **uma linha só**, entre aspas. Num escalar dobrado (`>-`) o YAML
  troca a quebra de linha por espaço, e o exporter compara o nome da variável
  literalmente: `" ups.load"` não casa com `"ups.load"` e a métrica desaparece
  sem erro em lugar nenhum.

## 📚 Referências

- [Loki: instalação por pacote](https://grafana.com/docs/loki/latest/setup/install/local/#install-using-apt-or-rpm-package-manager)
- [Alloy: instalação em Linux](https://grafana.com/docs/alloy/latest/set-up/install/linux/) · [migração do Promtail](https://grafana.com/docs/alloy/latest/set-up/migrate/from-promtail/)
- [Centralizar logs de aplicação no Grafana](https://dev.to/onticdani/how-to-centralize-and-visualize-your-app-logs-in-grafana-483c) · [logs de containers com Loki](https://dev.to/onticdani/centralize-and-visualize-docker-logs-in-grafana-with-loki-27c9)
- [6 formas de melhorar dashboards de log](https://grafana.com/blog/2023/05/18/6-easy-ways-to-improve-your-log-dashboards-with-grafana-and-grafana-loki/)
- [Monitorar OpenWrt com Prometheus](https://www.cloudrocket.at/posts/monitor-openwrt-nodes-with-prometheus/) · [com Grafana Cloud](https://grafana.com/blog/2021/02/09/how-i-monitor-my-openwrt-router-with-grafana-cloud-and-prometheus/)
- [Rotação de log por tamanho no rsyslog](https://www.rsyslog.com/doc/tutorials/log_rotation_fix_size.html)
- [Setup de referência de homelab](https://giuliomagnifico.blog/post/2024-07-08-home-setup-v5/#log-monitoring-in-grafana)
- [NUT: manual do usuário](https://networkupstools.org/docs/user-manual.chunked/index.html) · [driver `ragtech`](https://networkupstools.org/docs/man/ragtech.html)
- [nut_exporter (DRuggeri)](https://github.com/DRuggeri/nut_exporter)
