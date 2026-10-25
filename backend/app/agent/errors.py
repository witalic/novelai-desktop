"""How an agent's tool call can fail — the protocol layer turns each into a tool error the agent reads, and logs
the ones the user should see (``protocol``)."""


class ToolError(Exception):
    """The call can't be done as asked (bad argument, missing work, …) — the message goes back to the agent."""


class Refused(ToolError):
    """Refused by the user's switches or limits (e.g. Anlas): logged as denied. The message tells the agent what
    to do; ``detail`` is the short line the user's activity feed shows."""

    def __init__(self, message: str, detail: str | None = None) -> None:
        super().__init__(message)
        self.detail = detail or message


class GenerationFailed(ToolError):
    """A generation went wrong after its request was sent — the prompt left the machine and Anlas may be spent,
    so it is logged and announced. ``cost``: the Anlas still counted against today's budget."""

    def __init__(self, message: str, cost: int) -> None:
        super().__init__(message)
        self.cost = cost
