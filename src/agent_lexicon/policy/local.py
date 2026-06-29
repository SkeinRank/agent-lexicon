"""Local RBAC-lite policy modes for Agent Lexicon workflows.

The local policy layer is intentionally small: it does not authenticate users and
it does not replace enterprise RBAC. It gives local repositories and CI scripts a
predictable way to decide whether a declared actor/role may perform sensitive
workspace actions such as review decisions, snapshot publishing, or future LLM
review.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from agent_lexicon.workspace import DEFAULT_WORKSPACE_DIR


class LocalPolicyError(ValueError):
    """Raised when a local policy file or policy request is invalid."""


class LocalPolicyMode(str, Enum):
    """Local policy mode for a workspace."""

    SOLO = "solo"
    TEAM = "team"
    LOCKED = "locked"


class LocalPolicyRole(str, Enum):
    """RBAC-lite role names used by local policy checks."""

    OWNER = "owner"
    MAINTAINER = "maintainer"
    REVIEWER = "reviewer"
    READER = "reader"
    AGENT = "agent"


class PolicyAction(str, Enum):
    """Sensitive local actions that can be checked by policy."""

    READ_WORKSPACE = "read_workspace"
    SYNC_WORKSPACE = "sync_workspace"
    REVIEW_CANDIDATE = "review_candidate"
    EXPORT_REVIEW_EVENTS = "export_review_events"
    PUBLISH_SNAPSHOT = "publish_snapshot"
    RUN_LLM_REVIEW = "run_llm_review"
    MANAGE_POLICY = "manage_policy"


DEFAULT_POLICY_FILENAME = "policy.json"
_POLICY_VERSION = 1

_MODE_ALLOWED_ROLES: dict[LocalPolicyMode, dict[PolicyAction, frozenset[LocalPolicyRole]]] = {
    LocalPolicyMode.SOLO: {
        action: frozenset(LocalPolicyRole)
        for action in PolicyAction
    },
    LocalPolicyMode.TEAM: {
        PolicyAction.READ_WORKSPACE: frozenset(LocalPolicyRole),
        PolicyAction.SYNC_WORKSPACE: frozenset({LocalPolicyRole.OWNER, LocalPolicyRole.MAINTAINER}),
        PolicyAction.REVIEW_CANDIDATE: frozenset({
            LocalPolicyRole.OWNER,
            LocalPolicyRole.MAINTAINER,
            LocalPolicyRole.REVIEWER,
        }),
        PolicyAction.EXPORT_REVIEW_EVENTS: frozenset({
            LocalPolicyRole.OWNER,
            LocalPolicyRole.MAINTAINER,
            LocalPolicyRole.REVIEWER,
        }),
        PolicyAction.PUBLISH_SNAPSHOT: frozenset({LocalPolicyRole.OWNER, LocalPolicyRole.MAINTAINER}),
        PolicyAction.RUN_LLM_REVIEW: frozenset({LocalPolicyRole.OWNER, LocalPolicyRole.MAINTAINER}),
        PolicyAction.MANAGE_POLICY: frozenset({LocalPolicyRole.OWNER}),
    },
    LocalPolicyMode.LOCKED: {
        PolicyAction.READ_WORKSPACE: frozenset(LocalPolicyRole),
        PolicyAction.SYNC_WORKSPACE: frozenset({LocalPolicyRole.OWNER}),
        PolicyAction.REVIEW_CANDIDATE: frozenset({LocalPolicyRole.OWNER}),
        PolicyAction.EXPORT_REVIEW_EVENTS: frozenset({LocalPolicyRole.OWNER}),
        PolicyAction.PUBLISH_SNAPSHOT: frozenset({LocalPolicyRole.OWNER}),
        PolicyAction.RUN_LLM_REVIEW: frozenset({LocalPolicyRole.OWNER}),
        PolicyAction.MANAGE_POLICY: frozenset({LocalPolicyRole.OWNER}),
    },
}

_DEFAULT_ROLE_BY_MODE = {
    LocalPolicyMode.SOLO: LocalPolicyRole.OWNER,
    LocalPolicyMode.TEAM: LocalPolicyRole.READER,
    LocalPolicyMode.LOCKED: LocalPolicyRole.READER,
}


@dataclass(frozen=True, slots=True)
class LocalPolicy:
    """Local policy configuration for one workspace."""

    mode: LocalPolicyMode = LocalPolicyMode.SOLO
    default_role: LocalPolicyRole | None = None
    actors: Mapping[str, LocalPolicyRole | str] = field(default_factory=dict)
    version: int = _POLICY_VERSION
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        mode = _coerce_mode(self.mode)
        default_role = self.default_role
        if default_role is None:
            default_role = _DEFAULT_ROLE_BY_MODE[mode]
        default_role = _coerce_role(default_role)
        actors: dict[str, LocalPolicyRole] = {}
        if not isinstance(self.actors, Mapping):
            raise LocalPolicyError("actors must be a mapping")
        for actor, role in self.actors.items():
            actor_value = _clean_text(str(actor), field_name="actor")
            actors[actor_value] = _coerce_role(role)
        if not isinstance(self.metadata, Mapping):
            raise LocalPolicyError("metadata must be a mapping")
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "default_role", default_role)
        object.__setattr__(self, "actors", actors)
        object.__setattr__(self, "metadata", dict(self.metadata))
        if self.version != _POLICY_VERSION:
            raise LocalPolicyError(f"unsupported policy version: {self.version}")

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "LocalPolicy":
        """Build a local policy from a JSON-compatible mapping."""
        if not isinstance(payload, Mapping):
            raise LocalPolicyError("policy payload must be a mapping")
        return cls(
            version=int(payload.get("version", _POLICY_VERSION)),
            mode=payload.get("mode", LocalPolicyMode.SOLO.value),
            default_role=payload.get("default_role"),
            actors=payload.get("actors", {}),
            metadata=payload.get("metadata", {}),
        )

    def with_mode(self, mode: LocalPolicyMode | str | None) -> "LocalPolicy":
        """Return a copy with an optional mode override."""
        if mode is None:
            return self
        new_mode = _coerce_mode(mode)
        return replace(self, mode=new_mode, default_role=_DEFAULT_ROLE_BY_MODE[new_mode])

    def role_for_actor(self, actor: str = "local", role: LocalPolicyRole | str | None = None) -> LocalPolicyRole:
        """Resolve the effective role for an actor."""
        if role is not None:
            return _coerce_role(role)
        actor_value = _clean_text(actor, field_name="actor")
        if actor_value in self.actors:
            return _coerce_role(self.actors[actor_value])
        assert self.default_role is not None
        return self.default_role

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable policy document."""
        return {
            "version": self.version,
            "mode": self.mode.value,
            "default_role": self.default_role.value if self.default_role is not None else None,
            "actors": {actor: _coerce_role(role).value for actor, role in self.actors.items()},
            "metadata": dict(self.metadata),
        }

    def to_json(self) -> str:
        """Return the policy document as pretty JSON."""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """Result of checking one local policy action."""

    action: PolicyAction
    allowed: bool
    mode: LocalPolicyMode
    actor: str
    role: LocalPolicyRole
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "action", _coerce_action(self.action))
        object.__setattr__(self, "mode", _coerce_mode(self.mode))
        object.__setattr__(self, "actor", _clean_text(self.actor, field_name="actor"))
        object.__setattr__(self, "role", _coerce_role(self.role))
        object.__setattr__(self, "reason", _clean_text(self.reason, field_name="reason"))

    @property
    def is_allowed(self) -> bool:
        """Return whether the action is allowed."""
        return self.allowed

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable policy decision."""
        return {
            "action": self.action.value,
            "allowed": self.allowed,
            "mode": self.mode.value,
            "actor": self.actor,
            "role": self.role.value,
            "reason": self.reason,
        }

    def to_json(self) -> str:
        """Return this decision as pretty JSON."""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)


def policy_path(
    root: str | Path = ".",
    *,
    workspace_dir: str = DEFAULT_WORKSPACE_DIR,
    policy_filename: str = DEFAULT_POLICY_FILENAME,
) -> Path:
    """Return the default local policy file path for a workspace root."""
    return Path(root).resolve() / _clean_name(workspace_dir, field_name="workspace_dir") / _clean_name(
        policy_filename,
        field_name="policy_filename",
    )


def init_local_policy(
    root: str | Path = ".",
    *,
    mode: LocalPolicyMode | str = LocalPolicyMode.SOLO,
    actor: str = "local",
    role: LocalPolicyRole | str = LocalPolicyRole.OWNER,
    force: bool = False,
) -> LocalPolicy:
    """Create a local policy file under `.agent-lexicon/policy.json`."""
    actor_value = _clean_text(actor, field_name="actor")
    role_value = _coerce_role(role)
    mode_value = _coerce_mode(mode)
    path = policy_path(root)
    if path.exists() and not force:
        raise LocalPolicyError(f"local policy already exists: {path}")
    policy = LocalPolicy(
        mode=mode_value,
        default_role=_DEFAULT_ROLE_BY_MODE[mode_value],
        actors={actor_value: role_value},
        metadata={"source": "local"},
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(policy.to_json() + "\n", encoding="utf-8")
    return policy


def load_local_policy(
    root: str | Path = ".",
    *,
    mode: LocalPolicyMode | str | None = None,
) -> LocalPolicy:
    """Load a local policy or return the default solo policy when none exists."""
    path = policy_path(root)
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise LocalPolicyError(f"invalid local policy JSON: {path}: {exc}") from exc
        policy = LocalPolicy.from_dict(payload)
    else:
        mode_value = _coerce_mode(mode) if mode is not None else LocalPolicyMode.SOLO
        policy = LocalPolicy(mode=mode_value, default_role=_DEFAULT_ROLE_BY_MODE[mode_value])
    return policy.with_mode(mode)


def check_local_policy(
    policy: LocalPolicy,
    action: PolicyAction | str,
    *,
    actor: str = "local",
    role: LocalPolicyRole | str | None = None,
) -> PolicyDecision:
    """Check whether an actor/role may perform an action under a local policy."""
    if not isinstance(policy, LocalPolicy):
        raise LocalPolicyError("policy must be a LocalPolicy")
    action_value = _coerce_action(action)
    actor_value = _clean_text(actor, field_name="actor")
    role_value = policy.role_for_actor(actor_value, role=role)
    allowed_roles = _MODE_ALLOWED_ROLES[policy.mode][action_value]
    allowed = role_value in allowed_roles
    if allowed:
        reason = f"{role_value.value} may {action_value.value} in {policy.mode.value} mode"
    else:
        role_names = ", ".join(sorted(role.value for role in allowed_roles)) or "none"
        reason = (
            f"{role_value.value} may not {action_value.value} in {policy.mode.value} mode; "
            f"allowed roles: {role_names}"
        )
    return PolicyDecision(
        action=action_value,
        allowed=allowed,
        mode=policy.mode,
        actor=actor_value,
        role=role_value,
        reason=reason,
    )


def _coerce_mode(value: LocalPolicyMode | str) -> LocalPolicyMode:
    try:
        return value if isinstance(value, LocalPolicyMode) else LocalPolicyMode(str(value))
    except ValueError as exc:
        allowed = ", ".join(mode.value for mode in LocalPolicyMode)
        raise LocalPolicyError(f"invalid policy mode {value!r}; expected one of: {allowed}") from exc


def _coerce_role(value: LocalPolicyRole | str) -> LocalPolicyRole:
    try:
        return value if isinstance(value, LocalPolicyRole) else LocalPolicyRole(str(value))
    except ValueError as exc:
        allowed = ", ".join(role.value for role in LocalPolicyRole)
        raise LocalPolicyError(f"invalid policy role {value!r}; expected one of: {allowed}") from exc


def _coerce_action(value: PolicyAction | str) -> PolicyAction:
    try:
        return value if isinstance(value, PolicyAction) else PolicyAction(str(value))
    except ValueError as exc:
        allowed = ", ".join(action.value for action in PolicyAction)
        raise LocalPolicyError(f"invalid policy action {value!r}; expected one of: {allowed}") from exc


def _clean_text(value: str, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise LocalPolicyError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise LocalPolicyError(f"{field_name} must not be empty")
    return cleaned


def _clean_name(value: str, *, field_name: str) -> str:
    cleaned = _clean_text(value, field_name=field_name)
    if "/" in cleaned or "\\" in cleaned:
        raise LocalPolicyError(f"{field_name} must be a single path segment")
    return cleaned
