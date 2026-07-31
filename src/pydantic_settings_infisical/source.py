"""An Infisical settings source for pydantic-settings."""

from __future__ import annotations

import logging
import os
from typing import Any

from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

logger = logging.getLogger(__name__)


class InfisicalSettingsSource(PydanticBaseSettingsSource):
    """A pydantic-settings source that reads values from an Infisical project.

    Connection params each resolve in this order: explicit keyword argument ->
    ``model_config`` key (e.g. ``infisical_project_id=...``) -> environment variable
    (e.g. ``INFISICAL_PROJECT_ID``).

    Authentication uses either a service **token** (``INFISICAL_TOKEN``) or a
    **machine identity** via Universal Auth (``INFISICAL_CLIENT_ID`` +
    ``INFISICAL_CLIENT_SECRET``).

    Each settings field is looked up by its Infisical secret name, taken from the
    field's ``validation_alias`` (the first string of an ``AliasChoices``), else its
    ``alias``, else the field name. Secrets that are missing (or any Infisical error)
    fall through to the other settings sources; set ``raise_on_error=True`` to fail loud.
    """

    def __init__(
        self,
        settings_cls: type,
        *,
        host: str | None = None,
        project_id: str | None = None,
        environment: str | None = None,
        secret_path: str | None = None,
        token: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        raise_on_error: bool = False,
    ) -> None:
        super().__init__(settings_cls)
        self.host = self._resolve("host", host) or "https://app.infisical.com"
        self.project_id = self._resolve("project_id", project_id)
        self.environment = self._resolve("environment", environment) or "prod"
        self.secret_path = self._resolve("secret_path", secret_path) or "/"
        self.token = self._resolve("token", token)
        self.client_id = self._resolve("client_id", client_id)
        self.client_secret = self._resolve("client_secret", client_secret)
        self.raise_on_error = raise_on_error

        self._secrets: dict[str, str] = self._load()

    def _resolve(self, name: str, override: str | None) -> str | None:
        """Kwarg -> ``model_config["infisical_<name>"]`` -> ``$INFISICAL_<NAME>``."""
        return override or self.config.get(f"infisical_{name}") or os.environ.get(f"INFISICAL_{name.upper()}")

    def _build_client(self) -> Any:
        from infisical_sdk import InfisicalSDKClient

        if self.token:
            return InfisicalSDKClient(host=self.host, token=self.token)

        if not (self.client_id and self.client_secret):
            raise ValueError(
                "Infisical auth requires either INFISICAL_TOKEN, or "
                "INFISICAL_CLIENT_ID + INFISICAL_CLIENT_SECRET (machine identity)."
            )
        client = InfisicalSDKClient(host=self.host)
        client.auth.universal_auth.login(client_id=self.client_id, client_secret=self.client_secret)
        return client

    def _load(self) -> dict[str, str]:
        if not self.project_id:
            return self._fail("Infisical project_id is not set (infisical_project_id / INFISICAL_PROJECT_ID)")
        try:
            # One list call per source rather than a lookup per field. Trade-off: unlike
            # per-field get_secret_by_name, this may not resolve secret references/imports.
            result = self._build_client().secrets.list_secrets(
                project_id=self.project_id,
                environment_slug=self.environment,
                secret_path=self.secret_path,
            )
            return {s.secretKey: s.secretValue for s in result.secrets}
        except Exception as exc:  # noqa: BLE001 - fall through to other sources unless strict
            return self._fail(f"Infisical fetch failed ({exc})")

    def _fail(self, message: str) -> dict[str, str]:
        if self.raise_on_error:
            raise RuntimeError(message)
        logger.warning("%s; falling back to other settings sources", message)
        return {}

    @staticmethod
    def _secret_name(field: FieldInfo, field_name: str) -> str:
        alias = field.validation_alias
        if isinstance(alias, str):
            return alias
        default = field.alias or field_name
        return next((c for c in getattr(alias, "choices", ()) if isinstance(c, str)), default)

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        # Key the value by the secret name (validation_alias / alias / field name). That is the
        # key pydantic accepts for an aliased field — returning the field name would be rejected
        # as an extra input when the field defines a validation_alias.
        secret_name = self._secret_name(field, field_name)
        return self._secrets.get(secret_name), secret_name, False

    def __call__(self) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for field_name, field in self.settings_cls.model_fields.items():
            value, key, is_complex = self.get_field_value(field, field_name)
            if value is not None:
                # Inherited prepare_field_value JSON-decodes values bound for complex
                # fields (list/dict/...), exactly as the env source does.
                values[key] = self.prepare_field_value(key, field, value, is_complex)
        return values


class InfisicalBaseSettings(BaseSettings):
    """A ``BaseSettings`` with Infisical pre-wired into the source chain.

    Subclass this instead of ``BaseSettings`` to skip the ``settings_customise_sources``
    boilerplate. Precedence: init kwargs > Infisical > env > dotenv > file secrets. Configure
    the connection via ``model_config`` keys (``infisical_project_id=...``) or the matching
    ``INFISICAL_*`` environment variables. Need a different ordering or source kwargs
    (e.g. ``raise_on_error=True``)? Use ``InfisicalSettingsSource`` directly instead.
    """

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings):
        return (init_settings, InfisicalSettingsSource(settings_cls), env_settings, dotenv_settings, file_secret_settings)
