from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import delete
from sqlalchemy.orm import Session

from neareast_api.db.entities import AuthSessionEntity, UserEntity
from neareast_api.db.session import getSession
from neareast_api.modules.auth.dto import (
    AuthDto,
    LoginDto,
    PasswordUpdateDto,
    ProfileUpdateDto,
    SignupDto,
    UserDto,
)
from neareast_api.modules.auth.service import (
    AuthService,
    csrfToken,
    limitAuthentication,
    requireUser,
    tokenDigest,
)

router = APIRouter(tags=["Authentication"])


@router.post(
    "/auth/signup",
    response_model=AuthDto,
    status_code=201,
    dependencies=[Depends(limitAuthentication)],
)
def signup(
    body: SignupDto, request: Request, response: Response, session: Session = Depends(getSession)
):
    user = AuthService.signup(session, body)
    return AuthService.createSession(session, user, request, response)


@router.post("/auth/login", response_model=AuthDto, dependencies=[Depends(limitAuthentication)])
def login(
    body: LoginDto, request: Request, response: Response, session: Session = Depends(getSession)
):
    user = AuthService.login(session, body.email, body.password.get_secret_value())
    return AuthService.createSession(session, user, request, response)


@router.post("/auth/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    user: UserEntity = Depends(requireUser),
    session: Session = Depends(getSession),
):
    settings = request.app.state.settings
    session.execute(
        delete(AuthSessionEntity).where(
            AuthSessionEntity.tokenHash == tokenDigest(request.cookies[settings.sessionCookieName]),
            AuthSessionEntity.userId == user.id,
        )
    )
    session.commit()
    response.delete_cookie(
        settings.sessionCookieName,
        path="/",
        secure=settings.cookieSecure,
        httponly=True,
        samesite="lax",
    )


@router.get("/users/me", response_model=AuthDto)
def currentUser(request: Request, response: Response, user: UserEntity = Depends(requireUser)):
    response.headers["Cache-Control"] = "no-store"
    return AuthDto(
        user=UserDto.model_validate(user),
        csrfToken=csrfToken(request.cookies[request.app.state.settings.sessionCookieName]),
    )


@router.patch("/users/me", response_model=UserDto)
def updateProfile(body: ProfileUpdateDto, response: Response,
                  user: UserEntity = Depends(requireUser), session: Session = Depends(getSession)):
    response.headers["Cache-Control"] = "no-store"
    return AuthService.updateProfile(session, user, body.nickname)


@router.put("/users/me/password", status_code=204, dependencies=[Depends(limitAuthentication)])
def changePassword(body: PasswordUpdateDto, request: Request,
                   user: UserEntity = Depends(requireUser), session: Session = Depends(getSession)):
    AuthService.changePassword(
        session, user, body.currentPassword.get_secret_value(), body.newPassword.get_secret_value(),
        request.cookies[request.app.state.settings.sessionCookieName],
    )


@router.delete("/users/me", status_code=204)
def deleteAccount(request: Request, response: Response,
                  user: UserEntity = Depends(requireUser), session: Session = Depends(getSession)):
    AuthService.deleteAccount(session, user)
    response.delete_cookie(
        request.app.state.settings.sessionCookieName, path="/", httponly=True,
        secure=request.app.state.settings.cookieSecure, samesite="lax",
    )
