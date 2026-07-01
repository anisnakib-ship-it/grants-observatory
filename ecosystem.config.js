// PM2 config for the Grants Observatory Flask app (Python).
// Runs the app via the project's virtualenv interpreter, bound to 0.0.0.0 so it
// is reachable at 192.168.1.135:5000. Start with:  pm2 start ecosystem.config.js
module.exports = {
  apps: [{
    name: 'grants-monitor',
    script: 'app.py',
    interpreter: '/home/moez/grants-monitor/.venv/bin/python',
    cwd: '/home/moez/grants-monitor',
    autorestart: true,
    max_restarts: 10,
    env: {
      GRANTS_HOST: '0.0.0.0',
      GRANTS_PORT: '5000',
      PYTHONIOENCODING: 'utf-8'
    }
  }]
};
