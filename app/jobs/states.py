"""Job lifecycle state machine (task 3.1)."""
STATES = ["DRAFT","QUEUED","INITIALIZING","RUNNING","CHECKPOINTING","RECOVERING",
          "CLUSTERING","EXPLAINING","COMPLETED","FAILED","CANCELLED","SUSPENDED"]
TERMINAL = {"COMPLETED","FAILED","CANCELLED"}
ALLOWED: dict[str, set[str]] = {
  "DRAFT": {"QUEUED","CANCELLED"},
  "QUEUED": {"INITIALIZING","CANCELLED"},
  "INITIALIZING": {"RUNNING","FAILED","CANCELLED"},
  "RUNNING": {"CHECKPOINTING","CLUSTERING","FAILED","CANCELLED","SUSPENDED"},
  "CHECKPOINTING": {"RUNNING","CLUSTERING","FAILED","CANCELLED","SUSPENDED"},
  "RECOVERING": {"RUNNING","FAILED","CANCELLED"},
  "CLUSTERING": {"EXPLAINING","FAILED","CANCELLED"},
  "EXPLAINING": {"COMPLETED","FAILED","CANCELLED"},
  "SUSPENDED": {"RUNNING","CANCELLED"},
  "FAILED": {"QUEUED","CANCELLED"},  # retry re-queues
  "COMPLETED": set(), "CANCELLED": {"QUEUED"},
}

class InvalidTransition(ValueError): pass

def validate(frm: str, to: str) -> None:
    if to not in ALLOWED.get(frm, set()):
        raise InvalidTransition(f"{frm} -> {to} not allowed")
