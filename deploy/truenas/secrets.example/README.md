# Secret file inventory

Run `python tools/generate-secrets.py --output <private-directory>` on a trusted
administrator workstation. Upload the generated files to the dedicated TrueNAS
secrets dataset; do not fill secret values into this tracked directory.

The generator creates:

- `postgres_password`
- `database_url`
- `app_master_key`
- `session_pepper`
- `admin_setup_token`
- `backup_encryption_key`
- empty `tls.crt` and `tls.key` placeholders for internal-CA or public-ACME mode

For user-certificate mode, replace the two empty TLS placeholders with the PEM
certificate chain and its matching private key before installation.
