"""The provider contract.

A provider declares *what it needs* and *how to fetch*; everything else — the
setup form, the OAuth round trip, scheduling, storage, error reporting — is
generated from that declaration. Adding a platform means writing one file in
this folder and nothing else, anywhere.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Sequence

from .. import secret_store


@dataclass(frozen=True)
class Field:
    """One input on the connection form, rendered straight from this spec."""

    key: str
    label: str
    help: str = ""
    secret: bool = False          # masked in the UI and never returned by the API
    required: bool = True
    placeholder: str = ""
    kind: str = "text"            # text | list | number | url | date
    default: str = ""


@dataclass(frozen=True)
class OAuth2:
    """Everything needed to run an authorisation-code flow for this platform."""

    auth_url: str
    token_url: str
    scopes: Sequence[str]
    extra_auth_params: dict[str, str] = field(default_factory=dict)
    # Some platforms want the client credentials in the body, others in Basic auth.
    client_auth: str = "body"     # body | basic
    # Field keys on the connection holding the app credentials.
    client_id_key: str = "client_id"
    client_secret_key: str = "client_secret"


@dataclass
class Connection:
    """A saved account: which provider, plus its config and secrets."""

    id: int
    provider: str
    name: str
    config: dict[str, Any]
    secrets: dict[str, Any]
    enabled: bool = True

    def get(self, key: str, default: Any = None) -> Any:
        if key in self.secrets:
            return self.secrets[key]
        return self.config.get(key, default)

    def list_of(self, key: str) -> list[str]:
        """Read a comma-separated or list-valued field as a list of strings."""
        raw = self.get(key) or []
        if isinstance(raw, str):
            return [x.strip() for x in raw.split(",") if x.strip()]
        return [str(x).strip() for x in raw if str(x).strip()]

    @classmethod
    def from_row(cls, row) -> "Connection":
        config, secrets = secret_store.promote_config(
            json.loads(row["config"] or "{}"), secret_store.resolve(row["secrets"])
        )
        return cls(
            id=row["id"],
            provider=row["provider"],
            name=row["name"],
            config=config,
            secrets=secrets,
            enabled=bool(row["enabled"]),
        )


class Provider(ABC):
    """Base class for every platform the portal can pull from."""

    id: str = ""
    label: str = ""
    category: str = "other"          # ads | analytics | search | crm | commerce | other
    table: str = ""                  # which fact table fetch() returns rows for
    docs_url: str = ""
    summary: str = ""                # one line shown on the connection card
    fields: Sequence[Field] = ()
    oauth: OAuth2 | None = None
    syncable: bool = True

    # -- what the framework asks of a provider ------------------------------
    @abstractmethod
    def fetch(self, conn: Connection, start: date, end: date) -> list[dict]:
        """Pull rows for the window, normalised to this provider's table."""

    def missing(self, conn: Connection) -> list[str]:
        """Required fields this connection has not filled in yet."""
        out = []
        for f in self.fields:
            if f.required and not conn.get(f.key):
                out.append(f.label)
        if self.oauth and not conn.get("refresh_token"):
            out.append("Authorisation")
        return out

    def ready(self, conn: Connection) -> bool:
        return not self.missing(conn)

    def after_load(self, conn: Connection, start: date, end: date) -> None:
        """Optional hook, e.g. refreshing a derived reporting table."""

    # -- spec, as the setup UI consumes it ----------------------------------
    def spec(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "category": self.category,
            "summary": self.summary,
            "docs_url": self.docs_url,
            "auth": "oauth2" if self.oauth else "keys",
            "table": self.table,
            "syncable": self.syncable,
            "fields": [
                {
                    "key": f.key, "label": f.label, "help": f.help,
                    "secret": f.secret, "required": f.required,
                    "placeholder": f.placeholder, "kind": f.kind,
                    "default": f.default,
                }
                for f in self.fields
            ],
        }
