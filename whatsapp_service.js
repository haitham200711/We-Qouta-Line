/**
 * WE Quota Monitor - WhatsApp Microservice
 * Powered by @whiskeysockets/baileys (Free, Open-Source & Stable)
 * 
 * Provides a lightweight local HTTP REST API on port 3005 for:
 * - Session status & phone number
 * - QR code generation & pairing code (Link with phone number)
 * - Sending WhatsApp notifications to any Egyptian or International number
 * - Logout & session management
 */

const http = require('http');
const fs = require('fs');
const path = require('path');
const QRCode = require('qrcode');
const pino = require('pino');
const {
    default: makeWASocket,
    useMultiFileAuthState,
    DisconnectReason,
    fetchLatestBaileysVersion
} = require('@whiskeysockets/baileys');

const PORT = parseInt(process.env.WA_PORT || process.env.PORT || '3005', 10);
const DATA_DIR = path.join(__dirname, 'data');
const AUTH_DIR = path.join(DATA_DIR, 'wa_auth');

// Ensure directories exist
if (!fs.existsSync(DATA_DIR)) {
    fs.mkdirSync(DATA_DIR, { recursive: true });
}
if (!fs.existsSync(AUTH_DIR)) {
    fs.mkdirSync(AUTH_DIR, { recursive: true });
}

// Global state
let sock = null;
let connectionStatus = 'disconnected'; // 'disconnected' | 'connecting' | 'qr_ready' | 'connected'
let latestQrRaw = null;
let latestQrDataUrl = null;
let pairingCode = null;
let connectedPhone = null;
let connectedJid = null;
let isStarting = false;
let reconnectTimer = null;

// Normalize Egyptian / International phone number to WhatsApp JID
function formatWhatsAppJid(number) {
    if (!number) return null;
    let clean = String(number).trim().replace(/[^0-9]/g, '');
    if (!clean) return null;

    // Remove leading zeros like 002...
    if (clean.startsWith('00')) {
        clean = clean.substring(2);
    }

    // Egyptian mobile standard (010, 011, 012, 015)
    if (clean.startsWith('01') && clean.length >= 10 && clean.length <= 11) {
        clean = '2' + clean;
    } else if (clean.startsWith('1') && clean.length === 10) {
        // e.g. 106923040 -> 20106923040
        clean = '20' + clean;
    }

    // Append @s.whatsapp.net if not present
    if (!clean.includes('@')) {
        clean = `${clean}@s.whatsapp.net`;
    }
    return clean;
}

// Initialize WhatsApp Baileys Socket
async function startWhatsApp(forceNew = false) {
    if (isStarting) return;
    isStarting = true;

    try {
        if (reconnectTimer) {
            clearTimeout(reconnectTimer);
            reconnectTimer = null;
        }

        if (forceNew) {
            try {
                if (sock) {
                    sock.end(undefined);
                    sock = null;
                }
            } catch (e) {}
        }

        connectionStatus = 'connecting';
        const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);

        let version = [2, 3000, 1015901307];
        try {
            const vRes = await fetchLatestBaileysVersion();
            if (vRes && vRes.version) version = vRes.version;
        } catch (e) {}

        sock = makeWASocket({
            version,
            auth: state,
            logger: pino({ level: 'silent' }),
            printQRInTerminal: false,
            browser: ['WE Quota Monitor', 'Chrome', '1.0.0'],
            syncFullHistory: false,
            connectTimeoutMs: 60000,
            defaultQueryTimeoutMs: 60000,
            keepAliveIntervalMs: 30000,
        });

        // Listen for credentials updates
        sock.ev.on('creds.update', saveCreds);

        // Listen for connection events
        sock.ev.on('connection.update', async (update) => {
            const { connection, lastDisconnect, qr } = update;

            if (qr) {
                latestQrRaw = qr;
                connectionStatus = 'qr_ready';
                try {
                    latestQrDataUrl = await QRCode.toDataURL(qr, {
                        margin: 2,
                        width: 300,
                        color: {
                            dark: '#0f172a',
                            light: '#ffffff'
                        }
                    });
                } catch (err) {
                    console.error('[WhatsApp] Failed to render QR to DataURL:', err.message);
                }
                console.log('[WhatsApp] New QR code generated, awaiting scan.');
            }

            if (connection === 'close') {
                const statusCode = lastDisconnect?.error?.output?.statusCode;
                const shouldReconnect = statusCode !== DisconnectReason.loggedOut;
                console.log(`[WhatsApp] Connection closed (code ${statusCode}). Reconnect? ${shouldReconnect}`);

                latestQrRaw = null;
                latestQrDataUrl = null;
                pairingCode = null;

                if (statusCode === DisconnectReason.loggedOut || statusCode === 401) {
                    connectionStatus = 'disconnected';
                    connectedPhone = null;
                    connectedJid = null;
                    // Wipe invalid credentials
                    try {
                        fs.rmSync(AUTH_DIR, { recursive: true, force: true });
                        fs.mkdirSync(AUTH_DIR, { recursive: true });
                    } catch (e) {}
                    console.log('[WhatsApp] Logged out. Auth session cleared.');
                } else {
                    connectionStatus = 'connecting';
                    // Auto-reconnect with exponential-like backoff
                    reconnectTimer = setTimeout(() => {
                        startWhatsApp();
                    }, 5000);
                }
            } else if (connection === 'open') {
                connectionStatus = 'connected';
                latestQrRaw = null;
                latestQrDataUrl = null;
                pairingCode = null;

                connectedJid = sock.user?.id || '';
                connectedPhone = connectedJid ? connectedJid.split(':')[0].replace(/[^0-9]/g, '') : 'متصل';
                console.log(`[WhatsApp] Successfully connected! Phone: +${connectedPhone}`);
            }
        });

    } catch (err) {
        console.error('[WhatsApp] Error starting socket:', err);
        connectionStatus = 'disconnected';
    } finally {
        isStarting = false;
    }
}

// Helper: send JSON HTTP response
function sendJson(res, statusCode, data) {
    const body = JSON.stringify(data);
    res.writeHead(statusCode, {
        'Content-Type': 'application/json; charset=utf-8',
        'Content-Length': Buffer.byteLength(body),
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type, Authorization'
    });
    res.end(body);
}

// Helper: parse JSON request body
function parseBody(req) {
    return new Promise((resolve, reject) => {
        let data = '';
        req.on('data', chunk => {
            data += chunk;
            if (data.length > 1e6) {
                req.destroy();
                reject(new Error('Payload too large'));
            }
        });
        req.on('end', () => {
            if (!data.trim()) return resolve({});
            try {
                resolve(JSON.parse(data));
            } catch (err) {
                reject(new Error('Invalid JSON'));
            }
        });
        req.on('error', reject);
    });
}

// HTTP Server
const server = http.createServer(async (req, res) => {
    // Handle CORS preflight
    if (req.method === 'OPTIONS') {
        res.writeHead(204, {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type, Authorization'
        });
        res.end();
        return;
    }

    const url = new URL(req.url, `http://${req.headers.host || 'localhost'}`);
    const pathname = url.pathname;

    try {
        // --- GET /api/status ---
        if (req.method === 'GET' && (pathname === '/api/status' || pathname === '/status')) {
            return sendJson(res, 200, {
                success: true,
                status: connectionStatus,
                connected: connectionStatus === 'connected',
                phone: connectedPhone,
                has_qr: Boolean(latestQrDataUrl),
                has_pairing_code: Boolean(pairingCode),
                pairing_code: pairingCode
            });
        }

        // --- GET /api/qr ---
        if (req.method === 'GET' && (pathname === '/api/qr' || pathname === '/qr')) {
            if (connectionStatus === 'connected') {
                return sendJson(res, 200, {
                    success: true,
                    status: 'connected',
                    message: 'واتساب متصل بالفعل!',
                    phone: connectedPhone,
                    has_qr: false,
                    qr: null
                });
            }

            // If not connected and no QR yet, trigger start
            if (!sock || connectionStatus === 'disconnected') {
                startWhatsApp();
            }

            return sendJson(res, 200, {
                success: true,
                status: connectionStatus,
                has_qr: Boolean(latestQrDataUrl),
                qr: latestQrDataUrl
            });
        }

        // --- POST /api/pair (Request Pairing Code for Mobile Number) ---
        if (req.method === 'POST' && (pathname === '/api/pair' || pathname === '/pair')) {
            const body = await parseBody(req);
            const rawPhone = body.phone || body.phone_number;
            if (!rawPhone) {
                return sendJson(res, 400, {
                    success: false,
                    error: 'يرجى إدخال رقم الهاتف لطلب كود الاقتران'
                });
            }

            let cleanPhone = String(rawPhone).trim().replace(/[^0-9]/g, '');
            if (cleanPhone.startsWith('01') && cleanPhone.length >= 10 && cleanPhone.length <= 11) {
                cleanPhone = '2' + cleanPhone;
            } else if (cleanPhone.startsWith('1') && cleanPhone.length === 10) {
                cleanPhone = '20' + cleanPhone;
            }

            if (!sock) {
                await startWhatsApp();
            }

            if (connectionStatus === 'connected') {
                return sendJson(res, 400, {
                    success: false,
                    error: 'الحساب متصل بالفعل!'
                });
            }

            try {
                // Request 8-digit pairing code from Baileys
                const code = await sock.requestPairingCode(cleanPhone);
                pairingCode = code;
                console.log(`[WhatsApp] Pairing code for +${cleanPhone}: ${code}`);
                return sendJson(res, 200, {
                    success: true,
                    code: code,
                    phone: cleanPhone,
                    message: 'تم توليد كود الاقتران بنجاح. أدخل الكود في تطبيق واتساب بهاتفك.'
                });
            } catch (err) {
                console.error('[WhatsApp] Pairing code error:', err.message);
                return sendJson(res, 500, {
                    success: false,
                    error: `فشل طلب كود الاقتران: ${err.message}`
                });
            }
        }

        // --- POST /api/send ---
        if (req.method === 'POST' && (pathname === '/api/send' || pathname === '/send')) {
            const body = await parseBody(req);
            const to = body.to || body.recipient || body.phone;
            const message = body.message || body.text;

            if (!to || !message) {
                return sendJson(res, 400, {
                    success: false,
                    error: 'يرجى تحديد رقم المستلم ونص الرسالة'
                });
            }

            if (connectionStatus !== 'connected' || !sock) {
                return sendJson(res, 503, {
                    success: false,
                    error: 'خدمة واتساب غير متصلة حالياً. يرجى ربط الحساب أولاً من صفحة الإعدادات.'
                });
            }

            const jid = formatWhatsAppJid(to);
            if (!jid) {
                return sendJson(res, 400, {
                    success: false,
                    error: 'رقم الهاتف غير صالح'
                });
            }

            try {
                const result = await sock.sendMessage(jid, { text: String(message) });
                return sendJson(res, 200, {
                    success: true,
                    message: 'تم إرسال رسالة الواتساب بنجاح',
                    id: result?.key?.id,
                    to: jid
                });
            } catch (err) {
                console.error('[WhatsApp] Failed to send message to', jid, err);
                return sendJson(res, 500, {
                    success: false,
                    error: `فشل الإرسال عبر واتساب: ${err.message}`
                });
            }
        }

        // --- POST /api/logout ---
        if (req.method === 'POST' && (pathname === '/api/logout' || pathname === '/logout')) {
            try {
                if (sock) {
                    await sock.logout();
                }
            } catch (e) {}

            try {
                fs.rmSync(AUTH_DIR, { recursive: true, force: true });
                fs.mkdirSync(AUTH_DIR, { recursive: true });
            } catch (e) {}

            connectionStatus = 'disconnected';
            connectedPhone = null;
            connectedJid = null;
            latestQrDataUrl = null;
            pairingCode = null;

            // Restart clean
            setTimeout(() => startWhatsApp(true), 1500);

            return sendJson(res, 200, {
                success: true,
                message: 'تم تسجيل الخروج ومسح جلسة الواتساب بنجاح'
            });
        }

        // 404 Not Found
        sendJson(res, 404, { success: false, error: 'Endpoint not found' });

    } catch (err) {
        console.error('[WhatsApp Service Error]:', err);
        sendJson(res, 500, { success: false, error: err.message });
    }
});

// Start HTTP server & initial WhatsApp socket
server.listen(PORT, '0.0.0.0', () => {
    console.log(`=================================================`);
    console.log(`  WE WhatsApp Microservice Running on Port ${PORT}`);
    console.log(`  http://127.0.0.1:${PORT}`);
    console.log(`=================================================`);
    startWhatsApp();
});

// Graceful process exit
process.on('SIGINT', () => {
    console.log('\n[WhatsApp] Shutting down service...');
    if (sock) sock.end(undefined);
    process.exit(0);
});
process.on('SIGTERM', () => {
    console.log('\n[WhatsApp] Terminating service...');
    if (sock) sock.end(undefined);
    process.exit(0);
});
