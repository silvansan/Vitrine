# Regenerates the pulpit screenshots for the guest "tablet tips" page (server/guestmedia/static/guestmedia/help/).
# Needs a running agent with the fake vMix, an API key in /home/claude/e2e/key.txt and Playwright; the crops are made by hand afterwards.
import json, time, urllib.request
from playwright.sync_api import sync_playwright
A = 'http://127.0.0.1:8090'
KEY = open('/home/claude/e2e/key.txt').read().strip()
def post(path, body=None, key=False):
    h = {'Content-Type': 'application/json'}
    if key: h['Authorization'] = 'Bearer ' + KEY
    req = urllib.request.Request(A + path, data=json.dumps(body or {}).encode(), headers=h, method='POST')
    return json.load(urllib.request.urlopen(req))
def get(path):
    req = urllib.request.Request(A + path, headers={'Authorization': 'Bearer ' + KEY})
    return json.load(urllib.request.urlopen(req))

post('/api/clear', key=True)
boot = get('/api/bootstrap')
maria = [s for s in boot['sets'] if s['guest'] == 'Maria'][0]
video = [i for i in maria['items'] if boot['assets'].get(str(i), {}).get('kind') == 'video'][0]
vfile = [i for i in maria['items'] if str(i) in boot['assets']].index(video) + 1
if not boot['assets'][str(video)]['markers']:
    post('/api/markers', {'asset_id': video, 't_ms': 6000, 'label': 'Song'}, key=True)
    post('/api/markers', {'asset_id': video, 't_ms': 15000, 'label': 'Testimony'}, key=True)
pair = post('/api/companion/pair', {'role': 'pulpit', 'name': 'Pulpit (help shots)'})

with sync_playwright() as p:
    b = p.chromium.launch(executable_path='/usr/bin/google-chrome', args=['--no-sandbox'])
    pg = b.new_context(viewport={'width': 1180, 'height': 820}, device_scale_factor=1).new_page()
    pg.goto(pair['url']); pg.wait_for_selector('.foot')
    me = get('/api/bootstrap')['screens']
    sid = [s for s in me if s['name'] == 'Pulpit (help shots)'][-1]['id']
    scr = [s for s in me if s['id'] == sid][0]
    opts = dict(scr['options']); opts['show_status'] = True
    post('/api/screens/' + sid, {'current_set_id': maria['id'], 'options': opts}, key=True)
    pg.wait_for_selector('.tile'); time.sleep(1.2)
    pg.screenshot(path='1-idle.png')
    pg.click('.tile >> nth=0 >> .thumb'); pg.wait_for_timeout(120)
    pg.screenshot(path='2-sending.png')
    pg.wait_for_selector('.tile.live', timeout=8000); pg.wait_for_timeout(500)
    pg.screenshot(path='3-live-photo.png')
    pg.click('.tile.live .thumb'); pg.wait_for_timeout(1500)
    pg.click('.tile >> nth=%d >> .thumb' % (vfile - 1)); pg.wait_for_selector('.transport', timeout=8000); pg.wait_for_timeout(4000)
    pg.screenshot(path='4-live-video.png')
    pg.click('[data-act="clear"]'); pg.wait_for_timeout(2000)
    pg.screenshot(path='5-resume.png')
    pg.click('[data-act="cue"][data-id="%d"]' % video); pg.wait_for_selector('.sheet'); pg.wait_for_timeout(800)
    pg.screenshot(path='6-cue.png')
    pg.click('[data-act="cue-close"]')
    b.close()
post('/api/companion/screens/' + sid + '/delete')
print('ok', vfile)
