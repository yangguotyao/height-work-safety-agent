from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status


def current_identity(request: Request) -> dict:
    identity = getattr(request.state, "identity", None)
    if identity is None and not request.app.state.base_settings.enforce_auth:
        workspace = request.app.state.active_workspace
        return request.app.state.auth_repository.workspace_identity(
            workspace.get("id", "default-project"),
            workspace.get("name", "项目工作空间"),
        )
    if identity is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
    return identity


def require_roles(*roles: str):
    def dependency(identity: Annotated[dict, Depends(current_identity)]) -> dict:
        if identity["system_role"] == "admin" or identity["project_role"] in roles:
            return identity
        raise HTTPException(status_code=403, detail="当前账号没有执行该操作的权限")

    return dependency
