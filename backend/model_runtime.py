"""Fail-closed production model configuration and SDK construction."""

from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping
from urllib.parse import urlsplit

from dotenv import dotenv_values
from openai import OpenAI


REQUIRED_MODEL_ENV_KEYS = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_MODEL",
)
_PLACEHOLDER_KEYS = {"replace-with-your-api-key", "your-api-key", "changeme"}
_PLACEHOLDER_MODELS = {"replace-with-your-model-name", "your-model-name", "model-name"}
_PLACEHOLDER_HOSTS = {"example.com", "api.example.com"}
_DNS_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


@dataclass(frozen=True)
class ModelRuntimeConfiguration:
    api_key: str = field(repr=False)
    base_url: str
    model: str


@dataclass(frozen=True)
class ModelRuntimeSession:
    configuration: ModelRuntimeConfiguration
    client: object = field(repr=False)


class ModelConfigurationError(ValueError):
    """A model configuration failure that is safe to expose by code only."""

    def __init__(self, blockers: Iterable[str], variables: Iterable[str] = ()):
        self.code = "model_configuration_invalid"
        self.blockers = tuple(dict.fromkeys(blockers))
        self.conflicting_variables = tuple(dict.fromkeys(variables))
        super().__init__(self.code)

    def safe_detail(self) -> dict:
        return {
            "code": self.code,
            "blockers": list(self.blockers),
            "variables": list(self.conflicting_variables),
        }


def _present(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _valid_hostname(hostname: str) -> bool:
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        labels = hostname.split(".")
        return bool(labels) and all(_DNS_LABEL.fullmatch(label) for label in labels)


def _endpoint_errors(endpoint: str) -> list[str]:
    errors = []
    if any(character.isspace() for character in endpoint):
        errors.append("model_endpoint_contains_whitespace")
        return errors
    try:
        parsed = urlsplit(endpoint)
        scheme = parsed.scheme
        username = parsed.username
        password = parsed.password
        query = parsed.query
        fragment = parsed.fragment
        hostname = parsed.hostname or ""
    except (UnicodeError, ValueError):
        return ["model_endpoint_invalid_url"]
    if scheme != "https":
        errors.append("model_endpoint_must_use_https")
    if username is not None or password is not None:
        errors.append("model_endpoint_contains_credentials")
    if query:
        errors.append("model_endpoint_contains_query")
    if fragment:
        errors.append("model_endpoint_contains_fragment")
    try:
        parsed.port
    except ValueError:
        errors.append("model_endpoint_invalid_port")
    if not hostname or not _valid_hostname(hostname):
        errors.append("model_endpoint_invalid_host")
    elif hostname.casefold() in _PLACEHOLDER_HOSTS:
        errors.append("model_endpoint_is_placeholder")
    return errors


def resolve_model_runtime_config(
    process_environment: Mapping[str, str | None],
    environment_file_values: Mapping[str, str | None],
) -> ModelRuntimeConfiguration:
    conflicts = [
        name for name in REQUIRED_MODEL_ENV_KEYS
        if _present(process_environment.get(name))
        and _present(environment_file_values.get(name))
        and process_environment.get(name) != environment_file_values.get(name)
    ]
    if conflicts:
        raise ModelConfigurationError(
            ["model_configuration_source_conflict"], conflicts
        )

    values = {
        name: (process_environment.get(name)
               if _present(process_environment.get(name))
               else environment_file_values.get(name))
        for name in REQUIRED_MODEL_ENV_KEYS
    }
    blockers: list[str] = []
    variables: list[str] = []

    key = values["OPENAI_API_KEY"]
    if not _present(key):
        blockers.append("model_credential_not_configured")
        variables.append("OPENAI_API_KEY")
    elif key.casefold() in _PLACEHOLDER_KEYS:
        blockers.append("model_credential_is_placeholder")
        variables.append("OPENAI_API_KEY")
    elif any(character.isspace() for character in key):
        blockers.append("model_credential_contains_whitespace")
        variables.append("OPENAI_API_KEY")

    endpoint = values["OPENAI_BASE_URL"]
    if not _present(endpoint):
        blockers.append("model_endpoint_not_explicitly_configured")
        variables.append("OPENAI_BASE_URL")
    else:
        endpoint_blockers = _endpoint_errors(endpoint)
        blockers.extend(endpoint_blockers)
        if endpoint_blockers:
            variables.append("OPENAI_BASE_URL")

    model = values["OPENAI_MODEL"]
    if not _present(model):
        blockers.append("requested_model_not_explicitly_configured")
        variables.append("OPENAI_MODEL")
    elif model.casefold() in _PLACEHOLDER_MODELS:
        blockers.append("requested_model_is_placeholder")
        variables.append("OPENAI_MODEL")
    elif any(character.isspace() for character in model):
        blockers.append("requested_model_contains_whitespace")
        variables.append("OPENAI_MODEL")

    if blockers:
        raise ModelConfigurationError(blockers, variables)
    return ModelRuntimeConfiguration(api_key=key, base_url=endpoint, model=model)


def require_model_runtime_config(
    environment_file: Path,
    process_environment: Mapping[str, str | None] | None = None,
) -> ModelRuntimeConfiguration:
    try:
        file_values = (
            {name: value for name, value in dotenv_values(environment_file).items()
             if name in REQUIRED_MODEL_ENV_KEYS}
            if environment_file.is_file() else {}
        )
    except Exception:
        raise ModelConfigurationError(
            ["model_environment_file_unreadable"], REQUIRED_MODEL_ENV_KEYS
        ) from None
    return resolve_model_runtime_config(
        os.environ if process_environment is None else process_environment,
        file_values,
    )


def create_model_session(
    configuration: ModelRuntimeConfiguration,
    *,
    timeout: int,
    max_retries: int,
    client_factory=OpenAI,
) -> ModelRuntimeSession:
    client = client_factory(
        api_key=configuration.api_key,
        base_url=configuration.base_url,
        timeout=timeout,
        max_retries=max_retries,
    )
    return ModelRuntimeSession(configuration=configuration, client=client)


def create_validated_chat_completion(
    session: ModelRuntimeSession,
    environment_file: Path,
    *,
    process_environment: Mapping[str, str | None] | None = None,
    **request,
):
    current = require_model_runtime_config(
        environment_file, process_environment=process_environment
    )
    attributes = {
        "OPENAI_API_KEY": "api_key",
        "OPENAI_BASE_URL": "base_url",
        "OPENAI_MODEL": "model",
    }
    changed = [
        variable for variable, attribute in attributes.items()
        if getattr(current, attribute) != getattr(session.configuration, attribute)
    ]
    if changed:
        raise ModelConfigurationError(["model_configuration_changed"], changed)
    if request.get("model") != session.configuration.model:
        raise ModelConfigurationError(
            ["model_request_model_mismatch"], ["OPENAI_MODEL"]
        )
    return session.client.chat.completions.create(**request)
