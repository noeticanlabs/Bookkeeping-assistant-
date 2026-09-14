#!/usr/bin/env python3
"""Operator CLI for verified Bookkeeper Assistant backup and restore."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from backup_restore import create_backup, restore_backup, verify_backup


def main() -> int:
    parser = argparse.ArgumentParser(description="Verified Bookkeeper Assistant backup/restore")
    sub = parser.add_subparsers(dest="command", required=True)

    backup = sub.add_parser("backup", help="Create a verified backup bundle")
    backup.add_argument("--db", required=True, type=Path)
    backup.add_argument("--evidence", required=True, type=Path)
    backup.add_argument("--output", required=True, type=Path)

    verify = sub.add_parser("verify", help="Verify an existing backup without restoring it")
    verify.add_argument("--backup", required=True, type=Path)

    restore = sub.add_parser("restore", help="Restore a verified backup; application must be stopped")
    restore.add_argument("--backup", required=True, type=Path)
    restore.add_argument("--db", required=True, type=Path)
    restore.add_argument("--evidence", required=True, type=Path)
    restore.add_argument("--actor", default="administrator")

    args = parser.parse_args()
    if args.command == "backup":
        path = create_backup(args.db, args.evidence, args.output)
        result = verify_backup(path)
        print(json.dumps({
            "status": "ok",
            "backup": str(path),
            "audit_head_hash": result.audit_head_hash,
            "audit_event_count": result.audit_event_count,
            "evidence_file_count": result.evidence_file_count,
            "credential_key_included": False,
        }, indent=2))
        return 0

    if args.command == "verify":
        result = verify_backup(args.backup)
        print(json.dumps({
            "status": "ok" if result.valid else "invalid",
            "detail": result.detail,
            "audit_head_hash": result.audit_head_hash,
            "audit_event_count": result.audit_event_count,
            "evidence_file_count": result.evidence_file_count,
        }, indent=2))
        return 0 if result.valid else 2

    if args.command == "restore":
        restore_backup(args.backup, args.db, args.evidence, actor=args.actor)
        print(json.dumps({"status": "ok", "restored": str(args.db)}, indent=2))
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
