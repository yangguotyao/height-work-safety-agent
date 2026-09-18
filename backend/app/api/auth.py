from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, model_validator

from ..platform.auth import current_identity


router = APIRouter(prefix="/api/v1/auth", tags=["账号认证"])
COOKIE_NAME = "height_work_session"


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=200)


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    display_name: str | None = Field(default=None, min_length=2, max_length=40)
    password: str = Field(min_length=5, max_length=200)
    confirm_password: str = Field(min_length=5, max_length=200)

    @model_validator(mode="after")
    def passwords_match(self) -> "RegisterRequest":
        if self.password != self.confirm_password:
            raise ValueError("两次输入的密码不一致")
        return self


class PasswordResetRequest(BaseModel):
    password: str = Field(min_length=5, max_length=200)


class AccountStatusRequest(BaseModel):
    active: bool


def _token(request: Request) -> str:
    return request.cookies.get(COOKIE_NAME, "")


@router.post("/register", status_code=201)
def register(payload: RegisterRequest, request: Request) -> dict:
    user = request.app.state.auth_repository.create_user(
        username=payload.username,
        password=payload.password,
        display_name=payload.display_name or payload.username,
        system_role="user",
        project_id=None,
    )
    return {"user": user, "message": "注册成功，请登录后创建项目。"}


@router.post("/login")
def login(payload: LoginRequest, request: Request, response: Response) -> dict:
    token, identity = request.app.state.auth_repository.login(
        payload.username,
        payload.password,
        user_agent=request.headers.get("user-agent", ""),
        remote_addr=request.client.host if request.client else "",
    )
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=request.app.state.base_settings.auth_session_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=False,
        path="/",
    )
    return {
        "user": identity,
        "needs_project": not bool(identity.get("project_id")),
    }


@router.get("/me")
def me(
    request: Request,
    identity: Annotated[dict, Depends(current_identity)],
) -> dict:
    projects = request.app.state.auth_repository.projects_for_user(identity["id"])
    return {"user": identity, "needs_project": not projects, "projects": projects}


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response) -> Response:
    request.app.state.auth_repository.logout(_token(request))
    response.delete_cookie(COOKIE_NAME, path="/")
    response.status_code = 204
    return response


@router.get("/users")
def users(
    request: Request,
    identity: Annotated[dict, Depends(current_identity)],
) -> list[dict]:
    if identity["system_role"] != "admin":
        raise HTTPException(status_code=403, detail="仅系统管理员可以管理账号")
    return request.app.state.auth_repository.list_users()


@router.patch("/users/{user_id}/status")
def update_user_status(
    user_id: str,
    payload: AccountStatusRequest,
    request: Request,
    identity: Annotated[dict, Depends(current_identity)],
) -> dict:
    if identity["system_role"] != "admin":
        raise HTTPException(status_code=403, detail="仅系统管理员可以管理账号")
    request.app.state.auth_repository.deactivate_user(user_id, payload.active)
    return {"id": user_id, "active": payload.active}


@router.post("/users/{user_id}/reset-password")
def reset_user_password(
    user_id: str,
    payload: PasswordResetRequest,
    request: Request,
    identity: Annotated[dict, Depends(current_identity)],
) -> dict:
    if identity["system_role"] != "admin":
        raise HTTPException(status_code=403, detail="仅系统管理员可以管理账号")
    request.app.state.auth_repository.reset_password(user_id, payload.password)
    return {"id": user_id, "message": "密码已重置"}
