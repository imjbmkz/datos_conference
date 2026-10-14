"""Static site builder for DATOS.

Reads YAML content from src/data, renders Jinja templates from src/templates,
and writes a plain HTML/CSS/JS site to dist/.

    python build.py            # build into dist/
    python build.py --serve    # build, then serve on http://localhost:8000

Environment variables:
    BASE_PATH  URL prefix the site is served under. "/" for a custom domain,
               "/datos_conference/" for the GitHub Pages project URL.
    SITE_URL   Absolute origin used for canonical and social links.
    CNAME      If set, writes a CNAME file for GitHub Pages custom domains.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import os
import shutil
import socketserver
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

ROOT = Path(__file__).parent
SRC = ROOT / "src"
DATA = SRC / "data"
TEMPLATES = SRC / "templates"
STATIC = SRC / "static"

SESSION_TYPES = {"talk", "panel", "remarks", "break", "logistics"}
SPEAKER_GROUPS = {"speaker", "host", "remarks"}

# Page registry: template, output path, nav key, title, description.
PAGES = [
    ("pages/index.html", "", "home", None, None),
    ("pages/about.html", "about/", "about", "About", "What DATOS is, who organizes it, and the venue."),
    ("pages/schedule.html", "schedule/", "schedule", "Schedule", "The full DATOS agenda."),
    ("pages/speakers.html", "speakers/", "speakers", "Speakers", "Speakers, panelists and hosts at DATOS."),
    ("pages/sponsors.html", "sponsors/", "sponsors", "Sponsors", "The sponsors and partners who make DATOS possible."),
    ("pages/register.html", "register/", "register", "Register", "Register for DATOS through Meetup."),
    ("pages/404.html", "404.html", "404", "Page not found", "This page doesn't exist."),
]


class ContentError(Exception):
    """Raised when a content file is missing a field or points at something that doesn't exist."""


def load_yaml(path: Path):
    try:
        with path.open(encoding="utf-8") as fh:
            return yaml.safe_load(fh)
    except FileNotFoundError as exc:
        raise ContentError(f"Missing content file: {path.relative_to(ROOT)}") from exc


def require(obj: dict, keys: list[str], where: str) -> None:
    missing = [k for k in keys if obj.get(k) in (None, "")]
    if missing:
        raise ContentError(f"{where}: missing {', '.join(missing)}")


def is_todo(value) -> bool:
    return not value or str(value).strip().upper().startswith("TODO")


def time_label(dt: datetime) -> str:
    return dt.strftime("%I:%M %p").lstrip("0")


def load_content() -> dict:
    site = load_yaml(DATA / "site.yaml")
    require(site, ["current_edition", "name", "full_name", "nav", "cta", "organizer"], "site.yaml")

    year = str(site["current_edition"])
    edir = DATA / "editions" / year
    event = load_yaml(edir / "event.yaml")
    speakers = load_yaml(edir / "speakers.yaml") or []
    sessions = load_yaml(edir / "sessions.yaml") or []
    sponsors = load_yaml(edir / "sponsors.yaml") or {}
    faq = load_yaml(edir / "faq.yaml") or []

    # --- event -------------------------------------------------------------
    require(event, ["year", "theme", "start", "end", "doors_open", "venue", "capacity", "registration"], "event.yaml")
    for key in ("start", "end", "doors_open"):
        try:
            event[f"{key}_dt"] = datetime.fromisoformat(event[key])
        except ValueError as exc:
            raise ContentError(f"event.yaml: {key} must be ISO 8601, e.g. 2026-11-20T16:00:00+08:00") from exc
    sd = event["start_dt"]
    event["date_label"] = f"{sd:%B} {sd.day}, {sd.year}"  # avoids %-d, which fails on Windows
    event["weekday"] = event["start_dt"].strftime("%A")
    event["time_label"] = f"{time_label(event['doors_open_dt'])} – {time_label(event['end_dt'])}"
    event["program_label"] = f"Program starts {time_label(event['start_dt'])}"
    event["venue"]["address_known"] = not is_todo(event["venue"].get("address"))
    event["registration"]["url_is_event_page"] = "/events/" in event["registration"]["url"]

    # --- speakers ----------------------------------------------------------
    by_id: dict[str, dict] = {}
    for i, sp in enumerate(speakers):
        require(sp, ["id", "name", "group"], f"speakers.yaml entry {i + 1}")
        if sp["group"] not in SPEAKER_GROUPS:
            raise ContentError(f"speakers.yaml {sp['id']}: group must be one of {sorted(SPEAKER_GROUPS)}")
        if sp["id"] in by_id:
            raise ContentError(f"speakers.yaml: duplicate id {sp['id']}")
        sp.setdefault("links", {})
        sp["links"] = sp["links"] or {}
        sp["initials"] = "".join(w[0] for w in sp["name"].split()[:2]).upper()
        for key in ("org", "org_short", "title", "bio", "photo"):
            sp[key] = sp.get(key) or ""
        sp["org_short"] = sp["org_short"] or sp["org"]
        sp["photo_path"] = None
        if sp.get("photo"):
            photo = STATIC / "images" / year / "speakers" / sp["photo"]
            if not photo.exists():
                raise ContentError(f"speakers.yaml {sp['id']}: photo not found at {photo.relative_to(ROOT)}")
            sp["photo_path"] = f"images/{year}/speakers/{sp['photo']}"
        sp["sessions"] = []
        by_id[sp["id"]] = sp

    # --- sessions ----------------------------------------------------------
    day = event["start_dt"].date()
    tz = event["start_dt"].tzinfo
    previous_end = None
    for i, s in enumerate(sessions):
        require(s, ["id", "title", "type", "start", "end"], f"sessions.yaml entry {i + 1}")
        if s["type"] not in SESSION_TYPES:
            raise ContentError(f"sessions.yaml {s['id']}: type must be one of {sorted(SESSION_TYPES)}")
        try:
            start = datetime.combine(day, datetime.strptime(s["start"], "%H:%M").time(), tz)
            end = datetime.combine(day, datetime.strptime(s["end"], "%H:%M").time(), tz)
        except ValueError as exc:
            raise ContentError(f"sessions.yaml {s['id']}: start/end must look like 16:35") from exc
        if end <= start:
            raise ContentError(f"sessions.yaml {s['id']}: end must be after start")
        if previous_end and start < previous_end:
            raise ContentError(f"sessions.yaml {s['id']}: starts before the previous session ends")
        previous_end = end
        s.setdefault("has_qa", False)
        s.setdefault("featured", False)
        s["abstract"] = s.get("abstract") or ""
        s["moderator_person"] = None
        s["start_dt"], s["end_dt"] = start, end
        s["start_label"], s["end_label"] = time_label(start), time_label(end)
        s["minutes"] = int((end - start) / timedelta(minutes=1))
        s["start_iso"], s["end_iso"] = start.isoformat(), end.isoformat()

        people = []
        for sid in s.get("speakers") or []:
            if sid not in by_id:
                raise ContentError(f"sessions.yaml {s['id']}: unknown speaker id '{sid}'")
            people.append(by_id[sid])
            by_id[sid]["sessions"].append({"session": s, "role": "Panelist" if s["type"] == "panel" else "Speaker"})
        s["people"] = people
        mod = s.get("moderator")
        if mod:
            if mod not in by_id:
                raise ContentError(f"sessions.yaml {s['id']}: unknown moderator id '{mod}'")
            s["moderator_person"] = by_id[mod]
            by_id[mod]["sessions"].append({"session": s, "role": "Moderator"})

    # Hosts' repeated slots (start + closure) read better as one role line.
    for sp in by_id.values():
        if sp["group"] == "host":
            for item in sp["sessions"]:
                item["role"] = "Host"

    # --- sponsors ----------------------------------------------------------
    for tier in sponsors.get("tiers", []):
        require(tier, ["name", "sponsors"], "sponsors.yaml tier")
        tier.setdefault("size", "md")
        for sp in tier["sponsors"]:
            require(sp, ["name"], f"sponsors.yaml ({tier['name']})")
            sp["url"] = sp.get("url") or ""
            sp["logo_path"] = None
            logo = sp.get("logo")
            if logo:
                rel = logo if logo.startswith("images/") else f"images/{year}/sponsors/{logo}"
                if not (STATIC / rel).exists():
                    raise ContentError(f"sponsors.yaml {sp['name']}: logo not found at src/static/{rel}")
                sp["logo_path"] = rel

    speaker_list = list(by_id.values())
    return {
        "site": site,
        "year": year,
        "event": event,
        "sessions": sessions,
        "speakers": speaker_list,
        "speaker_groups": {
            "speaker": [s for s in speaker_list if s["group"] == "speaker"],
            "host": [s for s in speaker_list if s["group"] == "host"],
            "remarks": [s for s in speaker_list if s["group"] == "remarks"],
        },
        "featured_sessions": [s for s in sessions if s.get("featured")],
        "sponsors": sponsors,
        "faq": faq,
    }


def ics_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def build_ics(ctx: dict, site_url: str) -> str:
    ev, site = ctx["event"], ctx["site"]
    fmt = "%Y%m%dT%H%M%SZ"
    start = ev["doors_open_dt"].astimezone(timezone.utc).strftime(fmt)
    end = ev["end_dt"].astimezone(timezone.utc).strftime(fmt)
    stamp = datetime.now(timezone.utc).strftime(fmt)
    location = f"{ev['venue']['name']}, {ev['venue']['area']}"
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Data Engineering Pilipinas//DATOS//EN",
        "CALSCALE:GREGORIAN",
        "BEGIN:VEVENT",
        f"UID:datos-{ctx['year']}@{site['domain']}",
        f"DTSTAMP:{stamp}",
        f"DTSTART:{start}",
        f"DTEND:{end}",
        f"SUMMARY:{ics_escape(site['full_name'] + ' ' + ctx['year'])}",
        f"DESCRIPTION:{ics_escape(ev['theme'] + '. Schedule: ' + site_url + 'schedule/')}",
        f"LOCATION:{ics_escape(location)}",
        f"URL:{site_url}",
        "END:VEVENT",
        "END:VCALENDAR",
    ]

    def fold(line: str) -> str:
        # RFC 5545: lines longer than 75 octets continue on the next line after a space.
        chunks, current = [], ""
        for ch in line:
            if len((current + ch).encode("utf-8")) > (75 if not chunks else 74):
                chunks.append(current)
                current = ""
            current += ch
        chunks.append(current)
        return "\r\n ".join(chunks)

    return "\r\n".join(fold(line) for line in lines) + "\r\n"


def build(out: Path, base_path: str, site_url: str, cname: str | None) -> None:
    ctx = load_content()
    base = "/" + base_path.strip("/") + "/" if base_path.strip("/") else "/"
    site_url = site_url.rstrip("/") + base

    def url(path: str = "") -> str:
        """Link to a page or file inside the site, respecting BASE_PATH."""
        if path.startswith(("http://", "https://", "mailto:", "#")):
            return path
        return base + path.lstrip("/")

    env = Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=select_autoescape(["html"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.globals.update(ctx)
    env.globals.update(url=url, site_url=site_url, is_todo=is_todo, build_time=datetime.now().isoformat())

    out = out.resolve()
    if out in (ROOT.resolve(), SRC.resolve()) or SRC.resolve().is_relative_to(out):
        raise ContentError(f"Refusing to use {out} as the output folder; pick an empty folder such as dist/")
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(STATIC, out)

    for template, path, key, title, description in PAGES:
        html = env.get_template(template).render(
            page_key=key,
            page_title=title,
            page_description=description or ctx["event"]["summary"],
            page_path=path,
        )
        target = out / path / "index.html" if not path.endswith(".html") else out / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(html, encoding="utf-8")

    (out / f"datos-{ctx['year']}.ics").write_text(build_ics(ctx, site_url), encoding="utf-8")
    urls = [site_url + p for _, p, k, *_ in PAGES if k != "404"]
    (out / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "".join(f"  <url><loc>{u}</loc></url>\n" for u in urls)
        + "</urlset>\n",
        encoding="utf-8",
    )
    (out / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {site_url}sitemap.xml\n", encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    if cname:
        (out / "CNAME").write_text(cname.strip() + "\n", encoding="utf-8")

    todos = sum(p.read_text(encoding="utf-8").count("TODO") for p in DATA.rglob("*.yaml"))
    print(f"Built {len(PAGES)} pages for DATOS {ctx['year']} into {out}/ (base {base})")
    if todos:
        print(f"  {todos} TODO markers remain in src/data — see CONTENT_NEEDED.md")


def serve(out: Path, port: int) -> None:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out))
    with socketserver.TCPServer(("", port), handler) as httpd:
        print(f"Serving on http://localhost:{port}/ (Ctrl+C to stop)")
        httpd.serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the DATOS website.")
    parser.add_argument("--out", default="dist", help="output folder (default: dist)")
    parser.add_argument("--serve", action="store_true", help="serve the built site locally")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    base_path = os.environ.get("BASE_PATH", "/")
    site_url = os.environ.get("SITE_URL", "https://datos.dataengineering.ph")
    if args.serve and base_path.strip("/"):
        print("Note: --serve works best with BASE_PATH=/ ; ignoring BASE_PATH for local preview.")
        base_path = "/"
    try:
        build(ROOT / args.out, base_path, site_url, os.environ.get("CNAME"))
    except ContentError as exc:
        print(f"Content error: {exc}", file=sys.stderr)
        return 1
    if args.serve:
        serve(ROOT / args.out, args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
