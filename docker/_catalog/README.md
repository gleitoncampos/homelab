# Catálogo — stacks definidas; nem toda fora do ar

Aqui vive a **definição** de cada stack. `docker/` vs. `docker/_catalog/` não é
"implantado" vs. "não implantado": é "convenção de Swarm ok para promover" vs.
"ainda precisa adequação" — ver [`docker/README.md`](../README.md). Os
playbooks `02-` a `05-` do Ansible já implantam Traefik, Portainer e Monitoring
direto de `docker/`, e 11 das stacks abaixo direto deste diretório, sem passar
por `docker/`. As demais são só definição — sem playbook, sem deploy —
mantidas aqui para que a árvore não misture o que roda com o que ainda não
roda.

Um levantamento da produção mostrou 4 stacks em execução contra 26 diretórios no
repositório (4 em `docker/` e 22 aqui) — duas medições de naturezas diferentes: o
que estava no ar naquele dia contra o que o repositório define. Misturar as duas
coisas fazia o repositório parecer um retrato da
infraestrutura quando era, em boa parte, uma lista de desejos — e foi o que levou
os playbooks a apontarem para stacks inexistentes sem que ninguém percebesse.

## Promover uma stack para produção

1. Confira a stack contra o [`CLAUDE.md`](../../CLAUDE.md): labels do Traefik em
   `deploy.labels`, volumes sob `/srv/containers/`, tag de imagem fixa,
   `deploy.resources.limits` e `deploy.placement.constraints`.
2. `git mv docker/_catalog/<servico> docker/<servico>`
3. Ajuste o `stack_compose_path` no playbook que a implanta — o caminho é
   relativo ao repositório, porque o Portainer clona do Git.
4. Acrescente as variáveis de ambiente que o compose exige ao `stack_env` do
   playbook, e as chaves de vault correspondentes ao
   [`vault.yml.example`](../../IaC/ansible/inventories/vault.yml.example).

## Cinco não seguem o padrão

`jdownloader`, `kasm`, `nextcloud` e `zoneminder` têm o `compose.yml` na raiz do
diretório; `obsidian` usa `docker-compose.yml`. Todas trazem `version:`,
`container_name`, volumes relativos ou `privileged` — incompatíveis com Swarm ou
proibidos pelo `CLAUDE.md`. Precisam ser portadas antes de qualquer promoção,
não só movidas.
