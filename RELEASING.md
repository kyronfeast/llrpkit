# Releasing llrpkit

The repository is fully prepared for public release; these are the steps that
require account credentials, in order.

## 1. Create the GitHub repository and push

```console
$ gh repo create <you>/llrpkit --public --description \
    "Modern asyncio LLRP toolkit for Impinj RAIN RFID readers — typed client, emulator, web dashboard"
$ git remote add origin https://github.com/<you>/llrpkit.git
$ git push -u origin main --follow-tags
```

Then in the repo settings: add topics (`rfid`, `rain-rfid`, `llrp`, `impinj`,
`r700`, `speedway`, `asyncio`, `python`), set the social preview image
(`docs/img/dashboard-live.png` works well), and protect `main` (require the CI
checks). Add `[project.urls]` to `pyproject.toml` (Homepage, Repository,
Documentation, Changelog) now that the URLs exist, and update the
`<repo-url>` placeholders in README/CONTRIBUTING/quickstart.

## 2. Docs on GitHub Pages

The `Docs` workflow deploys `mkdocs` to the `gh-pages` branch on every push to
`main`. After the first run: Settings → Pages → deploy from `gh-pages`. Set
`site_url` in `mkdocs.yml` to the Pages URL.

## 3. PyPI via trusted publishing (no tokens stored anywhere)

1. Create the project on PyPI by registering a **pending publisher**:
   PyPI → Your account → Publishing → add `llrpkit`, owner `<you>`,
   repository `llrpkit`, workflow `release.yml`, environment `pypi`.
2. In GitHub: Settings → Environments → create `pypi` (optionally require
   review for it).
3. Tag and push:

   ```console
   $ git tag -a v0.1.0 -m "llrpkit v0.1.0"
   $ git push origin v0.1.0
   ```

   The `Release` workflow builds, publishes to PyPI with OIDC, and creates the
   GitHub release with generated notes. (The `v0.1.0` tag already exists
   locally in this repository — just push it.)

## 4. Launch checklist

A Show HN, a post to r/RFID, and a short technical write-up (the
RFModeTable-driven mode guidance or the emulator's TagFocus modeling are the
natural subjects). Seed three or four `good first issue`s — e.g. more Impinj
parameters in `codegen/impinj.xml`, a Zebra FX compatibility report, TLS
support for the client, additional curated modes.

## Version bumping

`src/llrpkit/__about__.py` is the single source of truth. Move the
`[Unreleased]` section of `CHANGELOG.md` under the new version, tag `vX.Y.Z`,
push the tag.
