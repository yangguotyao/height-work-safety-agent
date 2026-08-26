from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status


def current_identity(request: Request) -> dict:
    workspace = request.app.state.active_workspace
    try:
        return request.app.state.platform_repository.workspace_identity(
            workspace["id"], workspace["name"]
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="项目工作空间尚未初始化",
        ) from exc


def require_roles(*roles: str):
    def dependency(identity: Annotated[dict, Depends(current_identity)]) -> dict:
        if identity["system_role"] == "admin" or identity["project_role"] in roles:
            return identity
        raise HTTPException(status_code=403, detail="当前账号没有执行该操作的权限")

    return dependency
