const { spawn, execSync } = require('child_process');
const http = require('http');
const os = require('os');
const path = require('path');
const fs = require('fs');

const WS = globalThis.WebSocket || require('ws');

function getChromePath() {
    if (process.platform === 'win32') {
        const winPaths = [
            'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
            'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
            process.env.LOCALAPPDATA + '\\Google\\Chrome\\Application\\chrome.exe'
        ];
        for (const p of winPaths) {
            if (fs.existsSync(p)) return p;
        }
        return 'chrome.exe';
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

function getPythonPath() {
    if (process.platform === 'win32') {
        return 'python';
    }
    const venvPython = '/opt/we_quota_monitor/venv/bin/python3';
    if (fs.existsSync(venvPython)) return venvPython;
    return 'python3';
}

async function fetchQuota(landlineNumber, password) {
    const chromePath = getChromePath();
    const profileDir = path.join(os.tmpdir(), `we_chrome_${Date.now()}_${Math.floor(Math.random()*10000)}`);
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
        '--disable-extensions',
        '--window-size=1280,800',
        '--lang=en-US',
        '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'
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

                if (u.includes('queryFreeUnit') || u.includes('/cz/cbs/bb/queryFreeUnit')) {
                    setTimeout(async () => {
                        try {
                            const bodyRes = await send('Network.getResponseBody', { requestId: msg.params.requestId });
                            const data = JSON.parse(bodyRes.result?.body || '{}');
                            if (data.header?.retCode === '0' && data.body) {
                                quotaResult = data.body;
                            }
                        } catch(e) {}
                    }, 250);
                } else if (u.includes('/cbs/ar/queryBalance') || u.includes('queryBalance')) {
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
                                if (err && (err.includes('attempts') || err.includes('maximum'))) {
                                    authError = 'تم تجاوز الحد الأقصى للمحاولات لدى وي. يرجى الانتظار لمدة ساعة والمحاولة مجدداً.';
                                } else if (err && (err.includes('code is incorrect') || err.includes('كود التحقق'))) {
                                    // Captcha error - will be retried
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

        const val = (res) => res.result?.result?.value !== undefined ? res.result.result.value : res.result?.value;

        await new Promise(r => ws.onopen = r);
        await send('Network.enable');
        await send('Page.enable');

        // Stealth CDP injection
        await send('Page.addScriptToEvaluateOnNewDocument', {
            source: `
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'languages', { get: () => ['ar-EG', 'ar', 'en-US', 'en'] });
                window.chrome = { runtime: {} };
            `
        });

        await send('Network.setExtraHTTPHeaders', {
            headers: {
                'Accept-Language': 'ar-EG,ar;q=0.9,en-US;q=0.8,en;q=0.7'
            }
        });

        await send('Page.navigate', { url: 'https://my.te.eg/echannel/#/login' });

        // Wait dynamically until service number input is mounted (up to 20s)
        let mounted = false;
        for (let i = 0; i < 40; i++) {
            const chk = await send('Runtime.evaluate', {
                expression: `!!(document.querySelector('input[placeholder*="Service"]') || document.querySelector('input[placeholder*="الخدمة"]') || document.querySelector('input.ant-input:not([type="password"])'))`,
                returnByValue: true
            });
            if (val(chk)) {
                mounted = true;
                break;
            }
            await new Promise(r => setTimeout(r, 500));
        }

        if (!mounted) {
            throw new Error('تعذر تحميل صفحة تسجيل الدخول، يرجى المحاولة لاحقاً');
        }

        await new Promise(r => setTimeout(r, 500));

        // 1. Focus & Type Service Number
        await send('Runtime.evaluate', {
            expression: `(() => {
                const el = document.querySelector('input[placeholder*="Service"]') || document.querySelector('input[placeholder*="الخدمة"]') || document.querySelector('input.ant-input:not([type="password"])');
                if (el) { el.focus(); el.click(); }
            })()`
        });
        for (const c of landlineNumber) {
            await send('Input.dispatchKeyEvent', { type: 'keyDown', text: c });
            await send('Input.dispatchKeyEvent', { type: 'keyUp' });
            await new Promise(r => setTimeout(r, 20));
        }
        await send('Runtime.evaluate', {
            expression: `(() => {
                const el = document.querySelector('input[placeholder*="Service"]') || document.querySelector('input[placeholder*="الخدمة"]') || document.querySelector('input.ant-input:not([type="password"])');
                if (el) {
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    el.blur();
                }
            })()`
        });
        await new Promise(r => setTimeout(r, 800));

        // 2. Select Internet from dropdown if displayed (for landlines)
        const hasSelect = await send('Runtime.evaluate', {
            expression: `!!(document.querySelector('.ant-select-selector') || document.querySelector('.ant-select'))`,
            returnByValue: true
        });
        if (val(hasSelect)) {
            await send('Runtime.evaluate', {
                expression: `(() => {
                    const sel = document.querySelector('.ant-select-selector') || document.querySelector('.ant-select');
                    if (sel) { sel.dispatchEvent(new MouseEvent('mousedown', { bubbles: true })); sel.click(); }
                })()`
            });
            await new Promise(r => setTimeout(r, 600));
            await send('Runtime.evaluate', {
                expression: `(() => {
                    const opts = Array.from(document.querySelectorAll('.ant-select-item-option, [role="option"]'));
                    const o = opts.find(x => x.innerText.toLowerCase().includes('internet') || x.innerText.includes('إنترنت'));
                    if (o) { o.dispatchEvent(new MouseEvent('mousedown', { bubbles: true })); o.click(); }
                })()`
            });
            await new Promise(r => setTimeout(r, 800));
        }

        // 3. Focus & Type Password
        await send('Runtime.evaluate', {
            expression: `(() => {
                const p = document.querySelector('input[type="password"]');
                if (p) { p.focus(); p.click(); }
            })()`
        });
        for (const c of password) {
            await send('Input.dispatchKeyEvent', { type: 'keyDown', text: c });
            await send('Input.dispatchKeyEvent', { type: 'keyUp' });
            await new Promise(r => setTimeout(r, 20));
        }
        await send('Runtime.evaluate', {
            expression: `(() => {
                const p = document.querySelector('input[type="password"]');
                if (p) {
                    p.dispatchEvent(new Event('input', { bubbles: true }));
                    p.dispatchEvent(new Event('change', { bubbles: true }));
                    p.blur();
                }
            })()`
        });
        await new Promise(r => setTimeout(r, 800));

        // 4. Click Login Button
        await send('Runtime.evaluate', {
            expression: `(() => {
                const btn = Array.from(document.querySelectorAll('button')).find(b => 
                    b.innerText.includes('Login') || b.innerText.includes('تسجيل الدخول') || b.innerText.includes('دخول') || b.type === 'submit'
                );
                if (btn && !btn.disabled) btn.click();
            })()`
        });

        // 5. Intelligent Captcha Solver & CBS API Listener (up to 30s)
        const solverScript = path.join(__dirname, 'solve_captcha.py');
        const pythonBin = getPythonPath();
        let lastSolvedImg = '';

        const startTime = Date.now();
        while (Date.now() - startTime < 35000) {
            if (quotaResult || authError) break;

            // Check if Captcha Modal is present
            const modalCheck = await send('Runtime.evaluate', {
                expression: `(() => {
                    const m = document.querySelector('.ant-modal-content');
                    if (!m) return null;
                    const img = Array.from(m.querySelectorAll('img')).find(i => i.src && i.src.startsWith('data:image'));
                    const inp = m.querySelector('input');
                    const okBtn = Array.from(m.querySelectorAll('button')).find(b => b.innerText.includes('Ok') || b.innerText.includes('حسنا'));
                    return {
                        hasModal: true,
                        imgSrc: img ? img.src : null,
                        hasInput: !!inp,
                        okDisabled: okBtn ? okBtn.disabled : true
                    };
                })()`,
                returnByValue: true
            });

            const mc = val(modalCheck);
            if (mc?.hasModal && mc?.imgSrc && mc.imgSrc !== lastSolvedImg && fs.existsSync(solverScript)) {
                lastSolvedImg = mc.imgSrc;
                const b64Data = mc.imgSrc.split(',')[1];
                const tmpImg = path.join(os.tmpdir(), `cap_${Date.now()}_${Math.floor(Math.random()*1000)}.png`);
                fs.writeFileSync(tmpImg, Buffer.from(b64Data, 'base64'));

                let solvedCode = '';
                try {
                    solvedCode = execSync(`"${pythonBin}" "${solverScript}" "${tmpImg}"`, { timeout: 6000 }).toString().trim();
                } catch(e) {}
                try { fs.unlinkSync(tmpImg); } catch(e) {}

                if (solvedCode && solvedCode.length >= 4) {
                    await send('Runtime.evaluate', {
                        expression: `(() => {
                            const m = document.querySelector('.ant-modal-content');
                            const inp = m ? m.querySelector('input') : null;
                            if (inp) { inp.focus(); inp.click(); }
                        })()`
                    });
                    for (const c of solvedCode) {
                        await send('Input.dispatchKeyEvent', { type: 'keyDown', text: c });
                        await send('Input.dispatchKeyEvent', { type: 'keyUp' });
                        await new Promise(r => setTimeout(r, 20));
                    }
                    await send('Runtime.evaluate', {
                        expression: `(() => {
                            const m = document.querySelector('.ant-modal-content');
                            const inp = m ? m.querySelector('input') : null;
                            const okBtn = m ? Array.from(m.querySelectorAll('button')).find(b => b.innerText.includes('Ok') || b.innerText.includes('حسنا')) : null;
                            if (inp) {
                                inp.dispatchEvent(new Event('input', { bubbles: true }));
                                inp.dispatchEvent(new Event('change', { bubbles: true }));
                            }
                            if (okBtn && !okBtn.disabled) okBtn.click();
                        })()`
                    });
                } else {
                    // Click refresh icon on captcha modal
                    await send('Runtime.evaluate', {
                        expression: `(() => {
                            const m = document.querySelector('.ant-modal-content');
                            const imgs = m ? Array.from(m.querySelectorAll('img')) : [];
                            if (imgs[1]) imgs[1].click();
                        })()`
                    });
                }
            }

            // Dismiss any promo overlay modals on dashboard
            await send('Runtime.evaluate', {
                expression: `(() => {
                    if (window.location.href.includes('/login')) return;
                    const closeBtn = document.querySelector('.ant-modal-close, button[aria-label="Close"], .ant-modal-close-x');
                    if (closeBtn) closeBtn.click();
                })()`
            });

            await new Promise(r => setTimeout(r, 600));
        }

        // If no quota or authError, check for specific alerts on page
        if (!quotaResult && !authError) {
            try {
                const checkError = await send('Runtime.evaluate', {
                    expression: `
                        (() => {
                            const antAlert = document.querySelector('.ant-message-notice, .ant-alert, [role="alert"]');
                            if (antAlert && antAlert.innerText.trim()) {
                                return antAlert.innerText.trim();
                            }
                            const modal = document.querySelector('.ant-modal-content');
                            if (modal && modal.innerText.includes('Verification')) {
                                return 'مطلوب رمز التحقق (كابتشا) مؤقتاً - يرجى الانتظار 3 دقائق أو فحص الخط من المتصفح';
                            }
                            return null;
                        })()
                    `,
                    returnByValue: true
                });
                const errVal = val(checkError);
                if (errVal) authError = errVal;
            } catch(e) {}
        }

        if (quotaResult) {
            // Wait 2.5s for dashboard elements, balance and loyalty points APIs to settle
            await new Promise(r => setTimeout(r, 2500));
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

                const v = val(domData);
                if (v) {
                    if (v.name && (!customerNameResult || customerNameResult === 'مشترك وي')) {
                        customerNameResult = v.name;
                    }
                    if (v.balance !== undefined && v.balance !== null && !isNaN(v.balance) && balanceResult === null) {
                        balanceResult = v.balance;
                    }
                    if (v.points !== undefined && v.points !== null && !isNaN(v.points) && pointsResult === null) {
                        pointsResult = v.points;
                    }
                }
            } catch(e) {}
        }

        try { await send('Browser.close'); } catch(e) {}
        try { ws.close(); } catch(e) {}
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
            try { chrome.kill('SIGKILL'); } catch(e) {}
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
