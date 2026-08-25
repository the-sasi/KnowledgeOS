# services/models

Single abstraction layer over language and embedding models, so the rest of
the platform never talks to a provider directly.

Scope (not implemented):

- provider-agnostic interfaces for completion and embedding
- prompt and token accounting
- retry, timeout, and fallback behavior

No provider or model has been chosen. Provider credentials live in `.env`.
