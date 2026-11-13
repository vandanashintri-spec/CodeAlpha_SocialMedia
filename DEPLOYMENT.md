# Connect deployment guide (Render)

This project remains usable locally with SQLite and local media files. The
Render blueprint configures a managed PostgreSQL database, WhiteNoise static
files, Gunicorn, HTTPS-aware Django settings, and S3-compatible object storage
for production uploads. It does **not** deploy the site or transfer local data
automatically.

## 1. Prepare and upload the project to GitHub

Install Git, create an empty GitHub repository, and from PowerShell in the
project directory run:

```powershell
git init
git add .
git status
git commit -m "Prepare Connect for deployment"
git branch -M main
git remote add origin https://github.com/YOUR_GITHUB_NAME/YOUR_REPOSITORY.git
git push -u origin main
```

Inspect `git status` before committing. `.env`, the local SQLite database,
uploaded media, and static build output are excluded by `.gitignore`. Never
commit user-uploaded data or production credentials.

## 2. Create the Render service and database

1. Sign in to Render and choose **New → Blueprint**.
2. Connect the GitHub repository containing `render.yaml`.
3. Create an S3-compatible object storage bucket (AWS S3 or Cloudflare R2, for
   example) and a restricted access key with permission to read and write
   objects in that bucket. User images are public media, so configure public
   object reads for the bucket while keeping listing and write permissions
   private. For R2, configure a public custom domain.
4. Review the blueprint. It creates a Python web service and PostgreSQL
   database. The configured web service and database use paid Render plans;
   confirm current Render pricing before applying.
5. Apply the blueprint. Render generates `SECRET_KEY` and injects the database
   connection string. Add the bucket name, region, access key ID, secret
   access key, and SMTP provider settings to the service's environment. Its
   `RENDER_EXTERNAL_HOSTNAME` is added to Django's allowed hosts and HTTPS CSRF
   origins.
6. Wait for the build and deploy to finish. Static files are collected during
   build; migrations run in the pre-deploy step. Production startup requires
   the object storage values to be configured.

The service starts with `DEBUG=False`. If using a hostname other than the
Render-provided hostname, set `ALLOWED_HOSTS` to its comma-separated hostnames
and `CSRF_TRUSTED_ORIGINS` to its full HTTPS origins, for example
`https://connect.example.com`. Do not include paths in trusted origins.

## 3. Environment variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `SECRET_KEY` | Yes in production | Django signing key. Generate a unique value; store only in Render's secret environment settings. |
| `DEBUG` | Yes in production | Set to `False`. Defaults to `True` only for local development. |
| `ALLOWED_HOSTS` | For custom domains | Comma-separated hostnames. Render's external hostname is added automatically. |
| `CSRF_TRUSTED_ORIGINS` | For custom domains | Comma-separated full HTTPS origins, such as `https://connect.example.com`. Render's origin is added automatically. |
| `DATABASE_URL` | Yes in production | Render PostgreSQL internal connection string. For local development, leave empty to use SQLite. |
| `AWS_STORAGE_BUCKET_NAME` | Yes in production | Public-media bucket name. |
| `AWS_S3_REGION_NAME` | Yes in production | Bucket region. |
| `AWS_ACCESS_KEY_ID` | Yes in production | Restricted bucket access key ID. |
| `AWS_SECRET_ACCESS_KEY` | Yes in production | Restricted bucket secret; set only in Render's secret environment settings. |
| `AWS_S3_ENDPOINT_URL` | For S3-compatible services | Provider S3 API endpoint. Required by providers such as R2; omit for AWS S3. |
| `AWS_S3_CUSTOM_DOMAIN` | For custom media domains | Public HTTPS media hostname. Configure for R2 and any custom CDN/domain. |
| `EMAIL_HOST` | Yes in production | SMTP server hostname supplied by your email provider. |
| `EMAIL_PORT` | Yes in production | SMTP port; the blueprint defaults to `587`. |
| `EMAIL_HOST_USER` | Yes in production | SMTP username from your email provider. |
| `EMAIL_HOST_PASSWORD` | Yes in production | SMTP credential or provider-issued app password. Keep it in Render secrets. |
| `EMAIL_USE_TLS` / `EMAIL_USE_SSL` | Yes in production | Select one transport security mode; the blueprint enables TLS. Do not enable both. |
| `DEFAULT_FROM_EMAIL` | Yes in production | Verified sender address/domain accepted by your SMTP provider. |
| `EMAIL_TIMEOUT` | Optional | SMTP connection timeout in seconds; defaults to `10`. |

For local development, optionally copy `.env.example` to `.env` and set values
there. The project does not load `.env` automatically: activate the environment
variables in your shell or your editor's launch configuration. Leave
`DATABASE_URL` and the S3 variables empty locally to use the existing
`db.sqlite3` and local `media/` directory. Local email always uses Django's
console backend, regardless of SMTP environment values. Production startup
fails clearly if PostgreSQL, S3-compatible storage, or SMTP settings are
missing. Only JPEG, PNG, and WebP uploads up to 5 MB are accepted.

## 4. Existing data and uploaded media

Creating the Render PostgreSQL database does not transfer the existing
SQLite data or local image files. This command preserves numeric primary keys,
password hashes, model relationships, and existing records. It validates the
fixture, checks foreign keys and unique values, and imports inside a database
transaction. Identical rows are skipped; conflicting rows abort without
overwriting target data. An import receipt makes the exact same fixture a
no-op on later runs. This is a safe import, not a synchronization mechanism:
edits made after an import are not merged.

### Back up and export from the local project

Stop local writes while preparing a consistent backup. Run these commands from
PowerShell in the project directory. Keep both backups and the fixture private:

```powershell
python -c "import sqlite3; source=sqlite3.connect('file:db.sqlite3?mode=ro', uri=True); backup=sqlite3.connect('db.sqlite3.pre-transfer.bak'); source.backup(backup); backup.close(); source.close()"
Copy-Item .\media .\media-pre-transfer -Recurse
python manage.py dumpdata auth contenttypes network --indent 2 --output .\connect-data.json
```

The JSON fixture contains personal data and password hashes. Do not commit it,
email it, or put it in a public bucket. The importer never writes to the local
SQLite database. Keep the original database and media backup unchanged until
production verification is complete.

### Migrate and validate production data

1. Deploy the application once so the Render pre-deploy migration creates the
   current PostgreSQL schema. Keep the service private/unannounced and freeze
   writes until the data and media transfer is complete. Take a PostgreSQL
   backup/snapshot before import.
2. Obtain the database's **external** connection string from Render. In a
   private PowerShell session, configure it for the management command. Keep
   any `sslmode=require` query option included by Render:

   ```powershell
   $env:DATABASE_URL = "YOUR_RENDER_EXTERNAL_POSTGRES_CONNECTION_STRING"
   $env:DEBUG = "True"
   ```

   `DEBUG=True` here is only for the local one-off management command: it
   avoids requiring production SMTP and S3 credentials on the transfer
   workstation. Do not run the web server with this database URL and setting.
3. Confirm the URL points to the intended production database, then check its
   schema and preview the import:

   ```powershell
   python manage.py migrate --noinput
   python manage.py migrate_connect_data --source .\connect-data.json --dry-run
   ```

   Review row totals and ensure the destination is correct and has no
   unrelated/conflicting users. The dry run makes no data changes.
4. Take/confirm the production database backup, then run the same import
   without the dry-run flag:

   ```powershell
   python manage.py migrate_connect_data --source .\connect-data.json
   ```

   Running the same fixture again is a no-op. A changed fixture is checked
   record-by-record: identical existing rows are skipped and any conflicting
   primary key or unique value aborts rather than overwriting data. If a
   conflict is reported, stop and investigate; do not delete target data to
   force an import.
5. Clear the temporary `DATABASE_URL` from the workstation after verifying
   the command:

   ```powershell
   Remove-Item Env:DATABASE_URL
   Remove-Item Env:DEBUG
   ```

### Transfer image files

The configured bucket is the persistent production media store. The app writes
profile images under `profiles/` and post images under `posts/`; preserve these
relative paths when copying existing local files. Install/configure the AWS
CLI (or the chosen S3-compatible provider's equivalent) with the restricted
bucket credentials, then sync the local media directory. Example for AWS S3:

```powershell
aws s3 sync .\media s3://YOUR_BUCKET_NAME/ --only-show-errors
```

For an S3-compatible provider, pass its API endpoint using the provider CLI's
endpoint option (for AWS CLI, `--endpoint-url $env:AWS_S3_ENDPOINT_URL` when
that variable is configured). Do not upload the JSON fixture or SQLite backup
to the public media bucket. Configure public reads for image objects only,
keep object listing and writes private, and set `AWS_S3_CUSTOM_DOMAIN` to a
public HTTPS media hostname when required. Verify representative profile and
post image URLs before switching users over.

New uploads are stored in the bucket and remain available across Render
restarts and redeploys. Configure bucket versioning/backups and PostgreSQL
backups with their providers. Local development continues to use `media/`.

### Existing account verification

The schema migration adds `Profile.email_verified` with a `True` default so
existing profiles and imported legacy accounts remain usable without requiring
an unexpected verification step. New signups are explicitly unverified until
they use the time-limited email link. Do not bulk-mark existing accounts
unverified. If you choose to verify legacy addresses later, plan and announce
that separate account migration to users.

## 5. Migrations, static files and superuser

The Render blueprint executes:

```sh
python manage.py migrate
```

as its pre-deploy command, and runs:

```sh
python manage.py collectstatic --no-input
```

during the build. Do not run migrations in parallel from multiple instances.
For a manual deployment, run the migration command once before switching
traffic, then collect static files as part of the build.

Create a production administrator from the Render service's authenticated
Shell:

```sh
python manage.py createsuperuser
```

Enter credentials interactively; do not put passwords in the repository or
deployment command.

## 6. Verify the live service

1. Open the Render `onrender.com` URL and confirm the home page loads over
   HTTPS.
2. Register a test account, log in, edit its profile, and upload an image.
3. Create, edit, and delete a post; upload a post image; verify feed and profile
   rendering.
4. Test like/unlike, comments, discovery/search, follow/unfollow, and both
   connection lists.
5. Confirm static CSS loads, new uploads are accessible from the public media
   domain and remain available after a deploy,
   and `/admin/` works for the superuser. Test signup verification and the
   password-reset request/link/completion flow using the configured SMTP
   provider.
6. Review Render logs and remove test content/account as appropriate.

Before each release, run locally:

```powershell
python manage.py check
python manage.py test
python manage.py makemigrations --check --dry-run
```

Locally, verification and reset emails are printed to the runserver console.
In production, ensure the SMTP sender is verified with your provider. Password
reset and verification links expire after 24 hours.

The Render service definition is in `render.yaml`. This guide prepares the
project; it does not deploy it.
