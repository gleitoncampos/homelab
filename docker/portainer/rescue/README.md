# Portainer Rescue


Esse shell script + script Go são usados em conjunto para editar automaticamente um db do Portainer, alterando o SwarmID antigo pelo novo da maquina onde esta sendo executado.


## Como usar

1. Tenha o Docker instalado;
2. Copie 
2a. Execute o script sem nenhum arqumento e ele vai buscar o arquivo 'portainer.db' na mesma pasta onde os scripts estão;
2b. Execute o script passando como argumento o caminho do 'portainer.db';
