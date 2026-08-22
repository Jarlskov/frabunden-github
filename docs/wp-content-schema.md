# frabunden.dk WordPress content schema (audit)

Produced by querying a local, disposable MariaDB container loaded from a
content-only DB dump (`wp_posts`, `wp_postmeta`, `wp_terms`,
`wp_term_taxonomy`, `wp_term_relationships` only — `wp_users`, `wp_comments`,
`wp_usermeta` were never dumped or queried), plus a read-only filesystem scan
of the live host via SSH. No WordPress PHP was ever executed.

## Post types in scope

| post_type    | publish | draft | notes |
|--------------|---------|-------|-------|
| `post`       | 9       | 8     | blog posts, Gutenberg block content |
| `wprm_recipe`| 6       | 6     | WP Recipe Maker CPT, 1:1 with a parent `post` via `wprm_parent_post_id` (same title/date) |
| `page`       | 1       | 0     | the single published page is "Eksempelside" — WordPress's default placeholder page, not real content |

## Post types out of scope

`attachment` (68, media library — handled via `wp-content/uploads` + `_thumbnail_id` meta, not extracted as content), `custom_css`, `customize_changeset` (129), `nav_menu_item` (7, informs nav structure but isn't content), `oembed_cache` (272), `revision` (129), `request`, `wp_global_styles` — all internal/derived WordPress bookkeeping.

## Categories (confirmed against live public nav)

```
Hovedretter (6 posts)
Alkohol (1)
  └ Mjød (1)
Tilbehør (1)
  ├ Dressing (1)
  └ Sauce (0 published — only referenced by a draft)
Desserter (1)
  └ Is (1)
Menuer (0)
Ikke kategoriseret (0)
```

Tags are lightly used (mostly count=1, e.g. `thousand island`, `humle`,
`honning`, `galangarod`, `ingefær`) — not critical to preserve as a Jekyll
tag-index feature, but will be carried in front matter.

## Custom fields (WP Recipe Maker, not Custom Fields Suite — different from levemand)

Each `wprm_recipe` post carries structured recipe data in `wp_postmeta`:
`wprm_ingredients`, `wprm_instructions`, `wprm_prep_time`/`wprm_cook_time`/`wprm_total_time`,
`wprm_servings`/`wprm_servings_unit`, `wprm_rating`/`_average`/`_count`,
`wprm_nutrition_*` (calories/fat/protein/etc.), `wprm_notes`, `wprm_equipment`,
`wprm_video_embed`, `wprm_author_*`, and `wprm_parent_post_id` (links back to
the narrative `post`).

Since the relationship is strictly 1:1 (one recipe per post, linked by
`wprm_parent_post_id`), the extractor should **merge recipe fields into the
parent post's front matter** rather than creating a separate Jekyll
collection — there's no cross-referencing need like levemand's
beer→brewery/beertype many-to-one relationships.

## Content markup

Posts use the **block editor** (Gutenberg), not classic-editor shortcodes:
`<!-- wp:paragraph -->`, `<!-- wp:gallery -->`, and
`<!-- wp:wp-recipe-maker/recipe -->` (2 uses — embeds the recipe card inline).
The only classic shortcode present is `[caption]` (15 uses, standard WP image
captions — same as levemand). The extractor needs Gutenberg block-comment
stripping in addition to `[caption]` handling; there is no `[su_posts]`-style
dynamic-listing shortcode on this site.

## Spam content — not present in the database

Despite the live site showing numerous injected gambling/casino/slots posts,
**zero** posts in `wp_posts` (including drafts) reference gambling content —
titles are all legitimate Danish recipe/blog titles. No DB-level spam
exclusion rule is needed for extraction.

## Security finding: live backdoor, not stored spam

A read-only filesystem scan (`find`/`grep`/`cat`, no PHP execution) found
`/var/www/frabunden.dk/wp-content/uploads/wp-file-manager-pro/fm_backup/`,
owned by `www-data`, dated 2026-06-19. This directory is **not** a real
installed plugin (it's absent from `wp-content/plugins/`, which contains only
`akismet`, `cookie-law-info`, `google-authenticator`, `wp-recipe-maker`).
Its location and name match the well-known **WP File Manager plugin RCE
(CVE-2020-25213)** — a widely-exploited unauthenticated arbitrary-file-upload
vulnerability whose connector script gets dropped directly into
`wp-content/uploads`, bypassing WordPress's normal plugin-activation system
entirely (which is why it never shows up as an "installed" plugin).

No live `.php` payload was found in `uploads/` at scan time, and no
`eval(base64_decode|gzinflate|str_rot13)` patterns were found in any `.php`
file under the site root. Combined with the DB showing zero spam posts, the
most likely explanation is **user-agent/referrer-based cloaking** — the
backdoor serves spam content only to search-engine crawlers (to hijack the
site's SEO authority for gambling keywords) while showing normal content to
real visitors, which is also why a normal DB dump and file listing don't
surface it directly.

A Google Search Console verification file
(`googleb3e9585a09586aae.html`) is present at the site root, confirming the
domain is registered in GSC — so cloaked spam could be actively damaging
frabunden.dk's real search rankings/reputation right now, independent of the
migration timeline.

**Recommendation:** treat this as more urgent than the general "decommission
at the end of migration" plan (Phase 8) — flagged back to the user for a
decision on whether to take immediate mitigating action (e.g. removing the
`wp-file-manager-pro` directory and disabling PHP execution under
`wp-content/uploads/`) ahead of completing the full Jekyll migration.

## Media

51MB in `wp-content/uploads` (68 attachment records — real photos, e.g.
`DSC*.jpg`, `IMG_*.jpg`). One harmless anomaly: `uploads/2014/12/images.tar.gz`,
an old archive left in the uploads tree — not malicious, safe to ignore (not
referenced by any post, won't be picked up by the extractor's
referenced-media manifest anyway).

## Open items resolved by this audit

- ~~Plugin/shortcode stack~~ → WP Recipe Maker + Gutenberg blocks (not CFS/Shortcodes Ultimate).
- ~~Custom post type / structured recipe data~~ → yes, `wprm_recipe`, 1:1 with posts.
- ~~Category set~~ → confirmed above, matches live nav exactly.
- ~~Spam-exclusion rule~~ → not needed at DB level; spam is cloaked/file-based, not stored content.
