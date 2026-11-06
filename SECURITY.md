# Security policy

This is a student project meant to run on your own machine. It is not a hosted service. The dashboard and API are read-only, and Docker publishes the database and dashboard on `127.0.0.1` only.

## Reporting a vulnerability

Please do not open a public issue for a security problem. Use GitHub's private reporting instead (**Security → Report a vulnerability** on this repository), or contact the maintainers through their GitHub profiles. Include steps to reproduce it, and we will reply as soon as we can.

## Supported versions

Only the latest release (and `main`) receives fixes.

## If you run it yourself

- Change the database password in `docker-compose.yml` (`wx-local-only`) before exposing any port beyond your own machine.
- Keep credentials in `WX_DSN` / `WX_ADMIN_DSN` environment variables, never in the code.
