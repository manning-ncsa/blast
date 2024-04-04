#!/bin/bash

set -eo pipefail

# If .dustmapsrc has been mounted as a read-only file at /tmp/.dustmapsrc,
# copy it to the expected location /root/.dustmapsrc if it does not already exist.
if [[ ! -f "/root/.dustmapsrc" && -f "/tmp/.dustmapsrc" ]]; then
  cp /tmp/.dustmapsrc /root/.dustmapsrc
fi

INIT_STARTED_DB=/mnt/data/.initializing_db
INIT_STARTED_DATA=/mnt/data/.initializing_data

## Initialize astro data
##
if [[ -f "${INIT_STARTED_DATA}" ]]
then
  echo "Astro data is currently being initialized (\"${INIT_STARTED_DATA}\" exists)."
  sleep 10
  exit 1
else
  echo "\"${INIT_STARTED_DATA}\" not found. Running initialization script..."
  touch "${INIT_STARTED_DATA}"

  # Download and install data in parallel
  bash entrypoints/initialize_all_data.sh

  rm -f "${INIT_STARTED_DATA}"
fi

## Initialize Django database and static files
##
bash entrypoints/wait-for-it.sh ${DATABASE_HOST}:${DATABASE_PORT} --timeout=0

if [[ -f "${INIT_STARTED_DB}" ]]
then
  echo "Django database and static files are currently being initialized (\"${INIT_STARTED_DB}\" exists)."
  sleep 10
  exit 1
else
  echo "\"${INIT_STARTED_DB}\" not found. Running initialization script..."
  touch "${INIT_STARTED_DB}"

  python init.py

  rm -f "${INIT_STARTED_DB}"
  echo "Django database initialization complete."
fi

# Start server
if [[ $DEV_MODE == 1 ]]; then
  python manage.py runserver 0.0.0.0:${WEB_APP_PORT}
else
  bash entrypoints/wait-for-it.sh ${WEB_SERVER_HOST}:${WEB_SERVER_PORT} --timeout=0
  gunicorn app.wsgi --timeout 0 --bind 0.0.0.0:${WEB_APP_PORT} --workers=${GUNICORN_WORKERS:=1}
fi

# INIT_CHECK_FILE=/app/static/.initialized
# # The INIT_STARTED_FILE is necessary when multiple parallel containers
# # are launched for example in Kubernetes when there are multiple replicas.
# INIT_STARTED_FILE=/app/static/.initializing
# if [[ -f "${INIT_CHECK_FILE}" ]]
# then
#   echo "Application already initialized (\"${INIT_CHECK_FILE}\" exists)."
# elif [[ -f "${INIT_STARTED_FILE}" ]]
# then
#   echo "Application is currently being initialized (\"${INIT_STARTED_FILE}\" exists)."
#   sleep 10
#   exit 1
# else
#   echo "\"${INIT_CHECK_FILE}\" not found. Running initialization script..."
#   touch "${INIT_STARTED_FILE}"

#   # Download and install all required data files
#   if [[ "${INITIALIZE_DATA}" == "true" ]]; then
#     bash entrypoints/initialize_all_data.sh
#   fi

#   python init.py

#   touch "${INIT_CHECK_FILE}"
#   rm -f "${INIT_STARTED_FILE}"
# fi
