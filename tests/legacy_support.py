"""Keep PR regression tests focused on existing behavior; auth has separate integration tests."""

from fastapi import Depends
from sqlalchemy.orm import Session
from storage_support import createStorageApp

from neareast_api.core.config import Settings
from neareast_api.db.entities import UserEntity
from neareast_api.db.session import CONTENT_SESSION, getSession
from neareast_api.modules.auth.service import requireUser


def create_app(settings=None, *, seed=True):
    app = createStorageApp(settings or Settings.from_env(), seed=seed)

    def testUser(session: Session = Depends(getSession)):
        return UserEntity(id="legacy-test-user", email="legacy@example.com", nickname="Test")

    app.dependency_overrides[requireUser] = testUser
    return app


def setUpModule():
    global fixtureApp, fixtureSession, contextToken
    fixtureApp = createStorageApp()
    fixtureSession = fixtureApp.state.sessionFactory()
    contextToken = CONTENT_SESSION.set(fixtureSession)


def tearDownModule():
    CONTENT_SESSION.reset(contextToken)
    fixtureSession.close()
    fixtureApp.state.databaseEngine.dispose()
