# frabunden.dk

A static rebuild of [frabunden.dk](https://frabunden.dk), migrating away from a
compromised WordPress install to Markdown content built with Jekyll and
deployed via GitHub Actions to GitHub Pages.

This follows the same migration approach used for
[levemand.dk](https://levemand.dk) (see `jarlskov/levemand-github`).

## Migration checklist

- [x] Audit WordPress content model & security (`docs/wp-content-schema.md`)
- [x] Extraction tooling (`scripts/extract_wp_content.py`)
- [x] Jekyll skeleton + CI/CD
- [x] First extraction run + media assets
- [x] Layouts & design (author bio pages excluded — no real data to render)
- [x] Iterative refinement (spam sweep, media dedup, external link check — all clean)
- [ ] Custom domain cutover
- [ ] WordPress decommission
