const { spawn, execSync } = require('child_process');
const http = require('http');
const os = require('os');
const path = require('path');
const fs = require('fs');

const WS = globalThis.WebSocket || require('ws');

function getChromePath() {
    if (process.platform === 'win32') {
        return 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
    }
    const candidates = [
        '/usr/bin/google-chrome-stable',
        '/usr/bin/google-chrome',
        '/usr/bin/chromium-browser',
        '/usr/bin/chromium'
    ];
    for (const p of candidates) {
        if (fs.existsSync(p)) return p;
    }
    return 'google-chrome-stable';
}

async function fetchQuota(landlineNumber, password) {
    const chromePath = getChromePath();
    const profileDir = path.join(os.tmpdir(), `we_chrome_${Date.now()}_${Math.floor(Math.random()*1000)}`);
    const port = 9222 + Math.floor(Math.random() * 500);

    const chrome = spawn(chromePath, [
        '--headless=new',
        `--remote-debugging-port=${port}`,
        `--user-data-dir=${profileDir}`,
        '--no-first-run',
        '--no-default-browser-check',
        '--no-sandbox',
        '--disable-dev-shm-usage',
        '--disable-gpu',
        '--disable-extensions'
    ]);

    let quotaResult = null;
    let balanceResult = null;
    let pointsResult = null;
    let customerNameResult = null;
    let authError = null;

    try {
        await new Promise(r => setTimeout(r, 2000));

        const targets = await new Promise((resolve, reject) => {
            const req = http.get(`http://127.0.0.1:${port}/json`, (res) => {
                let data = '';
                res.on('data', chunk => data += chunk);
                res.on('end', () => resolve(JSON.parse(data)));
            });
            req.on('error', reject);
        });

        const target = targets.find(t => t.type === 'page' && !t.url.startsWith('chrome-extension://')) || targets[0];
        const ws = new WS(target.webSocketDebuggerUrl);
        let id = 1;
        const callbacks = new Map();

        ws.onmessage = async (event) => {
            const msg = JSON.parse(event.data);
            if (msg.id && callbacks.has(msg.id)) {
                callbacks.get(msg.id)(msg);
                callbacks.delete(msg.id);
            }

            if (msg.method === 'Network.responseReceived') {
                const res = msg.params.response;
                const u = res.url;

                if (u.includes('/cz/cbs/bb/queryFreeUnit')) {
                    setTimeout(async () => {
                        try {
                            const bodyRes = await send('Network.getResponseBody', { requestId: msg.params.requestId });
                            const data = JSON.parse(bodyRes.result?.body || '{}');
                            if (data.header?.retCode === '0' && data.body) {
                                quotaResult = data.body;
                            }
                        } catch(e) {}
                    }, 250);
                } else if (u.includes('/cbs/ar/queryBalance')) {
                    setTimeout(async () => {
                        try {
                            const bodyRes = await send('Network.getResponseBody', { requestId: msg.params.requestId });
                            const data = JSON.parse(bodyRes.result?.body || '{}');
                            if (data.body?.balanceInfo && data.body.balanceInfo[0]) {
                                const rawAmount = parseFloat(data.body.balanceInfo[0].totalAmount || 0);
                                balanceResult = rawAmount > 10000 ? Math.round((rawAmount / 10000.0) * 100) / 100 : rawAmount;
                            }
                        } catch(e) {}
                    }, 250);
                } else if (u.includes('LoyaltyPoints') || u.includes('getLoyalty') || u.includes('queryLoyalty')) {
                    setTimeout(async () => {
                        try {
                            const bodyRes = await send('Network.getResponseBody', { requestId: msg.params.requestId });
                            const data = JSON.parse(bodyRes.result?.body || '{}');
                            const lp = data.body?.loyaltyPointsInfo;
                            const pts = lp?.freeAmount ?? lp?.initialTotalAmount ?? data.body?.points ?? data.body?.totalPoints ?? data.body?.loyaltyPoints;
                            if (pts !== undefined && pts !== null) {
                                pointsResult = Math.round(parseFloat(pts));
                            }
                        } catch(e) {}
                    }, 250);
                } else if (u.includes('/cbs/bb/querySubscribers') || u.includes('/auth/queryCustomer')) {
                    setTimeout(async () => {
                        try {
                            const bodyRes = await send('Network.getResponseBody', { requestId: msg.params.requestId });
                            const data = JSON.parse(bodyRes.result?.body || '{}');
                            const name = data.body?.customerName || data.body?.name || (data.body?.subscriberInfoList && data.body.subscriberInfoList[0]?.subscriberName);
                            if (name) customerNameResult = name;
                        } catch(e) {}
                    }, 250);
                } else if (u.includes('/auth/userAuthenticate')) {
                    setTimeout(async () => {
                        try {
                            const bodyRes = await send('Network.getResponseBody', { requestId: msg.params.requestId });
                            const data = JSON.parse(bodyRes.result?.body || '{}');
                            if (data.header?.retCode !== '0') {
                                const err = data.header?.errorMsg || data.header?.description;
                                if (err && err.includes('attempts')) {
                                    authError = 'تم تجاوز الحد الأقصى للمحاولات. يرجى الانتظار لمدة ساعة والمحاولة مجدداً.';
                                } else {
                                    authError = 'خطأ في بيانات الحساب: رقم الهاتف أو كلمة المرور غير صحيحة';
                                }
                            } else {
                                const c = data.body?.customer;
                                const cName = c?.custName || c?.customerName || c?.firstName || c?.fullName;
                                if (cName && (!customerNameResult || customerNameResult === 'مشترك وي' || customerNameResult === 'مشترك الخط الأرضي')) {
                                    customerNameResult = cName.trim();
                                }
                            }
                        } catch(e) {}
                    }, 250);
                }
            }
        };

        function send(method, params = {}) {
            return new Promise((resolve) => {
                const reqId = id++;
                callbacks.set(reqId, resolve);
                ws.send(JSON.stringify({ id: reqId, method, params }));
            });
        }

        await new Promise(r => ws.onopen = r);
        await send('Network.enable');
        await send('Page.enable');

        await send('Page.navigate', { url: 'https://my.te.eg/echannel/' });

        // Wait dynamically until service number input is mounted (up to 25s)
        for (let i = 0; i < 60; i++) {
            const chk = await send('Runtime.evaluate', {
                expression: `!!document.querySelector('input[placeholder="Service number"]')`,
                returnByValue: true
            });
            if (chk.result?.result?.value || chk.result?.value) break;
            await new Promise(r => setTimeout(r, 300));
        }
        await new Promise(r => setTimeout(r, 500));

        // Focus & Type Number
        await send('Runtime.evaluate', {
            expression: `document.querySelector('input[placeholder="Service number"]').focus()`
        });
        for (const c of landlineNumber) {
            await send('Input.dispatchKeyEvent', { type: 'keyDown', text: c });
            await send('Input.dispatchKeyEvent', { type: 'keyUp' });
            await new Promise(r => setTimeout(r, 20));
        }
        await new Promise(r => setTimeout(r, 1000));

        // Trigger input validation & dropdown click
        const box = await send('Runtime.evaluate', {
            expression: `
                (() => {
                    const el = document.querySelector('.ant-select-selector');
                    if (!el) return null;
                    const r = el.getBoundingClientRect();
                    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
                })()
            `,
            returnByValue: true
        });

        const val = box.result ? box.result.value : box;
        const { x, y } = val || { x: 525.5, y: 333.5 };
        await send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 });
        await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 });
        await new Promise(r => setTimeout(r, 800));

        // Click Option "Internet" if present
        await send('Runtime.evaluate', {
            expression: `
                (() => {
                    const opt = Array.from(document.querySelectorAll('.ant-select-item-option')).find(o => o.innerText.trim() === 'Internet');
                    if (opt) opt.click();
                })()
            `
        });
        await new Promise(r => setTimeout(r, 800));

        // Type Password
        await send('Runtime.evaluate', {
            expression: `document.querySelector('input[type="password"]').focus()`
        });
        for (const c of password) {
            await send('Input.dispatchKeyEvent', { type: 'keyDown', text: c });
            await send('Input.dispatchKeyEvent', { type: 'keyUp' });
            await new Promise(r => setTimeout(r, 20));
        }
        await new Promise(r => setTimeout(r, 800));

        // Click Login Button
        await send('Runtime.evaluate', {
            expression: `
                (() => {
                    const btn = Array.from(document.querySelectorAll('button')).find(b => b.innerText.includes('Login'));
                    if (btn) btn.click();
                })()
            `
        });

        // Wait up to 25 seconds for quotaResult or authError
        const startTime = Date.now();
        while (Date.now() - startTime < 25000) {
            if (quotaResult || authError) break;
            await new Promise(r => setTimeout(r, 400));
        }

        if (!quotaResult && !authError) {
            try {
                const checkError = await send('Runtime.evaluate', {
                    expression: `
                        (() => {
                            const text = document.body ? document.body.innerText : '';
                            if (text.includes('Verification') || text.includes('Enter Code') || text.includes('رمز التحقق') || document.querySelector('img[src*="Captcha"]') || document.querySelector('iframe[src*="captcha"]')) {
                                return 'مطلوب رمز التحقق (كابتشا) مؤقتاً - يرجى الانتظار دقيقتين أو فحص الخط من المتصفح';
                            }
                            const antAlert = document.querySelector('.ant-message-notice, .ant-alert, [role="alert"], .ant-modal-content');
                            if (antAlert && antAlert.innerText.trim()) {
                                return antAlert.innerText.trim();
                            }
                            return null;
                        })()
                    `,
                    returnByValue: true
                });
                const errVal = checkError.result?.result?.value || checkError.result?.value;
                if (errVal) authError = errVal;
            } catch(e) {}
        }

        if (quotaResult) {
            // Wait 3.5s for dashboard elements, balance and loyalty points APIs to settle
            await new Promise(r => setTimeout(r, 3500));
            try {
                const domData = await send('Runtime.evaluate', {
                    expression: `
                        (() => {
                            const res = {};
                            const text = document.body ? document.body.innerText : '';

                            // 1. Customer Name (مرحبا، [الاسم] أو Hello [Name])
                            const nameMatch = text.match(/مرحبا[،,\s]+([^\n\r,،!]+)/) || text.match(/Hello[,\s]+([^\n\r,!]+)/i);
                            if (nameMatch && nameMatch[1]) {
                                const clean = nameMatch[1].trim();
                                if (clean && clean.length > 1 && !clean.includes('بك')) {
                                    res.name = clean;
                                }
                            }
                            const userGreeting = document.querySelector('.user-name, .customer-name, [class*="greeting"], [class*="userName"]');
                            if (userGreeting && userGreeting.innerText.trim()) {
                                res.name = res.name || userGreeting.innerText.trim();
                            }

                            // 2. Balance (الرصيد الحالي)
                            const balMatch = text.match(/الرصيد الحالي[\s\S]*?([0-9]+(?:\.[0-9]+)?)\s*(?:جنيه|ج\.م|EGP)?/);
                            if (balMatch && balMatch[1]) {
                                res.balance = parseFloat(balMatch[1]);
                            }

                            // 3. 4U Points (نقاط)
                            const ptsMatch = text.match(/نقاط[\s\S]*?([0-9,]+)\s*(?:متبقية|نقطة)?/) || text.match(/([0-9,]+)\s*متبقية[\s\S]*?نقاط/);
                            if (ptsMatch && ptsMatch[1]) {
                                const p = parseInt(ptsMatch[1].replace(/,/g, ''), 10);
                                if (!isNaN(p)) res.points = p;
                            }

                            return res;
                        })()
                    `,
                    returnByValue: true
                });

                const val = domData.result?.result?.value || domData.result?.value;
                if (val) {
                    if (val.name && (!customerNameResult || customerNameResult === 'مشترك وي')) {
                        customerNameResult = val.name;
                    }
                    if (val.balance !== undefined && val.balance !== null && !isNaN(val.balance) && balanceResult === null) {
                        balanceResult = val.balance;
                    }
                    if (val.points !== undefined && val.points !== null && !isNaN(val.points) && pointsResult === null) {
                        pointsResult = val.points;
                    }
                }
            } catch(e) {}
        }

        try {
            await send('Browser.close');
        } catch(e) {}
        try {
            ws.close();
        } catch(e) {}
    } catch(err) {
        authError = err.message;
    } finally {
        if (chrome && chrome.pid) {
            try {
                if (process.platform === 'win32') {
                    execSync(`taskkill /F /T /PID ${chrome.pid}`, { stdio: 'ignore' });
                } else {
                    execSync(`pkill -P ${chrome.pid} 2>/dev/null || true; kill -9 ${chrome.pid} 2>/dev/null || true`, { stdio: 'ignore' });
                }
            } catch(e) {}
            try {
                chrome.kill('SIGKILL');
            } catch(e) {}
        }

        try {
            if (process.platform === 'win32') {
                execSync(`cmd.exe /c "rd /s /q "${profileDir}""`, { stdio: 'ignore' });
            } else {
                execSync(`rm -rf "${profileDir}" 2>/dev/null || true`, { stdio: 'ignore' });
            }
        } catch(e) {}
        try {
            if (fs.existsSync(profileDir)) {
                fs.rmSync(profileDir, { recursive: true, force: true });
            }
        } catch(e) {}
    }

    if (quotaResult) {
        console.log(JSON.stringify({
            success: true,
            data: quotaResult,
            balance: balanceResult,
            points: pointsResult,
            customer_name: customerNameResult
        }));
    } else {
        console.log(JSON.stringify({ success: false, error: authError || 'لم يتم العثور على بيانات الاستهلاك للباقة' }));
    }
}

const args = process.argv.slice(2);
if (args.length < 2) {
    console.log(JSON.stringify({ success: false, error: 'البيانات غير مكتملة' }));
    process.exit(1);
}

fetchQuota(args[0], args[1]);
