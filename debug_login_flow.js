const { spawn } = require('child_process');
const http = require('http');
const fs = require('fs');
const WS = require('ws');

async function debugLogin(landline, password) {
    const port = 9966;
    const chrome = spawn('/usr/bin/google-chrome-stable', [
        '--headless=new',
        '--remote-debugging-port=' + port,
        '--no-sandbox',
        '--disable-dev-shm-usage',
        '--window-size=1280,800',
        '--lang=en-US'
    ]);

    await new Promise(r => setTimeout(r, 2000));
    const targets = await new Promise((res, rej) => {
        http.get('http://127.0.0.1:' + port + '/json', r => {
            let d = ''; r.on('data', c => d += c); r.on('end', () => res(JSON.parse(d)));
        }).on('error', rej);
    });
    const target = targets.find(t => t.type === 'page') || targets[0];
    const ws = new WS(target.webSocketDebuggerUrl);
    await new Promise(r => ws.onopen = r);

    let id = 1;
    function send(method, params = {}) {
        return new Promise(r => {
            const reqId = id++;
            function h(msg) {
                const data = JSON.parse(msg);
                if (data.id === reqId) { ws.off('message', h); r(data); }
            }
            ws.on('message', h);
            ws.send(JSON.stringify({ id: reqId, method, params }));
        });
    }

    const val = (res) => res.result?.result?.value !== undefined ? res.result.result.value : res.result?.value;

    ws.on('message', async (raw) => {
        const msg = JSON.parse(raw);
        if (msg.method === 'Network.responseReceived') {
            const u = msg.params.response.url;
            if (u.includes('userAuthenticate') || u.includes('freeUnit') || u.includes('queryBalance') || u.includes('Captcha') || u.includes('error')) {
                console.log('>>> [NETWORK EVENT]:', msg.params.response.status, u);
                try {
                    const bodyRes = await send('Network.getResponseBody', { requestId: msg.params.requestId });
                    console.log('>>> [BODY]:', (bodyRes.result?.body || '').slice(0, 300));
                } catch(e) {}
            }
        }
    });

    await send('Network.enable');
    await send('Page.enable');
    await send('Page.addScriptToEvaluateOnNewDocument', {
        source: `
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'languages', { get: () => ['ar-EG', 'ar', 'en-US', 'en'] });
            window.chrome = { runtime: {} };
        `
    });

    console.log('1. Navigating to login...');
    await send('Page.navigate', { url: 'https://my.te.eg/echannel/#/login' });

    let mounted = false;
    for (let i = 0; i < 40; i++) {
        const chk = await send('Runtime.evaluate', {
            expression: `!!(document.querySelector('input[placeholder*="Service"]') || document.querySelector('input[placeholder*="الخدمة"]') || document.querySelector('input.ant-input:not([type="password"])'))`,
            returnByValue: true
        });
        if (val(chk)) { mounted = true; break; }
        await new Promise(r => setTimeout(r, 500));
    }
    console.log('2. Form mounted:', mounted);

    // Type landline
    await send('Runtime.evaluate', {
        expression: `(() => { const el = document.querySelector('input[placeholder*="Service"]') || document.querySelector('input[placeholder*="الخدمة"]') || document.querySelector('input.ant-input:not([type="password"])'); if (el) { el.focus(); el.click(); } })()`
    });
    for (const c of landline) {
        await send('Input.dispatchKeyEvent', { type: 'keyDown', text: c });
        await send('Input.dispatchKeyEvent', { type: 'keyUp' });
        await new Promise(r => setTimeout(r, 20));
    }
    await send('Runtime.evaluate', {
        expression: `(() => { const el = document.querySelector('input[placeholder*="Service"]') || document.querySelector('input[placeholder*="الخدمة"]') || document.querySelector('input.ant-input:not([type="password"])'); if (el) { el.dispatchEvent(new Event('input', { bubbles: true })); el.dispatchEvent(new Event('change', { bubbles: true })); el.blur(); } })()`
    });
    await new Promise(r => setTimeout(r, 800));

    // Select Internet
    const hasSelect = await send('Runtime.evaluate', {
        expression: `!!(document.querySelector('.ant-select-selector') || document.querySelector('.ant-select'))`,
        returnByValue: true
    });
    if (val(hasSelect)) {
        console.log('3. Selecting Internet...');
        await send('Runtime.evaluate', {
            expression: `(() => { const sel = document.querySelector('.ant-select-selector') || document.querySelector('.ant-select'); if (sel) { sel.dispatchEvent(new MouseEvent('mousedown', { bubbles: true })); sel.click(); } })()`
        });
        await new Promise(r => setTimeout(r, 600));
        await send('Runtime.evaluate', {
            expression: `(() => { const opts = Array.from(document.querySelectorAll('.ant-select-item-option, [role="option"]')); const o = opts.find(x => x.innerText.toLowerCase().includes('internet') || x.innerText.includes('إنترنت')); if (o) { o.dispatchEvent(new MouseEvent('mousedown', { bubbles: true })); o.click(); } })()`
        });
        await new Promise(r => setTimeout(r, 800));
    }

    // Type password
    console.log('4. Typing password...');
    await send('Runtime.evaluate', {
        expression: `(() => { const p = document.querySelector('input[type="password"]'); if (p) { p.focus(); p.click(); } })()`
    });
    for (const c of password) {
        await send('Input.dispatchKeyEvent', { type: 'keyDown', text: c });
        await send('Input.dispatchKeyEvent', { type: 'keyUp' });
        await new Promise(r => setTimeout(r, 20));
    }
    await send('Runtime.evaluate', {
        expression: `(() => { const p = document.querySelector('input[type="password"]'); if (p) { p.dispatchEvent(new Event('input', { bubbles: true })); p.dispatchEvent(new Event('change', { bubbles: true })); p.blur(); } })()`
    });
    await new Promise(r => setTimeout(r, 800));

    // Click Login
    const btnState = await send('Runtime.evaluate', {
        expression: `(() => { const btn = Array.from(document.querySelectorAll('button')).find(b => b.innerText.includes('Login') || b.innerText.includes('تسجيل الدخول') || b.type === 'submit'); return { exists: !!btn, disabled: btn ? btn.disabled : true, text: btn ? btn.innerText.trim() : '' }; })()`,
        returnByValue: true
    });
    console.log('5. Login button state:', JSON.stringify(val(btnState)));

    await send('Runtime.evaluate', {
        expression: `(() => { const btn = Array.from(document.querySelectorAll('button')).find(b => b.innerText.includes('Login') || b.innerText.includes('تسجيل الدخول') || b.type === 'submit'); if (btn && !btn.disabled) btn.click(); })()`
    });
    console.log('6. Login button clicked!');

    // Wait and check what happens
    for (let i = 0; i < 20; i++) {
        await new Promise(r => setTimeout(r, 1000));
        const st = await send('Runtime.evaluate', {
            expression: `(() => { 
                const m = document.querySelector('.ant-modal-content');
                const alert = document.querySelector('.ant-message-notice, .ant-alert, [role="alert"]');
                return {
                    url: window.location.href,
                    hasModal: !!m,
                    modalText: m ? m.innerText.trim().slice(0, 100) : '',
                    alertText: alert ? alert.innerText.trim() : ''
                };
            })()`,
            returnByValue: true
        });
        const info = val(st);
        console.log(`[T+${i+1}s] URL: ${info.url} | Modal: ${info.hasModal} (${info.modalText}) | Alert: ${info.alertText}`);
        if (!info.url.includes('/login')) {
            console.log('>>> REACHED DASHBOARD! Current URL:', info.url);
            break;
        }
    }

    try { chrome.kill(); } catch(e) {}
    process.exit(0);
}

debugLogin(process.argv[2], process.argv[3]);
