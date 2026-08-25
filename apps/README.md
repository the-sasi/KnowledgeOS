# apps

Deployable entry points. An app wires `services/` together and exposes them;
it should contain as little logic of its own as possible.

- `api/` — backend service exposing KnowledgeOS over HTTP
- `web/` — user-facing interface

Framework choices are deliberately deferred.
