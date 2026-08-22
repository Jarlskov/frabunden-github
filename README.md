# frabunden.dk

A static rebuild of [frabunden.dk](https://frabunden.dk), migrating away from a
compromised WordPress install to Markdown content built with Jekyll and
deployed via GitHub Actions to GitHub Pages.

This follows the same migration approach used for
[levemand.dk](https://levemand.dk) (see `jarlskov/levemand-github`).

## Migration checklist

- [x] Audit WordPress content model & security (`docs/wp-content-schema.md`)
- [ ] Extraction tooling (`scripts/extract_wp_content.py`)
- [ ] Jekyll skeleton + CI/CD
- [ ] First extraction run + media assets
- [ ] Layouts & design
- [ ] Iterative refinement
- [ ] Custom domain cutover
- [ ] WordPress decommission
