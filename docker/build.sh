#!/bin/sh

rm -rf stock
rsync -av --progress ../../stock . --exclude .git --exclude .idea --exclude __pycache__ --exclude .DS_Store --exclude instock/cache --exclude instock/log --exclude instock/test

DOCKER_NAME=mayanghua/instock
TAG1=$(date "+%Y%m")
TAG2=latest

echo " docker build -f Dockerfile -t ${DOCKER_NAME} ."
docker build -f Dockerfile -t ${DOCKER_NAME}:${TAG1} -t ${DOCKER_NAME}:${TAG2} .
echo "#################################################################"
echo " docker push ${DOCKER_NAME} "

docker push ${DOCKER_NAME}:${TAG1}
docker push ${DOCKER_NAME}:${TAG2}
