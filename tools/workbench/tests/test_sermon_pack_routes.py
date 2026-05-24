import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import pytest


@pytest.fixture
def client():
    import app as app_module
    from conftest import init_all_app_globals
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    init_all_app_globals(tmp.name)
    app_module.app.config['TESTING'] = True
    with app_module.app.test_client() as c:
        yield c
    os.unlink(tmp.name)


def _new_session(client, passage='Romans 3:25-26'):
    r = client.post('/study/session/new', data={'passage': passage})
    return int(r.headers['Location'].rstrip('/').split('/')[-1])


def test_pack_manuscript_md_returns_attachment(client):
    sid = _new_session(client)
    r = client.get(f'/study/session/{sid}/pack/manuscript.md')
    assert r.status_code == 200
    assert r.content_type.startswith('text/markdown') or r.content_type.startswith('text/plain')
    cd = r.headers['Content-Disposition']
    assert 'attachment' in cd
    assert 'manuscript' in cd
    assert b'\xe2\x80\x94' not in r.data  # no em-dash bytes


def test_pack_outline_md(client):
    sid = _new_session(client)
    r = client.get(f'/study/session/{sid}/pack/outline.md')
    assert r.status_code == 200
    assert b'Preaching Outline' in r.data


def test_pack_handout_md(client):
    sid = _new_session(client)
    r = client.get(f'/study/session/{sid}/pack/handout.md')
    assert r.status_code == 200
    assert b'<!--PAGEBREAK-->' in r.data


def test_pack_handout_html_uses_tight_print_profile(client):
    sid = _new_session(client)
    r = client.get(f'/study/session/{sid}/pack/handout.md?format=html')
    assert r.status_code == 200
    assert r.content_type.startswith('text/html')
    assert b'<!DOCTYPE html>' in r.data
    # tight-print profile sets 10.5pt body font; default is 12pt
    assert b'10.5pt' in r.data


def test_pack_bad_artifact_returns_400(client):
    sid = _new_session(client)
    r = client.get(f'/study/session/{sid}/pack/nonsense.md')
    assert r.status_code == 404 or r.status_code == 400


def test_pack_missing_session_returns_404(client):
    r = client.get('/study/session/999999/pack/manuscript.md')
    assert r.status_code == 404
