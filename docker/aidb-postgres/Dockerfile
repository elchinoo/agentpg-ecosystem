# syntax=docker/dockerfile:1
# Postgres 17 with pgvector and EDB AIDB, for local development until the platform grants AIDB roles.
#
# The EDB repository token is a BuildKit secret (`--secret id=edb_token,env=EDB_SUBSCRIPTION_TOKEN`): it exists only for the one RUN
# below, the repository definition that embeds it is deleted in the same layer, and the build fails if the token is found anywhere
# in the filesystem afterwards.
FROM postgres:17-bookworm

ARG EDB_REPO=enterprise

RUN --mount=type=secret,id=edb_token \
    set -eu; \
    EDB_SUBSCRIPTION_TOKEN="$(cat /run/secrets/edb_token)"; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl gnupg; \
    curl -1sSLf "https://downloads.enterprisedb.com/${EDB_SUBSCRIPTION_TOKEN}/${EDB_REPO}/setup.deb.sh" | bash >/dev/null; \
    apt-get update; \
    apt-get install -y --no-install-recommends postgresql-17-pgvector edb-pg17-aidb edb-pg17-pgfs; \
    rm -f /etc/apt/sources.list.d/enterprisedb-* /etc/apt/auth.conf.d/* /etc/apt/trusted.gpg.d/enterprisedb* ; \
    apt-get purge -y curl gnupg; apt-get autoremove -y; \
    rm -rf /var/lib/apt/lists/* /root/.cache /tmp/*; \
    if grep -rqF -- "${EDB_SUBSCRIPTION_TOKEN}" /etc /var/lib/dpkg /var/log /root /usr/share/keyrings 2>/dev/null; then echo "token left in the image" >&2; exit 1; fi

COPY initdb.d/ /docker-entrypoint-initdb.d/
CMD ["postgres", "-c", "shared_preload_libraries=aidb"]
