# KnowledgeOS - base image placeholder
#
# Intentionally not implemented. The base image depends on the language and
# framework chosen for the first application, which has not been decided yet.
# Record that decision in docs/decisions/ before filling this in.
#
# Per-service Dockerfiles that diverge from this base belong in
# infrastructure/docker/.
#
# Sketch of the intended shape:
#
#   FROM <base-image>
#   WORKDIR /app
#   COPY <dependency-manifest> ./
#   RUN <install-dependencies>
#   COPY . .
#   CMD ["<entrypoint>"]

# This file will not build as-is. That is deliberate.
