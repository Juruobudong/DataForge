class DataForgeError(Exception):
    """Base exception for expected application failures."""


class NotFoundError(DataForgeError):
    """Requested domain object does not exist."""


class ValidationError(DataForgeError):
    """Input or state transition is invalid."""


class EngineUnavailableError(DataForgeError):
    """The requested processing engine cannot be loaded."""
