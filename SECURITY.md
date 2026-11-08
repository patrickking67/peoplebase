# Security policy

## Supported version

Security fixes target the latest version on `main`.

## Reporting a vulnerability

Use GitHub private vulnerability reporting when available. Otherwise, contact the repository owner through the public GitHub profile. Do not open a public issue containing exploit details, credentials, or personal data.

## Deployment notes

- `SECRET_KEY` is required in every environment. Use a long random value and keep it out of source control.
- Use a managed database and configure it with `DATABASE_URL` for production.
- Terminate TLS at the hosting platform and do not enable Flask debug mode publicly.
- Add authentication and authorization before storing real employee data.
