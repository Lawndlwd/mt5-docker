# Copies the files the Windows VM runs (app, requirements, runtime scripts) into the
# `shared` volume, which dockur exposes to Windows as \\host.lan\Data.
# A bind mount of the repo would go stale: Dokploy deletes and re-clones the repo on
# every deploy, and the running windows container would keep the deleted folder.
FROM alpine:3.20
RUN apk add --no-cache rsync
COPY app /src/app
COPY requirements.txt /src/requirements.txt
COPY windows/runtime /src/runtime
COPY sync/sync.sh /sync.sh
CMD ["sh", "/sync.sh"]
