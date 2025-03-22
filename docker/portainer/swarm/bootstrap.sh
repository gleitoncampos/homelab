#!/bin/bash
source .env

docker stack rm portainer

docker stack deploy -c portainer.yml portainer
