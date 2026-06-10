"""Browser profile manager — constants and defaults."""

from __future__ import annotations

import os
from pathlib import Path

KRYTH_HOME = Path(os.environ.get("KRYTH_HOME", str(Path.home() / ".kryth")))
BROWSER_PROFILES_DIR = KRYTH_HOME / "browser_profiles"
BROWSER_PROFILES_DB = KRYTH_HOME / "browser_profiles.db"

DEFAULT_PROFILE = "default"

BUILTIN_PROFILE_TYPES = [
    "default",
    "work",
    "personal",
    "testing",
    "temporary",
]

SENSITIVE_DOMAINS = {
    "github.com", "gitlab.com", "bitbucket.org",
    "stripe.com", "aws.amazon.com", "console.aws.amazon.com",
    "vercel.com", "cloudflare.com", "netlify.com",
    "heroku.com", "digitalocean.com", "linode.com",
    "fly.io", "render.com", "supabase.com",
    "firebase.google.com", "paypal.com", "twilio.com",
    "sendgrid.com", "datadog.com", "sentry.io",
    "npmjs.com", "pypi.org", "hub.docker.com",
}

PROFILE_TRANSIENT_PATTERNS = (
    "Singleton*", "*.lock", "*-journal", "LOCK", "LOCKFILE",
)

MAX_BACKUPS = 5
SESSION_CACHE_TTL = 300
SNAPSHOTS_DIR = "snapshots"
BACKUPS_DIR = "backups"
PROFILE_METADATA_FILE = "kryth_profile.json"
ENCRYPTION_MARKER_FILE = ".encrypted"
