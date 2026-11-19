import unittest

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from storage_support import createStorageApp

from neareast_api.db.entities import AuthSessionEntity, ConversationEntity, UserEntity


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.app = createStorageApp()
        self.client = TestClient(self.app)
        self.otherClient = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.addCleanup(self.otherClient.close)
        self.addCleanup(self.app.state.databaseEngine.dispose)
        response = self.client.post('/api/v1/auth/signup', json={
            'email': 'account@example.com', 'nickname': '여행자', 'password': 'Original123!',
        })
        self.client.headers['X-CSRF-Token'] = response.json()['csrfToken']

    def test_profile_contract_and_csrf(self):
        response = self.client.patch('/api/v1/users/me', json={'nickname': '새여행자'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['nickname'], '새여행자')
        self.assertEqual(response.json()['email'], 'account@example.com')
        self.assertNotIn('passwordHash', response.json())
        self.client.headers.pop('X-CSRF-Token')
        self.assertEqual(self.client.patch('/api/v1/users/me', json={'nickname': '변경'}).status_code, 403)

    def test_password_change_revokes_other_sessions(self):
        self.otherClient.post('/api/v1/auth/login', json={
            'email': 'account@example.com', 'password': 'Original123!',
        })
        path = '/api/v1/users/me/password'
        self.assertEqual(self.client.put(path, json={
            'currentPassword': 'WrongPass123!', 'newPassword': 'Updated123!',
        }).status_code, 403)
        self.assertEqual(self.client.put(path, json={
            'currentPassword': 'Original123!', 'newPassword': 'Updated123!',
        }).status_code, 204)
        self.assertEqual(self.otherClient.get('/api/v1/users/me').status_code, 401)
        self.assertEqual(self.client.get('/api/v1/users/me').status_code, 200)
        self.assertEqual(self.otherClient.post('/api/v1/auth/login', json={
            'email': 'account@example.com', 'password': 'Original123!',
        }).status_code, 401)
        self.assertEqual(self.otherClient.post('/api/v1/auth/login', json={
            'email': 'account@example.com', 'password': 'Updated123!',
        }).status_code, 200)

    def test_account_delete_cascades_own_content(self):
        self.client.post('/api/v1/conversations', json={'title': 'Delete fixture'})
        self.assertEqual(self.client.delete('/api/v1/users/me').status_code, 204)
        self.assertEqual(self.client.get('/api/v1/users/me').status_code, 401)
        with self.app.state.sessionFactory() as session:
            for entity in (UserEntity, AuthSessionEntity, ConversationEntity):
                self.assertEqual(session.scalar(select(func.count()).select_from(entity)), 0)
