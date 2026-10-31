const { spawn } = require('child_process');
const http = require('http');
const WS = require('ws');

async function test(landline, password) {
    const port = 9988;
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

    ws.on('message', async (raw) => {
        const msg = JSON.parse(raw);
        if (msg.method === 'Network.responseReceived') {
            const u = msg.params.response.url;
            if (u.includes('userAuthenticate') || u.includes('freeUnit') || u.includes('Balance') || u.includes('query')) {
                console.log('>>> [NETWORK RESPONSE]:', msg.params.response.status, u);
                try {
                    const bodyRes = await send('Network.getResponseBody', { requestId: msg.params.requestId });
                    console.log('>>> [BODY SNIPPET]:', (bodyRes.result?.body || '').slice(0, 300));
                } catch(e) {}
            }
        }
    });

    await send('Network.enable');
    await send('Page.enable');
    await send('Page.navigate', { url: 'https://my.te.eg/echannel/#/login' });
    await new Promise(r => setTimeout(r, 5000));

    // Type landline
    await send('Runtime.evaluate', {
        expression: `(() => { const el = document.querySelector('input[placeholder*="Service"], input[placeholder*="الخدمة"], input.ant-input:not([type="password"])'); if (el) { el.focus(); el.click(); } })()`
    });
    for (const c of landline) {
        await send('Input.dispatchKeyEvent', { type: 'keyDown', text: c });
        await send('Input.dispatchKeyEvent', { type: 'keyUp' });
    }
    await send('Runtime.evaluate', {
        expression: `(() => { const el = document.querySelector('input[placeholder*="Service"], input[placeholder*="الخدمة"], input.ant-input:not([type="password"])'); if (el) { el.dispatchEvent(new Event('input', { bubbles: true })); el.dispatchEvent(new Event('change', { bubbles: true })); el.blur(); } })()`
    });
    await new Promise(r => setTimeout(r, 800));

    // Select Internet
    await send('Runtime.evaluate', {
        expression: `(() => { const sel = document.querySelector('.ant-select-selector, .ant-select'); if (sel) { sel.dispatchEvent(new MouseEvent('mousedown', { bubbles: true })); sel.click(); } })()`
    });
    await new Promise(r => setTimeout(r, 600));
    await send('Runtime.evaluate', {
        expression: `(() => { const opts = Array.from(document.querySelectorAll('.ant-select-item-option, [role="option"]')); const o = opts.find(x => x.innerText.toLowerCase().includes('internet') || x.innerText.includes('إنترنت')); if (o) { o.dispatchEvent(new MouseEvent('mousedown', { bubbles: true })); o.click(); } })()`
    });
    await new Promise(r => setTimeout(r, 800));

    // Type password
    await send('Runtime.evaluate', {
        expression: `(() => { const p = document.querySelector('input[type="password"]'); if (p) { p.focus(); p.click(); } })()`
    });
    for (const c of password) {
        await send('Input.dispatchKeyEvent', { type: 'keyDown', text: c });
        await send('Input.dispatchKeyEvent', { type: 'keyUp' });
    }
    await send('Runtime.evaluate', {
        expression: `(() => { const p = document.querySelector('input[type="password"]'); if (p) { p.dispatchEvent(new Event('input', { bubbles: true })); p.dispatchEvent(new Event('change', { bubbles: true })); p.blur(); } })()`
    });
    await new Promise(r => setTimeout(r, 800));

    // Check login button state
    const btnCheck = await send('Runtime.evaluate', {
        expression: `(() => { const btn = Array.from(document.querySelectorAll('button')).find(b => b.innerText.includes('Login') || b.innerText.includes('تسجيل الدخول') || b.type === 'submit'); return { exists: !!btn, disabled: btn ? btn.disabled : null, text: btn ? btn.innerText.trim() : null }; })()`,
        returnByValue: true
    });
    console.log('>>> [LOGIN BUTTON]:', JSON.stringify(btnCheck.result?.value));

    // Click login
    await send('Runtime.evaluate', {
        expression: `(() => { const btn = Array.from(document.querySelectorAll('button')).find(b => b.innerText.includes('Login') || b.innerText.includes('تسجيل الدخول') || b.type === 'submit'); if (btn && !btn.disabled) btn.click(); })()`
    });

    // Wait and check what happens for 15s
    for (let i = 0; i < 15; i++) {
        await new Promise(r => setTimeout(r, 1000));
        const status = await send('Runtime.evaluate', {
            expression: `(() => { return { url: window.location.href, modal: !!document.querySelector('.ant-modal-content'), alert: document.querySelector('.ant-message-notice, .ant-alert, [role="alert"]')?.innerText?.trim() }; })()`,
            returnByValue: true
        });
        console.log(`>>> [T+${i+1}s]:`, JSON.stringify(status.result?.value));
    }

    try { chrome.kill(); } catch(e) {}
    process.exit(0);
}

test(process.argv[2], process.argv[3]);
