# Releasing a new version

Releasing is: **change the code, bump the version, push to `main`.** GitHub builds and publishes the
installer, and every installed copy of the app offers the update by itself.

## Every release
1. Make your changes.
2. Run the self-test (optional but quick): `python tests/smoke_test.py`
3. Edit `tutoring_organiser/version.py`, e.g. `__version__ = "1.2.1"`
4. Add a `## 1.2.1` section at the top of `CHANGELOG.md` describing the changes. It becomes the release
   notes that installed apps show when they offer the update. With no section, GitHub lists the commits.
5. Commit and push to `main`.
6. The **Build and publish release** workflow (Actions tab, about 10-20 min) tests the app, builds the
   installer, and creates the Release `v1.2.1`. If the self-test fails, nothing is published.
7. Installed apps check on start-up (at most twice a day) or via Settings > Check for updates now. They
   download the installer, verify its SHA-256, back up the database, install silently and restart.

A push that doesn't change `version.py` does nothing. If you push a version that is already released, the
workflow stops and says so. You can also run the workflow by hand from the Actions tab.

## Publishing v1.3.0 from this folder
Copy everything in this folder over your local clone of the repo (including `.gitignore`, the new
`assets/fonts/` folder and the new `assets/icon.ico`), then commit and push to `main`:
```
git add -A
git commit -m "Release 1.3.0"
git push
```
`version.py` says 1.3.0, so the push publishes v1.3.0 and installed 1.2.0 apps offer the update.

## Building locally instead
`powershell -ExecutionPolicy Bypass -File build.ps1`, then upload the `.exe` and `.exe.sha256` from
`installer_output\` to a GitHub Release by hand. The asset names must be exactly
`TutoringOrganiser-Setup-<version>.exe` and `...exe.sha256`.

## Where things live on a user's PC
- App: `%LOCALAPPDATA%\Programs\Tutoring Organiser` (replaced on update/uninstall)
- Data: `%APPDATA%\TutoringOrganiser` (database, Google sign-in files, `backups\`; never touched by
  update/uninstall)
