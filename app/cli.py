"""Admin commands. Run inside the container: docker compose exec app python -m app.cli <command>"""
import argparse
import getpass
import json
import re
import sys
from collections import Counter, defaultdict

from sqlalchemy import select

from . import crypto
from .config import REGIONS
from .db import Client, SessionLocal, init_db
from .visionone import VisionOneClient


def _read_key(_args=None) -> str:
    key = getpass.getpass("Vision One API key (input hidden): ").strip()
    if not key:
        sys.exit("No key entered")
    return key


SENSITIVE = re.compile(r"name|ip|guid|mac|user|domain|host|fqdn|address|serial|id$|uuid|sid", re.I)
SAFE = re.compile(r"version|pattern|engine|component|status|manager|type|platform|policy|group|state|connect|deploy|install", re.I)


def _flatten(obj, prefix=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _flatten(v, f"{prefix}.{k}" if prefix else k)
    elif isinstance(obj, list):
        if obj and all(isinstance(x, (dict, list)) for x in obj):
            for x in obj:
                yield from _flatten(x, prefix + "[]")
        else:
            yield prefix, ", ".join(map(str, obj))
    else:
        yield prefix, obj


def inspect_fields(items: list[dict]) -> str:
    """Field paths, how often they are filled, and value counts for low-cardinality, non-identifying fields."""
    seen: dict[str, Counter] = defaultdict(Counter)
    for item in items:
        for path, val in _flatten(item):
            if val not in (None, "", []):
                seen[path][str(val)[:80]] += 1
    out = [f"{len(items)} endpoint records returned by the API", ""]
    for path in sorted(seen):
        counts = seen[path]
        filled = sum(counts.values())
        leaf = path.split(".")[-1].replace("[]", "")
        line = f"{path}  (filled {filled})"
        hidden = (bool(SENSITIVE.search(leaf)) and not SAFE.search(leaf) and not leaf.lower().startswith("os")
                  and "patterns[]" not in path)
        if "datetime" in leaf.lower():
            line += f"  [{len(counts)} distinct timestamps]"
        elif hidden:
            line += f"  [{len(counts)} distinct, values hidden]"
        elif len(counts) <= 25 or SAFE.search(leaf):
            top = ", ".join(f"{v!r}: {n}" for v, n in counts.most_common(25))
            line += f"\n      {top}" + (f"  … +{len(counts) - 25} more" if len(counts) > 25 else "")
        else:
            line += f"  [{len(counts)} distinct]"
        out.append(line)
    return "\n".join(out)


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m app.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("gen-key", help="print a new MASTER_KEY")
    a = sub.add_parser("add-client", help="add a client (prompts for its API key)")
    a.add_argument("--name", required=True)
    a.add_argument("--region", required=True, choices=sorted(REGIONS))
    a.add_argument("--report-to", default="", help="comma-separated recipients for report drafts")
    u = sub.add_parser("set-key", help="rotate a client's API key")
    u.add_argument("--name", required=True)
    e = sub.add_parser("edit-client")
    e.add_argument("--name", required=True)
    e.add_argument("--region", choices=sorted(REGIONS))
    e.add_argument("--report-to")
    e.add_argument("--enabled", choices=["yes", "no"])
    sub.add_parser("list-clients")
    t = sub.add_parser("test", help="check a client's key and print endpoint count")
    t.add_argument("--name", required=True)
    d = sub.add_parser("dump", help="print the first raw endpoint record (to check field mapping)")
    d.add_argument("--name", required=True)
    i = sub.add_parser("inspect", help="summarise the API fields across all endpoints (hides names/IPs)")
    i.add_argument("--name", required=True)
    sw = sub.add_parser("set-swp", help="connect a client's Server & Workload Protection console API (exact patterns)")
    sw.add_argument("--name", required=True)
    sw.add_argument("--url", required=True, help="SWP API base, e.g. https://workload.in-1.cloudone.trendmicro.com")
    ap = sub.add_parser("set-apex", help="connect a client's Apex One / SEP automation API (exact patterns)")
    ap.add_argument("--name", required=True)
    ap.add_argument("--url", required=True, help="e.g. https://xxxx.manage.trendmicro.com")
    ap.add_argument("--app-id", required=True)
    si = sub.add_parser("swp-inspect", help="summarise SWP computer fields (hides names/IPs)")
    si.add_argument("--name", required=True)
    pr = sub.add_parser("apex-probe", help="try Apex One API paths and summarise their fields")
    pr.add_argument("--name", required=True)
    dt = sub.add_parser("detail", help="fetch single-endpoint detail for one SEP and one SWP agent (names hidden)")
    dt.add_argument("--name", required=True)
    sub.add_parser("poll", help="run one collection for all clients now")
    args = p.parse_args(argv)

    if args.cmd == "gen-key":
        print(crypto.generate_key())
        return
    init_db()
    with SessionLocal() as s:
        def get(name):
            c = s.scalars(select(Client).where(Client.name == name)).first()
            if not c:
                sys.exit(f"No client named {name!r}")
            return c

        if args.cmd == "add-client":
            key = _read_key(args)
            s.add(Client(name=args.name, region=args.region, api_key_enc=crypto.encrypt(key),
                         report_to=args.report_to))
            s.commit()
            print(f"Added {args.name} ({args.region})")
        elif args.cmd == "set-key":
            c = get(args.name)
            c.api_key_enc = crypto.encrypt(_read_key(args))
            s.commit()
            print("Key updated")
        elif args.cmd == "edit-client":
            c = get(args.name)
            if args.region:
                c.region = args.region
            if args.report_to is not None:
                c.report_to = args.report_to
            if args.enabled:
                c.enabled = args.enabled == "yes"
            s.commit()
            print("Updated")
        elif args.cmd == "list-clients":
            for c in s.scalars(select(Client).order_by(Client.name)):
                state = "ok" if c.last_poll_ok else ("error: " + (c.last_error or "")[:80] if c.last_poll_ok is False else "never polled")
                print(f"{c.name:30} {c.region:4} {'enabled' if c.enabled else 'disabled':9} {state}")
        elif args.cmd == "set-swp":
            c = get(args.name)
            c.swp_url = args.url
            c.swp_key_enc = crypto.encrypt(getpass.getpass("SWP API key (input hidden): ").strip())
            s.commit()
            print("SWP API saved")
        elif args.cmd == "set-apex":
            c = get(args.name)
            c.apex_url, c.apex_app_id = args.url, args.app_id
            c.apex_key_enc = crypto.encrypt(getpass.getpass("Apex One API key (input hidden): ").strip())
            s.commit()
            print("Apex One API saved")
        elif args.cmd == "swp-inspect":
            from .swp import SWPClient
            c = get(args.name)
            with SWPClient(c.swp_url, crypto.decrypt(c.swp_key_enc)) as swp:
                comps = swp.list_computers()
            print(inspect_fields(comps))
        elif args.cmd == "apex-probe":
            from .apexone import PROBE_PATHS, ApexClient
            c = get(args.name)
            with ApexClient(c.apex_url, c.apex_app_id, crypto.decrypt(c.apex_key_enc)) as apex:
                for path in PROBE_PATHS:
                    print(f"=== GET {path}")
                    try:
                        data = apex.request("GET", path)
                        content = data.get("result_content", data) if isinstance(data, dict) else data
                        items = content if isinstance(content, list) else [content]
                        print(inspect_fields(items))
                    except Exception as exc:
                        print(f"  failed: {exc}")
        elif args.cmd == "detail":
            from .collector import classify_protection
            c = get(args.name)
            with VisionOneClient(crypto.decrypt(c.api_key_enc), c.region) as v1:
                items = v1.list_endpoints()
                picked = {}
                for it in items:
                    p = classify_protection(it)
                    if p in ("SEP", "SWP") and p not in picked:
                        picked[p] = it
                for p, it in picked.items():
                    print(f"===== {p}: list record patterns")
                    print(json.dumps((it.get("eppAgent") or {}).get("patterns"), indent=1)[:3000])
                    print(f"===== {p}: detail record")
                    try:
                        d = v1.get_endpoint(it["agentGuid"])
                        print(inspect_fields([d]))
                        print(f"----- {p}: detail eppAgent.patterns")
                        print(json.dumps((d.get("eppAgent") or {}).get("patterns"), indent=1)[:6000])
                    except Exception as exc:
                        print(f"  failed: {exc}")
        elif args.cmd == "inspect":
            c = get(args.name)
            with VisionOneClient(crypto.decrypt(c.api_key_enc), c.region) as v1:
                items = v1.list_endpoints()
            print(inspect_fields(items))
        elif args.cmd in ("test", "dump"):
            c = get(args.name)
            with VisionOneClient(crypto.decrypt(c.api_key_enc), c.region) as v1:
                items = v1.list_endpoints()
            if args.cmd == "test":
                print(f"OK: {len(items)} endpoints")
            else:
                print(json.dumps(items[0] if items else {}, indent=2))
        elif args.cmd == "poll":
            from .collector import collect_all
            collect_all()
            print("Done")


if __name__ == "__main__":
    main()
