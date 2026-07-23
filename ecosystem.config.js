// PM2 config for the Grants Observatory Flask app (Python).
// Runs the app via the project's virtualenv interpreter.
//
// Bound to 127.0.0.1, NOT 0.0.0.0: the app is reached at
// https://grants.sunandsun.com.tr through Cloudflare Tunnel, and cloudflared
// connects over loopback. Listening on the LAN as well would be a second,
// unencrypted way in that nothing needs — and it is what lets the login
// throttle trust CF-Connecting-IP (see _throttle_key in app.py).
//
// NOTE: PM2 caches env from when the process was first started. After changing
// anything here you must use:  pm2 restart grants-monitor --update-env
// A plain restart silently keeps the old values.
module.exports = {
  apps: [{
    name: 'grants-monitor',
    script: 'app.py',
    interpreter: '/home/moez/grants-monitor/.venv/bin/python',
    cwd: '/home/moez/grants-monitor',
    autorestart: true,
    max_restarts: 10,
    env: {
      GRANTS_HOST: '127.0.0.1',
      GRANTS_PORT: '5000',
      PYTHONIOENCODING: 'utf-8'
    }
  }]
};
