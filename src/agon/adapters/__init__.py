"""Platform adapters: the fixture backend tests run on, and the live Meta one."""

from agon.adapters.base import (
    AdapterError,
    AdPlatformAdapter,
    EntitySnapshot,
    EntityType,
    IncompletePullError,
    PostIdMismatchError,
    WriteRefusedError,
)

__all__ = [
    "AdPlatformAdapter",
    "AdapterError",
    "EntitySnapshot",
    "EntityType",
    "IncompletePullError",
    "PostIdMismatchError",
    "WriteRefusedError",
]
