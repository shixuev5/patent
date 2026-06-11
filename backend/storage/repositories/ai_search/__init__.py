"""AI search repository mixins grouped by subdomain."""

from .checkpoints import AiSearchCheckpointsRepositoryMixin
from .documents import AiSearchDocumentsRepositoryMixin
from .messages import AiSearchMessagesPlansRepositoryMixin
from .runs import AiSearchRunsRepositoryMixin


class AiSearchRepositoryMixin(
    AiSearchMessagesPlansRepositoryMixin,
    AiSearchRunsRepositoryMixin,
    AiSearchDocumentsRepositoryMixin,
    AiSearchCheckpointsRepositoryMixin,
):
    """Composite AI search repository API used by the storage facade."""

    pass


__all__ = ["AiSearchRepositoryMixin"]
