# Bookkeeper Assistant Backup / Restore

The pilot backup format is a directory bundle containing:

- a consistent SQLite snapshot created with SQLite's online backup API;
- the evidence/document vault;
- `manifest.json`, which records SHA-256 hashes, file sizes, schema version, audit-event count, and the audit-chain head.

`BOOKKEEPER_CREDENTIAL_KEY` is **not** included in a backup. Store that key separately in an approved secret/backup mechanism. A restored database can contain encrypted connector credentials that are unusable if the original credential key is lost.

## Create a backup

Run from the repository root or installed application directory:

```bash
python scripts/backup_restore.py backup \
  --db /path/to/bookkeeper.sqlite3 \
  --evidence /path/to/bookkeeper-data-documents \
  --output /path/to/backups/bookkeeper-2026-09-14
```

The command creates the backup through a staging directory and self-verifies it before publishing the final bundle.

## Verify a backup

Verification is read-only with respect to live application state:

```bash
python scripts/backup_restore.py verify \
  --backup /path/to/backups/bookkeeper-2026-09-14
```

A valid backup must satisfy all of the following:

1. the manifest is present and uses a supported format version;
2. the SQLite snapshot hash and size match the manifest;
3. `PRAGMA integrity_check` returns `ok`;
4. the audit chain verifies and its event count/head hash match the manifest;
5. every manifested evidence file exists and matches its size/SHA-256;
6. no unmanifested evidence file is present in the backup vault;
7. every `SourceDocument` SHA-256 resolves to an actual file in the backup evidence vault.

A failure in any check makes the backup invalid.

## Restore

**Stop Bookkeeper Assistant before restoring.** The pilot restore path intentionally does not run from the web UI because replacing SQLite/evidence state underneath active requests would violate the local-process consistency model.

```bash
python scripts/backup_restore.py restore \
  --backup /path/to/backups/bookkeeper-2026-09-14 \
  --db /path/to/bookkeeper.sqlite3 \
  --evidence /path/to/bookkeeper-data-documents \
  --actor administrator
```

Restore behavior:

1. verify the untouched backup completely before live files are touched;
2. stage a second copy of the database and evidence vault;
3. relocate `SourceDocument.stored_path` values for the target installation;
4. verify staged SQLite/evidence/audit integrity again;
5. append a `system.restore.completed` receipt to the audit chain;
6. preserve existing live state as `.pre-restore` rollback copies;
7. swap in the restored state;
8. verify the installed result;
9. delete rollback copies only after successful installed verification.

If verification fails before the swap, live state is untouched. If the filesystem swap or post-swap verification fails, the previous live database/evidence vault are restored from the rollback copies.

## Security boundary

This mechanism verifies integrity and recoverability. It is **not backup encryption** and does not replace secure off-host storage, access control, retention policy, or independent audit checkpoints. Backup encryption and an external audit-head checkpoint remain separate hardening controls.
