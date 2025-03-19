import pytest, re

async def get_csrf(response) -> str:
    csrf_token = re.search(r'"_csrf_token"[^>]+?"([^"]+)"',
                           await response.get_data(True)).group(1)
    if csrf_token is not None: return csrf_token

async def do_login(client, user: str, pwd: str):
    lresp = await client.get('/login')
    csrf_token = await get_csrf(lresp)
    data = {
        'user': user, 'pass': pwd,
        '_csrf_token': csrf_token
    }
    lresp = await client.post('/login', form=data)
    return lresp

async def test_main(openakun_app):
    client = openakun_app.test_client()
    resp = await client.get('/')
    assert resp.status_code == 200

async def test_login_logout(openakun_app):
    client = openakun_app.test_client()
    resp = await client.get('/')
    assert resp.status_code == 200

    assert re.search(r"<a[^>]*>Log in</a>", await resp.get_data(True))

    lresp = await client.get('/login')
    csrf_token = re.search(r'name="_csrf_token" value="([^"]+)"',
                           await lresp.get_data(True)).group(1)
    data = {
        'user': 'admin', 'pass': 'password',
        '_csrf_token': csrf_token
    }
    lresp = await client.post('/login', form=data)
    assert lresp.status_code == 302

    lresp = await client.get('/')
    assert lresp.status_code == 200
    d = await lresp.get_data(True)
    assert 'Welcome, admin' in d
    assert re.search(r"<a[^>]*>Log out</a>", d)

    tok = await get_csrf(lresp)
    data = { '_csrf_token': tok }
    r = await client.post('/logout', form=data)
    assert r.status_code == 302
    l = r.headers['Location']

    r = await client.get(l)
    assert re.search(r"<a[^>]*>Log in</a>", await r.get_data(True))

async def test_bad_password(openakun_app):
    client = openakun_app.test_client()

    r = await do_login(client, 'admin', 'wrong_password')
    assert r.status_code == 302
    l = r.headers['Location']
    assert l.startswith('/login')

    r = await client.get(l)
    assert 'Login failed' in await r.get_data(True)

async def test_bad_csrf(openakun_app):
    client = openakun_app.test_client()
    r = await client.get('/login')
    data = { 'user': 'admin', 'pass': 'password' }
    r = await client.post('/login', form=data)
    assert r.status_code == 400

async def test_login_required(openakun_app):
    client = openakun_app.test_client()

    r = await client.get('/new_story')
    assert r.status_code == 302
    l = r.headers['Location']
    assert l.startswith('/login')

async def test_register(openakun_app):
    client = openakun_app.test_client()
    r = await client.get('/signup')
    tok = await get_csrf(r)

    r = await client.post('/signup', form={
        '_csrf_token': tok, 'user': 'newuser',
        'email': '', 'pass1': 'newpassword', 'pass2': 'newpassword'
    })
    assert r.status_code == 302
    l = r.headers['Location']
    assert l == '/'
    r = await client.get(l)
    d = await r.get_data(True)
    assert 'Welcome, newuser' in d

async def test_register_existing(openakun_app):
    client = openakun_app.test_client()
    r = await client.get('/signup')
    tok = await get_csrf(r)

    r = await client.post('/signup', form={
        '_csrf_token': tok, 'user': 'admin',
        'email': '', 'pass1': 'np', 'pass2': 'np'
    })
    assert r.status_code == 302
    l = r.headers['Location']
    assert l == '/signup'

    r = await client.get(l)
    assert 'Username not available' in await r.get_data(True)

async def test_register_mismatch(openakun_app):
    client = openakun_app.test_client()
    r = await client.get('/signup')
    tok = await get_csrf(r)

    r = await client.post('/signup', form={
        '_csrf_token': tok, 'user': 'newuser2',
        'email': '', 'pass1': 'password', 'pass2': 'different_password'
    })
    assert r.status_code == 302
    l = r.headers['Location']
    assert l == '/signup'

    r = await client.get(l)
    assert 'Passwords did not match' in await r.get_data(True)

async def test_story_post(openakun_app):
    client = openakun_app.test_client()
    await do_login(client, "admin", "password")

    r = await client.get('/new_story')
    tok = await get_csrf(r)
    desc = 'this is the test story description'
    r = await client.post('/new_story', form={
        'title': 'test story',
        'description': desc,
        '_csrf_token': tok
    })
    assert r.status_code == 302
    story_url = r.headers['Location']

    r = await client.get(story_url)
    assert r.status_code == 302
    chapter_url = r.headers['Location']

    r = await client.get(chapter_url)
    d = await r.get_data(True)
    assert desc in d

# class LoginTest(OpenakunTestCase):
#     def test_login(self):
#         rv = self.client.get('/')
#         self.assertIn(b'Log in', rv.data)
#         rv = self.login('admin', 'test')
#         self.assertIn(b'Welcome, admin', rv.data)

#     def test_logout(self):
#         rv = self.logout()
#         self.assertIn(b'Log in', rv.data)

# class PostStoryTest(OpenakunTestCase):
#     def test_post(self):
#         rv = self.client.get('/new_story', follow_redirects=True)
#         self.assertIn(b'Please log in to access this page', rv.data)
#         self.login('admin', 'test')
#         csrf = self.get_csrf()
#         rv = self.client.post('/new_story',
#                               data={ 'title': 'test title',
#                                      'description': 'test description',
#                                      '_csrf_token': csrf },
#                               follow_redirects=True)
#         self.assertIn(b'<h2>test title</h2>', rv.data)
#         self.assertRegex(rv.data.decode(),
#                          r'<div class="description">\s*test description\s*</div>')  # noqa: E501

# test_no_html = [
#     ('This contains no HTML', 'This contains no HTML'),
#     ('This "contains" double quotes', 'This "contains" double quotes'),
#     ('This uses the symbol (x < y)', 'This uses the symbol (x &lt; y)'),
#     ('Tries <script>alert("injection")</script>',
#      'Tries &lt;script&gt;alert("injection")&lt;/script&gt;'),
# ]

# test_clean_html = [
#     ('This contains no HTML', 'This contains no HTML'),
#     ('This "contains" double quotes', 'This "contains" double quotes'),
#     ('This uses the symbol (x < y)', 'This uses the symbol (x &lt; y)'),
#     ('This is <b>rich</b> text', 'This is <b>rich</b> text'),
#     ('This is <strong>rich</strong> text',
#      'This is <strong>rich</strong> text'),
#     ('<p>This is a paragraph</p>', '<p>This is a paragraph</p>'),
#     ('This has <i>italics</i>', 'This has <i>italics</i>'),
#     ('This has <em>italics</em>', 'This has <em>italics</em>'),
# ]

# class CleanHTMLTest(unittest.TestCase):
#     def test_chapter_cleaner(self):
#         for b, a in test_no_html + test_clean_html:
#             with self.subTest(case=b):
#                 h = pages.ChapterHTMLText(b)
#                 self.assertEqual(h.dirty_html, b)
#                 self.assertEqual(h.clean_html, a)

# # Strings like story title don't support any formatting; all HTML is escaped
# # (this just happens automatically on the Jinja2 level)
# class TitleXSSTest(OpenakunTestCase):
#     xss_strings = { '': '' }

#     def test_title_xss(self):
#         pass
