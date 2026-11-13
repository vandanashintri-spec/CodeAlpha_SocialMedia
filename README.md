# Connect

Connect is a Django social networking application backed by SQLite for local
development and configurable PostgreSQL for production.

## Run locally (PowerShell)

```powershell
cd E:\CodeAlpha_SocialMedia
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

Open http://127.0.0.1:8000/. Register an account to use the app. Create an
administrator with `python manage.py createsuperuser` and visit
http://127.0.0.1:8000/admin/.

## Features

- Signup, login, logout, profiles, profile images, and biographies
- Create, edit, and delete your own text/image posts
- Feed, image previews, likes, and comments
- Follow/unfollow, profile connection counts, and follower/following lists
- User search and discovery
- Responsive desktop and mobile navigation
- CSRF-protected forms, validation, and server-side ownership checks
- Email verification for new accounts, resend verification, and password reset
- Automated tests: `python manage.py test`

Uploaded JPEG, PNG, and WebP images are limited to 5 MB. Local development
uses the existing SQLite database and `media/` directory. New accounts must
verify their email; existing accounts are treated as verified by the additive
migration and remain usable. Locally, verification and reset emails are
printed to the development console. Production uses SMTP and S3-compatible
persistent media storage.

The repeat-safe SQLite-to-PostgreSQL import command is
`python manage.py migrate_connect_data --source <fixture> --dry-run`, followed
by the same command without `--dry-run` after validating the preview. It keeps
primary keys and password hashes, rejects conflicting target records, and
never changes the SQLite source. See [DEPLOYMENT.md](./DEPLOYMENT.md) for
backup, media transfer, verification, and complete production instructions.

## Deployment

The project includes Render's service definition in `render.yaml` and the
deployment and data-transfer guide in [DEPLOYMENT.md](./DEPLOYMENT.md).
Production uses Render environment variables, PostgreSQL, WhiteNoise-served
static assets, and an S3-compatible media bucket. Deployment is not performed
automatically from this repository.

Before deploying code changes, run:

```powershell
python manage.py check
python manage.py test
python manage.py makemigrations --check --dry-run
```
