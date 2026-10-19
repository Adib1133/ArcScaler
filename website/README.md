# ArcScaler website

A buildless static showcase. Open `index.html` directly, or run `python -m http.server 8080 --directory website` from the repository root.

All CSS, JavaScript, icons and screenshots are local; no fonts, analytics or libraries are fetched. Screenshot buttons switch between four actual captured app screens. Images can be opened at full size. Captures are from an earlier app build using demonstration profiles; the page labels this and describes newer features separately.

## GitHub Pages

A manual workflow is provided in `.github/workflows/website.yml`. Push the repository contents, choose **Settings → Pages → Source → GitHub Actions**, then run **Deploy website** from Actions. It deploys only this folder. Nothing is published merely by creating these files.

Links currently use `Adib1133/OptiXess`, as supplied in the reference website. If the upload target changes, replace that URL in `index.html`. Release links lead to the release listing and do not assume a particular installer exists. No legacy domain verification, sitemap or analytics were copied from the reference.

Assets: ArcScaler's existing `assets/arcscaler-icon.png` and four repository audit captures. Feature icons are inline SVG.
