# pydantic-settings-infisical

An [Infisical](https://infisical.com) secrets source for
[pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/).

Declare your configuration as a typed `BaseSettings` class and load values from an
Infisical project — alongside (and with the usual precedence over) environment
variables and dotenv files.

## Install

```bash
pip install pydantic-settings-infisical
```

## Usage

```python
from pydantic_settings import SettingsConfigDict
from pydantic_settings_infisical import InfisicalBaseSettings


class Settings(InfisicalBaseSettings):
    model_config = SettingsConfigDict(
        infisical_project_id="<your-infisical-project-id>",
        infisical_environment="prod",   # default: "prod"
        # infisical_host="https://app.infisical.com",  # or self-hosted
        # infisical_secret_path="/",
    )

    DB_HOST: str
    DB_PASSWORD: str


settings = Settings()
```

`InfisicalBaseSettings` wires Infisical into the source chain for you, with precedence
**init kwargs → Infisical → env → dotenv → file secrets**.

### Custom source ordering

Need a different precedence, or source kwargs like `raise_on_error=True`? Use the source
directly and override `settings_customise_sources` yourself:

```python
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings_infisical import InfisicalSettingsSource


class Settings(BaseSettings):
    model_config = SettingsConfigDict(infisical_project_id="<your-infisical-project-id>")

    DB_HOST: str

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings):
        return (
            init_settings,
            InfisicalSettingsSource(settings_cls, raise_on_error=True),
            env_settings,
            dotenv_settings,
            file_secret_settings,
        )
```

## Authentication

Set **one** of the following (via env var, `model_config`, or a source kwarg):

- **Service token:** `INFISICAL_TOKEN`
- **Machine identity (Universal Auth):** `INFISICAL_CLIENT_ID` + `INFISICAL_CLIENT_SECRET`

## Configuration

Every connection parameter resolves in this order: **explicit source kwarg → `model_config` key → environment variable**.

| Parameter | `model_config` key | Environment variable | Default |
| --- | --- | --- | --- |
| host | `infisical_host` | `INFISICAL_HOST` | `https://app.infisical.com` |
| project id | `infisical_project_id` | `INFISICAL_PROJECT_ID` | *(required)* |
| environment | `infisical_environment` | `INFISICAL_ENVIRONMENT` | `prod` |
| secret path | `infisical_secret_path` | `INFISICAL_SECRET_PATH` | `/` |

## Mapping fields to secret names

Each field is looked up by its Infisical secret name, resolved from the field's
`validation_alias` (the first string of an `AliasChoices`), else its `alias`, else the
field name. This lets you map Python-friendly field names to Infisical's naming:

```python
from pydantic import Field, AliasChoices

class Settings(BaseSettings):
    # reads the Infisical secret "POSTGRES--HOST", falls back to the DB_HOST env var
    db_host: str = Field(validation_alias=AliasChoices("POSTGRES--HOST", "DB_HOST"))
```

## Error handling

By default a missing secret or any Infisical error is **non-fatal** — the value simply
falls through to the remaining settings sources (env, dotenv, …). Pass
`InfisicalSettingsSource(settings_cls, raise_on_error=True)` to fail loudly instead.

## License

MIT
