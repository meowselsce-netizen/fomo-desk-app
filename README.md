# FOMO Desk PWA

Static shell. Desk data is AES-GCM ciphertext under `data/`.
Open the private link from Telegram (URL fragment). Do not commit secrets.

The files in `data/` are an encrypted snapshot. Automatic refresh is not configured.

The PAPER runner is a separate Python package in `fomo-desk-backend/`. It does not replace this static site. See `fomo-desk-backend/README.md` for local startup. Do not point that database at this `data/` directory.
