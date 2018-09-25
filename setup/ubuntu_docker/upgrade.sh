#!/usr/bin/env bash

# This script upgrades dockerized Redash on Ubuntu 16.04.

set -eu

REDASH_BASE_PATH=/opt/redash
REDASH_BRANCH="${REDASH_BRANCH:-master}" # Default branch/version to master if not specified in REDASH_BRANCH env var
FILES_BASE_URL=https://raw.githubusercontent.com/getredash/redash/${REDASH_BRANCH}/setup/ubuntu_docker

verify_root() {
    # Verify running as root:
    if [[ "$(id -u)" != "0" ]]; then
        if [[ $# -ne 0 ]]; then
            echo "Failed running with sudo. Exiting." 1>&2
            exit 1
        fi
        echo "This script must be run as root. Trying to run with sudo."
        sudo bash "$0" --with-sudo
        exit 1
    fi
}

verify_ubuntu() {
	if [[ "$(cat /etc/lsb-release | grep "DISTRIB_ID" | awk 'BEGIN{FS="="}{print $2}')" != "Ubuntu" ]]; then
		echo "This operating system is not Ubuntu. Exiting."
		exit 1
	fi
}

verify_root
verify_ubuntu

CURRENT_IMAGE_VERSION=`docker inspect redash_server_1 | grep "Image" | grep "redash" | awk 'BEGIN{FS=":"}{print $3}' | awk 'BEGIN{FS="\""}{print $1}'`
echo -e "Current Redash Docker image version is: $CURRENT_IMAGE_VERSION \n"

REQUESTED_CHANNEL="$1"
AVAILABLE_IMAGE_VERSION=`curl -s "https://version.redash.io/releases?channel=$REQUESTED_CHANNEL"  | json_pp  | grep "docker_image" | head -n 1 | awk 'BEGIN{FS=":"}{print $3}' | awk 'BEGIN{FS="\""}{print $1}'`

echo -e "Available Redash Docker image version is: $AVAILABLE_IMAGE_VERSION \n"

var=`semver compare $CURRENT_IMAGE_VERSION $AVAILABLE_IMAGE_VERSION`
if [[ "$var" = "-1" ]]; then
    echo "There is a newer version of Redash Docker Image"
    read -p "Do you want to upgrade it?  [Y/n] : " doUpgrade
    if [[ "$doUpgrade" = "y" || "$doUpgrade" = "Y" ]]; then
        sed -i "/TAG=*/c TAG=${AVAILABLE_IMAGE_VERSION}" $REDASH_BASE_PATH/.env
        cd $REDASH_BASE_PATH
        docker-compose stop server worker > /dev/null
        docker-compose rm -f server worker > /dev/null
        docker-compose pull
        docker-compose run --rm server manage db upgrade
        docker-compose up -d
        echo "Docker image and services were upgraded. Exiting."
    else
        echo "Docker image and services were not upgraded. Exiting."
    fi
else
    echo "There is no new Redash Docker image. Exiting"
fi