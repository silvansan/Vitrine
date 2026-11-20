"""Knowledge base: the Markdown files in docs/ rendered at /docs/, with search.

The pages are public (they contain setup instructions, no secrets), so people
deploying Vitrine can read them before they have an account.
"""

import html
import re
from pathlib import Path

import markdown
from django.conf import settings
from django.http import FileResponse, Http404
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET

# Sidebar order and short names. Other .md files in docs/ follow, alphabetically.
ORDER = ["overview", "server", "agent", "usage", "architecture", "organizations", "integr8"]
SHORT = {
    "overview": "Overview",
    "server": "Server setup",
    "agent": "Agent on the vMix PC",
    "usage": "Using Vitrine",
    "architecture": "Architecture",
    "organizations": "Organizations & people",
    "integr8": "Moving into Integr8",
}
GITHUB = "https://github.com/silvansan/Vitrine/blob/main/"

_cache = {}


def docs_dir():
    return Path(getattr(settings, "VITRINE_DOCS_DIR", "") or Path(settings.BASE_DIR).parent / "docs")


def _sources():
    """slug -> path of every page. "overview" is the README."""
    base = docs_dir()
    found = {}
    for candidate in (base / "README.md", base.parent / "README.md"):
        if candidate.exists():
            found["overview"] = candidate
            break
    for path in sorted(base.glob("*.md")):
        if path.name != "README.md":
            found[path.stem] = path
    ordered = {slug: found[slug] for slug in ORDER if slug in found}
    ordered.update({slug: p for slug, p in found.items() if slug not in ordered})
    return ordered


def _rewrite_links(body, slugs):
    """Point links between docs at /docs/… and other repo paths at GitHub."""

    def href(match):
        target = html.unescape(match.group(2))
        if re.match(r"^[a-z]+:|^#|^/", target):
            return match.group(0)
        path, _, anchor = target.partition("#")
        path = path.removeprefix("./").removeprefix("docs/")
        anchor = "#" + anchor if anchor else ""
        if path.endswith(".md"):
            stem = Path(path).stem
            slug = "overview" if stem == "README" else stem
            if slug in slugs and "/" not in path.removesuffix(".md").lstrip("../"):
                return f'{match.group(1)}="{reverse("docs-page", args=[slug])}{anchor}"'
        if path.startswith("screenshots/"):
            return f'{match.group(1)}="{reverse("docs-media", args=[path.removeprefix("screenshots/")])}"'
        return f'{match.group(1)}="{GITHUB}{path.lstrip("./")}{anchor}"'

    return re.sub(r'(href|src)="([^"]+)"', href, body)


def _sections(slug, title, md_text):
    """Search entries: one per page and one per ## / ### heading."""
    entries = []
    current = {"slug": slug, "page": title, "title": title, "anchor": "", "text": []}
    for line in md_text.splitlines():
        m = re.match(r"^(#{2,3})\s+(.*)", line)
        if m:
            entries.append(current)
            heading = m.group(2).strip()
            current = {"slug": slug, "page": title, "title": heading, "anchor": _slugify(heading, "-"), "text": []}
        elif not line.startswith("# "):
            current["text"].append(line)
    entries.append(current)
    out = []
    for e in entries:
        text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", " ".join(e["text"]))
        text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
        text = re.sub(r"[`*_>|#]+", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        out.append({**e, "text": text[:1500]})
    return out


def _slugify(value, separator):
    value = re.sub(r"[^\w\s-]", "", value.lower()).strip()
    return re.sub(r"[\s_-]+", separator, value)


def _github_indent(text):
    """GitHub nests a list under "1. " with 2-3 spaces; Python-Markdown needs 4."""
    out, fenced = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
        if not fenced:
            stripped = line.lstrip(" ")
            indent = len(line) - len(stripped)
            if 0 < indent < 4 and stripped:
                line = "    " + stripped
        out.append(line)
    return "\n".join(out)


def _render(slug, path):
    mtime = path.stat().st_mtime
    hit = _cache.get(slug)
    if hit and hit["mtime"] == mtime:
        return hit
    text = path.read_text(encoding="utf-8")
    md = markdown.Markdown(
        extensions=["tables", "fenced_code", "sane_lists", "toc"],
        extension_configs={"toc": {"slugify": _slugify, "toc_depth": "2-3"}},
    )
    body = md.convert(_github_indent(text))
    m = re.search(r"^#\s+(.*)$", text, re.M)
    title = m.group(1).strip() if m else slug.title()
    if slug == "overview":
        title = "Overview"
    doc = {
        "mtime": mtime,
        "title": title,
        "body": body,
        "sections": _sections(slug, title, text),
    }
    # toc_tokens nests h2/h3 under the h1: flatten to the h2/h3 we show on the right.
    flat = []
    for token in md.toc_tokens:
        for child in token.get("children", []) if token["level"] == 1 else [token]:
            flat.append({"id": child["id"], "name": html.unescape(child["name"]), "level": child["level"]})
            for grand in child.get("children", []):
                if grand["level"] == 3:
                    flat.append({"id": grand["id"], "name": html.unescape(grand["name"]), "level": 3})
    doc["toc"] = flat
    _cache[slug] = doc
    return doc


@require_GET
def page(request, slug="overview"):
    sources = _sources()
    if slug not in sources:
        raise Http404("No such page. The documentation is added to the image by the GitHub build (docs/ and README.md).")
    pages = []
    search = []
    for s, p in sources.items():
        d = _render(s, p)
        pages.append({"slug": s, "title": SHORT.get(s, d["title"]), "current": s == slug})
        search.extend(d["sections"])
    doc = _render(slug, sources[slug])
    body = _rewrite_links(doc["body"], sources)
    return render(
        request,
        "guestmedia/docs.html",
        {"doc": doc, "body": body, "pages": pages, "search_index": search, "slug": slug},
    )


@require_GET
def media(request, name):
    base = (docs_dir() / "screenshots").resolve()
    path = (base / name).resolve()
    if base not in path.parents or not path.is_file() or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}:
        raise Http404()
    response = FileResponse(open(path, "rb"))
    response["Cache-Control"] = "public, max-age=3600"
    return response
