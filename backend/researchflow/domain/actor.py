"""Actor identity and environment policy."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from uuid import NAMESPACE_URL, UUID, uuid5

from researchflow.errors import DevelopmentActorForbiddenError

DEVELOPMENT_ACTOR_NAMESPACE_NAME: Final = "https://researchflow.local/actors/development"
DEVELOPMENT_ACTOR_ID: Final = uuid5(
    NAMESPACE_URL,
    DEVELOPMENT_ACTOR_NAMESPACE_NAME,
)


class RuntimeEnvironment(StrEnum):
    """Approved deployment environments."""

    LOCAL = "local"
    INTERNAL = "internal"
    EXTERNAL = "external"


class ActorType(StrEnum):
    """Actor origins supported by the current runtime."""

    DEVELOPMENT = "development"


@dataclass(frozen=True, slots=True)
class ActorContext:
    """Identity attached to an application request or command."""

    actor_id: UUID
    actor_type: ActorType
    environment: RuntimeEnvironment

    @classmethod
    def development(cls, environment: RuntimeEnvironment) -> "ActorContext":
        """Construct the fixed development actor when policy permits it."""
        if environment is RuntimeEnvironment.EXTERNAL:
            raise DevelopmentActorForbiddenError()
        return cls(
            actor_id=DEVELOPMENT_ACTOR_ID,
            actor_type=ActorType.DEVELOPMENT,
            environment=environment,
        )
