# infrastructure/docker

Docker assets that do not belong at the repository root:

- per-service Dockerfiles
- `docker-compose.override.yml` for local development
- entrypoint and healthcheck scripts

The root `Dockerfile` and `docker-compose.yml` stay minimal and reference this
folder as the platform grows.
