#!/usr/bin/env python3
"""
Extract frabunden.dk WordPress content into Jekyll markdown.

Reads from a local, content-only copy of the WordPress database (wp_posts,
wp_postmeta, wp_terms, wp_term_taxonomy, wp_term_relationships — see
docs/wp-content-schema.md for how that copy is produced). Never touches
wp_users, wp_comments, or wp_usermeta, and never executes the site's own
theme/plugin PHP.

Usage:
    venv/bin/python scripts/extract_wp_content.py

Requires markdownify + beautifulsoup4 (see scripts/requirements.txt) and a
running local MariaDB container with the content-only dump imported, named
via DB_CONTAINER/DB_NAME/DB_USER/DB_PASSWORD below.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from bs4 import BeautifulSoup, Comment
from markdownify import markdownify as html_to_markdown

REPO_ROOT = Path(__file__).resolve().parent.parent

DB_CONTAINER = "frabunden-wp-db"
DB_NAME = "frabunden_dk"
DB_USER = "root"
DB_PASSWORD = "migration"


def db_query(sql: str) -> list[str]:
    """Run a query against the local content-only DB, one JSON object per row."""
    result = subprocess.run(
        [
            "docker", "exec", DB_CONTAINER,
            "mariadb", f"-u{DB_USER}", f"-p{DB_PASSWORD}", DB_NAME,
            "-N", "-B", "--raw", "-e", sql,
        ],
        capture_output=True, text=True, check=True,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def fetch_posts() -> list[dict]:
    sql = (
        "SELECT JSON_OBJECT("
        "'ID', ID, 'post_title', post_title, 'post_name', post_name, "
        "'post_date', post_date, 'post_status', post_status, "
        "'post_content', post_content) "
        "FROM wp_posts WHERE post_type = 'post' ORDER BY ID;"
    )
    return [json.loads(line) for line in db_query(sql)]


def fetch_postmeta(post_ids: list[int]) -> dict[int, dict[str, list[str]]]:
    if not post_ids:
        return {}
    ids_sql = ",".join(str(i) for i in post_ids)
    sql = (
        "SELECT JSON_OBJECT('meta_id', meta_id, 'post_id', post_id, "
        "'meta_key', meta_key, 'meta_value', meta_value) "
        f"FROM wp_postmeta WHERE post_id IN ({ids_sql}) ORDER BY meta_id;"
    )
    by_post: dict[int, dict[str, list[str]]] = {}
    for line in db_query(sql):
        row = json.loads(line)
        post_meta = by_post.setdefault(row["post_id"], {})
        post_meta.setdefault(row["meta_key"], []).append(row["meta_value"])
    return by_post


def fetch_attached_files() -> dict[int, str]:
    sql = (
        "SELECT JSON_OBJECT('post_id', post_id, 'meta_value', meta_value) "
        "FROM wp_postmeta WHERE meta_key = '_wp_attached_file';"
    )
    return {json.loads(line)["post_id"]: json.loads(line)["meta_value"] for line in db_query(sql)}


def fetch_post_categories() -> dict[int, list[str]]:
    sql = (
        "SELECT JSON_OBJECT('post_id', tr.object_id, 'name', t.name) "
        "FROM wp_term_relationships tr "
        "JOIN wp_term_taxonomy tt ON tt.term_taxonomy_id = tr.term_taxonomy_id "
        "JOIN wp_terms t ON t.term_id = tt.term_id "
        "WHERE tt.taxonomy = 'category';"
    )
    by_post: dict[int, list[str]] = {}
    for line in db_query(sql):
        row = json.loads(line)
        by_post.setdefault(row["post_id"], []).append(row["name"])
    return by_post


# --- minimal PHP unserialize, just enough for WP Recipe Maker's nested arrays ---

def php_unserialize(value: str | None):
    """PHP's string length prefix (s:N:"...") counts UTF-8 bytes, not Python
    characters, so this has to walk the UTF-8-encoded bytes rather than the
    decoded str (Danish æ/ø/å are 2 bytes each) — otherwise multi-byte
    characters throw the offsets off and truncate/misparse later fields."""
    if not value:
        return None
    data = value.encode("utf-8")
    pos = 0

    def parse():
        nonlocal pos
        kind = chr(data[pos])
        if kind == "s":
            m = re.match(rb's:(\d+):"', data[pos:])
            length = int(m.group(1))
            start = pos + m.end()
            s = data[start:start + length].decode("utf-8")
            pos = start + length + 2  # skip closing ";
            return s
        if kind == "i":
            m = re.match(rb"i:(-?\d+);", data[pos:])
            pos += m.end()
            return int(m.group(1))
        if kind == "b":
            m = re.match(rb"b:([01]);", data[pos:])
            pos += m.end()
            return m.group(1) == b"1"
        if kind == "N":
            pos += 2
            return None
        if kind == "a":
            m = re.match(rb"a:(\d+):\{", data[pos:])
            count = int(m.group(1))
            pos += m.end()
            result = {}
            for _ in range(count):
                key = parse()
                val = parse()
                result[key] = val
            pos += 1  # closing }
            return result
        raise ValueError(f"Unsupported serialized type {kind!r} at {pos} in {value!r}")

    return parse()


# --- content cleanup: strip WP/Gutenberg/plugin cruft, rewrite media URLs, to markdown ---

CAPTION_RE = re.compile(r"\[caption[^\]]*\](.*?)\[/caption\]", re.DOTALL)
CAPTION_MEDIA_RE = re.compile(r"^\s*(<a[^>]*>.*?</a>|<img[^>]*/?>)\s*(.*)$", re.DOTALL)
UPLOAD_IMG_RE = re.compile(
    r"https?://frabunden\.dk/wp-content/uploads/([^\"'\s)]+?)(-\d+x\d+)?(\.[a-zA-Z0-9]+)(?=[\"'\s)])"
)

def unwrap_caption(m: re.Match) -> str:
    """WP's [caption] shortcode wraps '<img/> trailing caption text' with no
    block-level separator, so naively unwrapping it runs the image markdown
    and caption text together on one line. Split media from trailing text
    and put the caption on its own paragraph instead."""
    inner = m.group(1)
    media_match = CAPTION_MEDIA_RE.match(inner)
    if not media_match:
        return inner
    media, caption_text = media_match.groups()
    caption_text = caption_text.strip()
    if not caption_text:
        return media
    return f"{media}<p><em>{caption_text}</em></p>"


def canonicalize_upload_urls(html: str, referenced_files: set[str]) -> str:
    """Rewrite to a Liquid relative_url call, not a bare /assets/... path —
    GitHub Pages serves this repo under a /<repo>/ path prefix until the
    custom domain goes live, and a hardcoded absolute path 404s under that
    prefix. Jekyll resolves the Liquid tag at build time regardless of which
    base path is active."""
    def repl(m):
        base, _size_suffix, ext = m.group(1), m.group(2), m.group(3)
        filename = f"{base}{ext}"
        referenced_files.add(filename)
        return f"{{{{ '/assets/uploads/{filename}' | relative_url }}}}"

    return UPLOAD_IMG_RE.sub(repl, html)


def html_to_clean_markdown(raw_html: str, referenced_files: set[str]) -> str:
    if not raw_html:
        return ""

    html = raw_html.replace("\r\n", "\n")
    html = CAPTION_RE.sub(unwrap_caption, html)
    html = canonicalize_upload_urls(html, referenced_files)

    soup = BeautifulSoup(html, "html.parser")

    # Gutenberg saves every block wrapped in <!-- wp:x --> / <!-- /wp:x -->
    # HTML comments; left in place these survive markdownify as literal
    # "<!-- wp:paragraph -->" text lines. Comments carry no content, so drop
    # all of them unconditionally rather than special-casing each block type.
    for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
        comment.extract()

    # WP Recipe Maker's dynamic block saves a fully-rendered static fallback
    # card (heading/ingredient-list/instruction-list, styled with wprm-*
    # classes) inline in post_content for contexts where the plugin's JS
    # can't render it. Most of that duplicates the structured front matter
    # (see build_ingredient_groups/build_instruction_groups) and gets
    # dropped — but its "summary" paragraph is genuine authored text with
    # no other home (not stored anywhere in postmeta), and for a recipe-only
    # post with no separate narrative it's the post's *entire* body. Keep
    # just that piece, in place of the rest of the card.
    for div in soup.find_all("div", class_="wprm-fallback-recipe"):
        summary = div.find(class_="wprm-fallback-recipe-summary")
        if summary:
            summary.extract()
            div.replace_with(summary)
        else:
            div.decompose()

    for tag in soup.find_all(["script", "style"]):
        tag.decompose()
    for tag in soup.find_all(True):
        if tag.has_attr("style"):
            del tag["style"]
        if tag.has_attr("class"):
            del tag["class"]
        if tag.has_attr("id"):
            del tag["id"]

    markdown = html_to_markdown(str(soup), heading_style="ATX", bullets="-")
    markdown = re.sub(r"\n{3,}", "\n\n", markdown).strip()
    return markdown


# --- WP Recipe Maker: structured recipe data as front-matter groups ---
#
# The recipe layout renders ingredients as a checklist (each item needs its
# own checkbox element) and instructions as individually-numbered steps with
# a distinct numeral style — both require iterating discrete items, which
# isn't possible if they're baked into one markdown blob. So these come back
# as `[{"name": str, "items": [str, ...]}, ...]` — a group's "name" is only
# ever non-empty for recipes with named sub-sections (e.g. "Grundfars" /
# "Rosmarinpølsen" for a sausage recipe with multiple variants); the layout
# just skips rendering the heading when it's empty.

def build_ingredient_groups(raw_value: str | None) -> list[dict]:
    groups = php_unserialize(raw_value) or {}
    result = []
    for group in groups.values():
        items = []
        for item in (group.get("ingredients") or {}).values():
            amount = (item.get("amount") or "").strip()
            unit = (item.get("unit") or "").strip()
            name_part = (item.get("name") or "").strip()
            notes = (item.get("notes") or "").strip()
            line = " ".join(p for p in (amount, unit, name_part) if p)
            if notes:
                line += f" ({notes})"
            if line:
                items.append(line)
        if items:
            result.append({"name": (group.get("name") or "").strip(), "items": items})
    return result


def build_instruction_groups(raw_value: str | None, attached_files: dict[int, str], referenced_files: set[str]) -> list[dict]:
    groups = php_unserialize(raw_value) or {}
    result = []
    for group in groups.values():
        items = []
        for step in (group.get("instructions") or {}).values():
            text_md = html_to_clean_markdown(step.get("text") or "", referenced_files)
            text_md = " ".join(text_md.split())
            image_id = step.get("image")
            if image_id:
                filename = attached_files.get(int(image_id))
                if filename:
                    referenced_files.add(filename)
                    text_md += f"\n\n![]({{{{ '/assets/uploads/{filename}' | relative_url }}}})"
            if text_md:
                items.append(text_md)
        if items:
            result.append({"name": (group.get("name") or "").strip(), "items": items})
    return result


def parse_equipment(raw_value: str | None) -> list[str]:
    items = php_unserialize(raw_value) or {}
    return [item["name"] for item in items.values() if item.get("name")]


def to_int_or_none(value: str | None):
    """WP Recipe Maker stores unset numeric fields as the string "0" rather
    than empty, so a literal 0 always means "not set" here — never a real
    zero-minute time or zero-serving recipe."""
    if not value:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed or None


def fetch_recipes_by_parent(attached_files: dict[int, str], referenced_files: set[str]) -> dict[int, dict]:
    """WP Recipe Maker stores each recipe as its own post type, 1:1 linked
    back to the narrative post via wprm_parent_post_id — not a many-to-one
    relationship like a brewery/beertype, so there's no need for a separate
    Jekyll collection; the fields are merged straight into the parent post."""
    sql = "SELECT ID FROM wp_posts WHERE post_type = 'wprm_recipe' AND post_status = 'publish' ORDER BY ID;"
    recipe_ids = [int(line) for line in db_query(sql)]
    meta_by_recipe = fetch_postmeta(recipe_ids)

    by_parent: dict[int, dict] = {}
    for recipe_id, meta in meta_by_recipe.items():
        def first(key: str) -> str | None:
            values = meta.get(key)
            return values[0] if values else None

        parent_id_raw = first("wprm_parent_post_id")
        if not parent_id_raw:
            continue

        by_parent[int(parent_id_raw)] = {
            "prep_time": to_int_or_none(first("wprm_prep_time")),
            "cook_time": to_int_or_none(first("wprm_cook_time")),
            "total_time": to_int_or_none(first("wprm_total_time")),
            "servings": to_int_or_none(first("wprm_servings")),
            "servings_unit": first("wprm_servings_unit"),
            "equipment": parse_equipment(first("wprm_equipment")),
            "ingredient_groups": build_ingredient_groups(first("wprm_ingredients")),
            "instruction_groups": build_instruction_groups(first("wprm_instructions"), attached_files, referenced_files),
        }
    return by_parent


def yaml_str(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def front_matter_lines(fields: list[tuple[str, object]]) -> list[str]:
    lines = ["---"]
    for key, value in fields:
        if value is None or value == "" or value == []:
            continue
        if isinstance(value, str):
            lines.append(f"{key}: {yaml_str(value)}")
        elif isinstance(value, bool):
            lines.append(f"{key}: {str(value).lower()}")
        elif isinstance(value, (int, float)):
            lines.append(f"{key}: {value}")
        elif isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {yaml_str(str(item))}")
        else:
            raise TypeError(f"Unsupported front matter value type for {key!r}: {type(value)}")
    lines.append("---")
    return lines


def group_list_lines(key: str, groups: list[dict]) -> list[str]:
    """Emit a `key: [{name, items}, ...]` block. Always uses this shape
    (even a single ungrouped recipe still gets one group with name: "") so
    the recipe layout only ever needs one code path: loop groups, skip the
    heading when name is empty, loop items."""
    if not groups:
        return []
    lines = [f"{key}:"]
    for group in groups:
        lines.append(f"  - name: {yaml_str(group['name'])}")
        lines.append("    items:")
        for item in group["items"]:
            lines.append(f"      - {yaml_str(item)}")
    return lines


def main():
    referenced_files: set[str] = set()

    posts = fetch_posts()
    post_ids = [p["ID"] for p in posts]
    meta_by_post = fetch_postmeta(post_ids)
    attached_files = fetch_attached_files()
    categories_by_post = fetch_post_categories()
    recipes_by_parent = fetch_recipes_by_parent(attached_files, referenced_files)

    def meta_first(post_id: int, key: str) -> str | None:
        values = meta_by_post.get(post_id, {}).get(key)
        return values[0] if values else None

    written = 0
    out_dir = REPO_ROOT / "_posts"
    out_dir.mkdir(parents=True, exist_ok=True)

    for post in posts:
        if post["post_status"] != "publish":
            continue

        post_id = post["ID"]
        slug = post["post_name"]
        title = post["post_title"]
        date = post["post_date"][:10]
        recipe = recipes_by_parent.get(post_id)

        thumb_id = meta_first(post_id, "_thumbnail_id")
        thumb_file = attached_files.get(int(thumb_id)) if thumb_id else None
        if thumb_file:
            referenced_files.add(thumb_file)
        thumbnail_field = f"/assets/uploads/{thumb_file}" if thumb_file else None

        body = html_to_clean_markdown(post["post_content"], referenced_files)

        fields = [
            ("title", title),
            ("date", date),
            ("permalink", f"/{slug}/"),
            ("thumbnail", thumbnail_field),
            ("categories", sorted(categories_by_post.get(post_id, []))),
        ]
        if recipe:
            fields += [
                ("recipe_prep_time", recipe["prep_time"]),
                ("recipe_cook_time", recipe["cook_time"]),
                ("recipe_total_time", recipe["total_time"]),
                ("recipe_servings", recipe["servings"]),
                ("recipe_servings_unit", recipe["servings_unit"]),
                ("recipe_equipment", recipe["equipment"]),
            ]

        lines = front_matter_lines(fields)
        if recipe:
            # Insert before the closing "---" rather than appending fresh
            # front_matter_lines output, so these stay part of the same block.
            extra = group_list_lines("recipe_ingredients", recipe["ingredient_groups"])
            extra += group_list_lines("recipe_instructions", recipe["instruction_groups"])
            lines = lines[:-1] + extra + lines[-1:]

        out_path = out_dir / f"{date}-{slug}.md"
        content = "\n".join(lines) + "\n\n" + body + "\n"
        out_path.write_text(content, encoding="utf-8")
        written += 1

    manifest_path = REPO_ROOT / "scripts" / "referenced-media.txt"
    manifest_path.write_text("\n".join(sorted(referenced_files)) + "\n", encoding="utf-8")

    print(f"Written: {written} posts")
    print(f"Referenced media files: {len(referenced_files)} (see {manifest_path.relative_to(REPO_ROOT)})")


if __name__ == "__main__":
    main()
