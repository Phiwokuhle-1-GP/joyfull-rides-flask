import re, sqlite3
import pytest
from werkzeug.security import generate_password_hash
from app import create_app

@pytest.fixture
def site(tmp_path):
    path=str(tmp_path/'analytics.db')
    conn=sqlite3.connect(path); conn.execute('CREATE TABLE visits(id INTEGER PRIMARY KEY, path TEXT)'); conn.execute("INSERT INTO visits(path) VALUES('/legacy')"); conn.commit(); conn.close()
    app=create_app({'TESTING':True,'DATABASE':path,'SECRET_KEY':'test-secret-'*5})
    conn=sqlite3.connect(path); conn.execute('INSERT INTO owners(username,password_hash) VALUES(?,?)',('owner',generate_password_hash('A-test-password-12345'))); conn.commit(); conn.close()
    return app,app.test_client(),path

def token(c):
    with c.session_transaction() as s: return s['csrf']

def login(c):
    c.get('/login'); return c.post('/login',data={'csrf_token':token(c),'username':'owner','password':'A-test-password-12345'})

def enquiry(c,kind='quote',**extra):
    c.get('/enquiry/'+kind)
    with c.session_transaction() as s: sid=s['submission_id']
    data={'csrf_token':token(c),'submission_id':sid,'name':'Test Parent','email':'parent@example.com','phone':'','school':'School','pickup':'Linden','dropoff':'Randburg','message':'Please reply','consent':'yes'}
    data.update(extra); return data

def test_quote_owner_and_campaign(site):
    app,c,path=site
    c.get('/?utm_source=facebook&utm_medium=social&utm_campaign=school',headers={'User-Agent':'Mozilla/5.0'})
    data=enquiry(c); assert c.post('/enquiry/quote',data=data).status_code==303
    assert c.post('/enquiry/quote',data=data).status_code==302
    assert c.get('/admin/enquiries').status_code==302
    assert login(c).status_code==302
    assert b'Test Parent' in c.get('/admin/enquiries').data
    assert c.post('/admin/enquiries/1/status',data={'csrf_token':token(c),'status':'won'}).status_code==303
    r=c.get('/admin/analytics'); assert r.status_code==200; assert b'100.0%' in r.data and b'facebook' in r.data
    conn=sqlite3.connect(path); assert conn.execute('SELECT count(*) FROM enquiries').fetchone()[0]==1
    assert conn.execute('SELECT path FROM visits').fetchone()[0]=='/legacy'
    assert conn.execute('SELECT status FROM enquiries').fetchone()[0]=='won'
    assert c.post('/logout',data={'csrf_token':token(c)}).status_code==302
    assert c.get('/admin/enquiries').status_code==302

def test_validation_csrf_contact(site):
    _,c,path=site
    assert c.post('/enquiry/quote',data={}).status_code==400
    data=enquiry(c,email='',phone='',school=''); assert c.post('/enquiry/quote',data=data).status_code==422
    data=enquiry(c,'contact'); assert c.post('/enquiry/contact',data=data).status_code==303
    data=enquiry(c,'contact',email='<bad>',message=''); assert c.post('/enquiry/contact',data=data).status_code==422

def test_analytics_exclusions_and_templates(site):
    _,c,path=site
    for url in ['/','/blog','/blog/safe-school-transport-checklist']:
        assert c.get(url,headers={'User-Agent':'Mozilla/5.0'}).status_code==200
    c.get('/',headers={'User-Agent':'Googlebot'}); c.get('/missing',headers={'User-Agent':'Mozilla/5.0'}); c.get('/login')
    conn=sqlite3.connect(path); assert conn.execute('SELECT count(*) FROM page_views').fetchone()[0]==3
    assert conn.execute('SELECT count(*) FROM request_errors').fetchone()[0]==1
    html=c.get('/').data
    assert b'27XXXXXXXXX' not in html and b'R___' not in html and b'quoteModal' not in html

def test_login_limits_and_csrf(site):
    _,c,_=site
    c.get('/login')
    for i in range(5): assert c.post('/login',data={'csrf_token':token(c),'username':'owner','password':'wrong'}).status_code==401
    assert login(c).status_code==429

def test_owner_hash_and_production_secret(site,monkeypatch,tmp_path):
    app,c,path=site
    result=app.test_cli_runner().invoke(args=['create-owner','--username','second','--password','short'])
    assert result.exit_code!=0
    assert login(c).status_code==302
    assert c.post('/admin/enquiries/1/status',data={'status':'won'}).status_code==400
    monkeypatch.setenv('APP_ENV','production'); monkeypatch.delenv('SECRET_KEY',raising=False); monkeypatch.setenv('DATA_DIR',str(tmp_path))
    with pytest.raises(RuntimeError): create_app()
