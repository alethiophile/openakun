from context import pages
import unittest

class LoginTest(unittest.TestCase):
    def setUp(self):
        pages.app.testing = True
        self.client = pages.app.test_client()
        pages.init_db()
        s = pages.Session()
        s.add(pages.create_user('admin', 'placeholder@example.com', 'test'))
        s.commit()

    def test_login(self):
        rv = self.client.get('/')
        self.assertIn(b'Log in', rv.data)
        rv = self.client.post('/login',
                              data={ 'user': 'admin', 'pass': 'test' },
                              follow_redirects=True)
        self.assertIn(b'Welcome, admin', rv.data)

    def tearDown(self):
        pass