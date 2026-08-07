"""Administration from the command line.

Account and client-master maintenance without needing the web UI -- useful for
first-time setup, for recovering a locked-out administrator, and for scripting
the monthly master refresh.

    python3 -m bms.cli create-user --username jahnvi --admin
    python3 -m bms.cli reset-password --username jahnvi
    python3 -m bms.cli list-users
    python3 -m bms.cli add-client --name "ACME LLC" --sub-group "AUH:Abu Dhabi"
    python3 -m bms.cli import-clients master.xlsx
    python3 -m bms.cli list-clients
    python3 -m bms.cli purge
"""

from __future__ import annotations

import argparse
import getpass
import secrets
import sys
from pathlib import Path

from sqlalchemy import select

from . import master
from .db import create_all, init_engine, session_scope
from .models import Client, User
from .pipeline import purge_closed_cases
from .web.security import hash_password


def _prompt_password(supplied: str | None) -> tuple[str, bool]:
    """Return (password, generated). Never echoes a typed password."""
    if supplied:
        if len(supplied) < 10:
            raise SystemExit("choose a password of at least 10 characters")
        return supplied, False
    if sys.stdin.isatty():
        first = getpass.getpass("Password: ")
        second = getpass.getpass("Repeat password: ")
        if first != second:
            raise SystemExit("passwords did not match")
        if len(first) < 10:
            raise SystemExit("choose a password of at least 10 characters")
        return first, False
    generated = secrets.token_urlsafe(12)
    return generated, True


def create_user(args) -> int:
    with session_scope() as session:
        if session.scalar(select(User).where(User.username == args.username)):
            raise SystemExit(f"user {args.username!r} already exists")
        password, generated = _prompt_password(args.password)
        session.add(
            User(
                username=args.username,
                display_name=args.display_name or args.username,
                log_associate=args.log_associate,
                password_hash=hash_password(password),
                is_admin=args.admin,
            )
        )
        role = "administrator" if args.admin else "processor"
        print(f"created {role} {args.username!r}")
        if generated:
            print(f"generated password: {password}")
    return 0


def reset_password(args) -> int:
    with session_scope() as session:
        user = session.scalar(select(User).where(User.username == args.username))
        if user is None:
            raise SystemExit(f"no user named {args.username!r}")
        password, generated = _prompt_password(args.password)
        user.password_hash = hash_password(password)
        print(f"password reset for {args.username!r}")
        if generated:
            print(f"generated password: {password}")
    return 0


def list_users(args) -> int:
    with session_scope() as session:
        users = list(session.scalars(select(User).order_by(User.username)))
        if not users:
            print("no users")
            return 0
        width = max(len(u.username) for u in users)
        for user in users:
            flags = ", ".join(
                part
                for part in (
                    "admin" if user.is_admin else "",
                    "" if user.active else "disabled",
                )
                if part
            )
            print(
                f"{user.username:<{width}}  {user.display_name:<24} "
                f"log={user.log_associate or '-':<10} {flags}"
            )
    return 0


def add_client(args) -> int:
    with session_scope() as session:
        client, created = master.get_or_create_client(session, args.name, code=args.code)
        print(("created" if created else "found") + f" client {client.name!r}")

        for spec in args.sub_group or []:
            name, _, emirate = spec.partition(":")
            _, made = master.get_or_create_sub_group(
                session, client, name.strip(), emirate=emirate.strip() or None
            )
            print(f"  sub-group {name.strip()!r}{' (' + emirate.strip() + ')' if emirate else ''}"
                  f" {'created' if made else 'already present'}")

        for spec in args.entity or []:
            name, _, contract = spec.partition("=")
            _, made = master.get_or_create_entity(
                session, client, name.strip(), contract_name=contract.strip() or None
            )
            print(f"  legal entity {name.strip()!r} {'created' if made else 'already present'}")
    return 0


def import_clients(args) -> int:
    data = Path(args.workbook).read_bytes()
    with session_scope() as session:
        summary = master.import_workbook(session, data, actor=args.actor)
    print(
        f"read {summary.rows_read} row(s): "
        f"{summary.clients_created} client(s), "
        f"{summary.sub_groups_created} sub-group(s), "
        f"{summary.legal_entities_created} legal entity(ies), "
        f"{summary.policies_created} policy(ies) created"
    )
    for problem in summary.skipped:
        print(f"  skipped: {problem}")
    return 0


def list_clients(args) -> int:
    with session_scope() as session:
        clients = list(session.scalars(select(Client).order_by(Client.name)))
        if not clients:
            print("client master is empty")
            return 0
        for client in clients:
            print(f"{client.name}" + (f"  [{client.code}]" if client.code else ""))
            for sub_group in sorted(client.sub_groups, key=lambda s: s.name):
                emirate = f" ({sub_group.emirate})" if sub_group.emirate else ""
                print(f"    sub-group: {sub_group.name}{emirate}")
            for entity in sorted(client.legal_entities, key=lambda e: e.name):
                print(f"    entity:    {entity.name}")
                if entity.contract_name and entity.contract_name != entity.name:
                    print(f"               contract literal: {entity.contract_name}")
            for policy in client.policies:
                bits = [policy.insurer]
                if policy.policy_no:
                    bits.append(f"policy {policy.policy_no}")
                if policy.category:
                    bits.append(policy.category)
                if policy.addition_template_key:
                    bits.append(f"+{policy.addition_template_key}")
                if policy.deletion_template_key:
                    bits.append(f"-{policy.deletion_template_key}")
                print(f"    policy:    {' · '.join(bits)}")
    return 0


def purge(args) -> int:
    with session_scope() as session:
        result = purge_closed_cases(session, actor="cli")
    print(f"purged {result.files} document(s) and {result.exports} export(s) "
          f"across {result.cases} closed case(s)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bms.cli", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    new_user = subparsers.add_parser("create-user", help="create an account")
    new_user.add_argument("--username", required=True)
    new_user.add_argument("--display-name")
    new_user.add_argument("--log-associate", help="name used in the log's SHARED BY column")
    new_user.add_argument("--admin", action="store_true", help="grant access to administration")
    new_user.add_argument("--password", help="omit to be prompted, or to have one generated")
    new_user.set_defaults(func=create_user)

    reset = subparsers.add_parser("reset-password", help="reset an account password")
    reset.add_argument("--username", required=True)
    reset.add_argument("--password")
    reset.set_defaults(func=reset_password)

    subparsers.add_parser("list-users", help="list accounts").set_defaults(func=list_users)

    client = subparsers.add_parser("add-client", help="add a client to the master")
    client.add_argument("--name", required=True)
    client.add_argument("--code")
    client.add_argument("--sub-group", action="append", metavar="NAME[:EMIRATE]")
    client.add_argument("--entity", action="append", metavar="NAME[=CONTRACT LITERAL]")
    client.set_defaults(func=add_client)

    importer = subparsers.add_parser("import-clients", help="import a client master workbook")
    importer.add_argument("workbook")
    importer.add_argument("--actor", default="cli")
    importer.set_defaults(func=import_clients)

    subparsers.add_parser("list-clients", help="show the client master").set_defaults(
        func=list_clients
    )
    subparsers.add_parser("purge", help="run the document retention purge").set_defaults(func=purge)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    init_engine()
    create_all()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
