import hashlib
import hmac
import secrets
from datetime import timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, Header, HTTPException, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from neareast_api.db.entities import AuthSessionEntity, UserEntity, currentTime
from neareast_api.db.session import getSession
from neareast_api.modules.auth.dto import AuthDto, SignupDto, UserDto
from neareast_api.modules.chat.limits import RateLimiter

PASSWORD_HASHER = PasswordHasher()
DUMMY_PASSWORD_HASH = PASSWORD_HASHER.hash(secrets.token_urlsafe(32))
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def tokenDigest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def csrfToken(sessionToken: str) -> str:
    return tokenDigest(f"csrf:{sessionToken}")


def checkOrigin(request: Request) -> None:
    origin = request.headers.get("origin")
    allowedOrigins = {
        value.strip().rstrip("/")
        for value in request.app.state.settings.cors_allow_origins.split(",")
    }
    allowedOrigins.add(str(request.base_url).rstrip("/"))
    if origin and origin.rstrip("/") not in allowedOrigins:
        raise HTTPException(403, "Origin is not allowed")


def limitAuthentication(request: Request) -> None:
    checkOrigin(request)
    limiter = getattr(request.app.state, "authLimiter", None)
    if limiter is None:
        limiter = RateLimiter(10)
        request.app.state.authLimiter = limiter
    waitSeconds = limiter.check(request.client.host if request.client else "unknown")
    if waitSeconds:
        raise HTTPException(
            429,
            "Too many authentication attempts",
            headers={"Retry-After": str(int(waitSeconds) + 1)},
        )


def requireUser(
    request: Request,
    session: Session = Depends(getSession),
    csrfHeader: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> UserEntity:
    settings = request.app.state.settings
    rawToken = request.cookies.get(settings.sessionCookieName, "")
    if not rawToken or len(rawToken) > 128:
        raise HTTPException(401, "Login required")
    authSession = session.get(AuthSessionEntity, tokenDigest(rawToken))
    if authSession is None:
        raise HTTPException(401, "Session expired")
    expiresAt = authSession.expiresAt.replace(tzinfo=timezone.utc)
    if expiresAt <= currentTime():
        raise HTTPException(401, "Session expired")
    if request.method not in SAFE_METHODS:
        checkOrigin(request)
        if not hmac.compare_digest(csrfHeader or "", csrfToken(rawToken)):
            raise HTTPException(403, "Invalid CSRF token")
    user = session.get(UserEntity, authSession.userId)
    if user is None:
        raise HTTPException(401, "Login required")
    return user


class AuthService:
    @staticmethod
    def signup(session: Session, signup: SignupDto) -> UserEntity:
        user = UserEntity(
            email=signup.email,
            nickname=signup.nickname,
            passwordHash=PASSWORD_HASHER.hash(signup.password.get_secret_value()),
        )
        session.add(user)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            raise HTTPException(409, "Email is already registered") from None
        return user

    @staticmethod
    def login(session: Session, email: str, password: str) -> UserEntity:
        user = session.scalar(select(UserEntity).where(UserEntity.email == email))
        try:
            PASSWORD_HASHER.verify(user.passwordHash if user else DUMMY_PASSWORD_HASH, password)
        except (VerificationError, InvalidHashError):
            raise HTTPException(401, "Email or password is incorrect") from None
        if user is None:
            raise HTTPException(401, "Email or password is incorrect")
        if PASSWORD_HASHER.check_needs_rehash(user.passwordHash):
            user.passwordHash = PASSWORD_HASHER.hash(password)
        return user

    @staticmethod
    def createSession(
        session: Session, user: UserEntity, request: Request, response: Response
    ) -> AuthDto:
        settings = request.app.state.settings
        oldToken = request.cookies.get(settings.sessionCookieName)
        if oldToken:
            session.execute(
                delete(AuthSessionEntity).where(
                    AuthSessionEntity.tokenHash == tokenDigest(oldToken)
                )
            )
        session.execute(
            delete(AuthSessionEntity).where(AuthSessionEntity.expiresAt <= currentTime())
        )
        rawToken = secrets.token_urlsafe(32)
        lifetime = settings.sessionLifetimeHours * 3600
        session.add(
            AuthSessionEntity(
                tokenHash=tokenDigest(rawToken),
                userId=user.id,
                expiresAt=currentTime() + timedelta(seconds=lifetime),
            )
        )
        session.commit()
        response.set_cookie(
            settings.sessionCookieName,
            rawToken,
            httponly=True,
            secure=settings.cookieSecure,
            samesite="lax",
            max_age=lifetime,
            path="/",
        )
        response.headers["Cache-Control"] = "no-store"
        return AuthDto(user=UserDto.model_validate(user), csrfToken=csrfToken(rawToken))
