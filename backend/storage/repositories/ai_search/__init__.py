"""AI search repository mixins grouped by subdomain."""

from .checkpoints import AiSearchCheckpointsRepositoryMixin
from .documents import AiSearchDocumentsRepositoryMixin
from .messages import AiSearchMessagesPlansRepositoryMixin
from .pending_actions import AiSearchPendingActionsRepositoryMixin
from .runs import AiSearchRunsRepositoryMixin


class AiSearchRepositoryMixin(
    AiSearchMessagesPlansRepositoryMixin,
    AiSearchRunsRepositoryMixin,
    AiSearchPendingActionsRepositoryMixin,
    AiSearchDocumentsRepositoryMixin,
    AiSearchCheckpointsRepositoryMixin,
):
    """Composite AI search repository API used by the storage facade."""

    pass


__all__ = ["AiSearchRepositoryMixin"]
