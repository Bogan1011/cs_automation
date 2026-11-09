"""Admin commands. Run inside the container: docker compose exec app python -m app.cli <command>"""
import argparse
import getpass
import json
import sys

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
