# config

Non-secret configuration, kept separate from application code so behavior can
change without touching modules.

- `settings.example.yaml` — annotated template of application settings
- environment-specific overrides are added here as needed

Secrets never live here. They belong in `.env`, which is git-ignored.
