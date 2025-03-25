Arquivos dessa pasta estão formatados para serem usados em modo Docker Swarm. Para isso, vamos usar o Portainer como facilitador da manutenção

# COMO USAR: #

O Portainer deve ser o primeiro serviço a ser iniciado, pois ele irá criar a rede mestra do traefik, por onde ele vai se conectar a todos os serviços para fazer o roteamento do trafego.

Para isso, devemos copiar os arquivos "portainer.yaml" e ".example.env". Este ultimo deve ser renomeado para ".env" e dentro dele devem ser alteradas as variaveis conforme a necessidade.

Após editar o .env, dentro da pasta onde estão os 2 arquivos, execute o arquivo bootstrap.sh e ele irá subir os containers do Portainer.

Qualquer outro container/serviço deve ser executado por dentro do Portainer, passando o caminho do YAML do compose/swarm.

#
# 
Qualquer serviço que esteja nessa pasta pode ser considerado um serviço *fixo* no meu cluster. Serviços que foram apenas testados, serão mantidos apenas na pasta de *Compose*.

#
#
#
# TO-DO
- IaC do provisionamento do Portainer
- IaC do provisionamento dos stacks