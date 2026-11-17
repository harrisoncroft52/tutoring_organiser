"""Single source of truth for the app version.

Bump this, commit and push to main. The GitHub Actions workflow builds the installer and publishes the
release; installed apps then offer the update. See RELEASING.md.
"""
__version__ = "1.2.0"
