"""Domain exceptions raised by the business-logic layer."""


class AgentsbError(Exception):
    """Base class for agentsb failures.

    The CLI layer catches this and prints a clean error message to the
    user; internal layers raise it rather than calling sys.exit() so they
    stay testable and composable.
    """
