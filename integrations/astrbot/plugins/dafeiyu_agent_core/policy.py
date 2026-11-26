"""Tool authorization independent from model prompts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Principal:
    id: str
    role: str
    is_private: bool = False


@dataclass(frozen=True)
class ActionRequest:
    tool: str
    risk: str
    target: str = ""
    explicit: bool = False


class PermissionPolicy:
    ROLES = {"owner", "admin", "friend", "member", "system"}
    RISKS = {"read", "write", "high"}

    def authorize(self, principal: Principal, request: ActionRequest) -> tuple[bool, str]:
        if principal.role not in self.ROLES or request.risk not in self.RISKS:
            return False, "invalid policy input"
        if request.tool in {"shell", "service_restart", "plugin_install", "budget_change", "permission_change"}:
            if principal.role != "owner" or not principal.is_private or not request.explicit:
                return False, "owner private explicit approval required"
        if request.risk == "high":
            if principal.role != "owner" or not principal.is_private or not request.explicit:
                return False, "high-risk action requires owner private approval"
        elif request.risk == "write":
            if principal.role not in {"owner", "admin", "system"} or not request.explicit:
                return False, "explicit authorized write required"
        if principal.role in {"member", "friend"} and request.tool in {
            "server_status", "file_read", "email", "calendar", "qzone_write", "group_admin"
        }:
            return False, "tool is not available to this role"
        return True, "allowed"
