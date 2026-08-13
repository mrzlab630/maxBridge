const path = require("path");

const root = __dirname;

module.exports = {
  apps: [
    {
      name: "maxbridge",
      cwd: root,
      script: path.join(root, ".venv/bin/maxbridge"),
      interpreter: "none",
      args: "",
      env: {
        PYTHONUNBUFFERED: "1",
        MAXBRIDGE_DAEMON_PID_FILE: path.resolve(__dirname, "data", "maxbridge.pid"),
      },
      autorestart: true,
      watch: false,
      max_restarts: 50,
      min_uptime: "10s",
      restart_delay: 5000,
      kill_timeout: 15000,
      time: true,
      merge_logs: true,
      out_file: path.join(root, "logs/maxbridge-out.log"),
      error_file: path.join(root, "logs/maxbridge-error.log"),
      log_date_format: "YYYY-MM-DD HH:mm:ss Z",
    },
  ],
};
