"""
Standalone CLI script to archive legacy/orphaned documents out of the
`projects` collection into a separate `projects_archive` collection.

Background
----------
The live `devops_autopilot.projects` collection accumulated two kinds of
documents that are effectively dead weight for the current app:

  1. Documents missing the `user_id` field entirely (pre-auth era docs).
     Every current API endpoint filters reads by `user_id`, so these are
     already invisible to the app - they just sit in the collection.
  2. Documents carrying a `__v` field, which is a Mongoose/Node.js
     version-key signature left over from a legacy Node/Express+Mongoose
     implementation of this same app (camelCase fields like `projectName`,
     `fileName`, etc. instead of the current Python snake_case schema).

These two sets can overlap (a doc can be both missing `user_id` AND have
`__v`). This script selects the *union* of both sets, de-duplicated by
`_id`, and - only when explicitly asked to via --apply - moves each
matched document into `projects_archive` (tagging it with `archived_at`
and `archive_reason`) and then removes it from `projects`.

By design this script never deletes data and never guesses/backfills a
`user_id` - it only relocates documents, preserving their original content.

Usage
-----
    # Dry run (default) - report only, no writes:
    python scripts/archive_legacy_projects.py

    # Actually perform the move:
    python scripts/archive_legacy_projects.py --apply
"""

import argparse
import os
import sys
from datetime import datetime, timezone

# Make the `app` package importable, the same way the existing test files do.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config.settings import settings  # noqa: E402

from pymongo import MongoClient  # noqa: E402


REASON_MISSING_USER_ID = "missing_user_id"
REASON_LEGACY_MONGOOSE_SCHEMA = "legacy_mongoose_schema"
REASON_BOTH = "both"


def classify(doc: dict) -> str:
    """Return the archive_reason for a matched document."""
    missing_user_id = "user_id" not in doc
    has_v_key = "__v" in doc
    if missing_user_id and has_v_key:
        return REASON_BOTH
    if missing_user_id:
        return REASON_MISSING_USER_ID
    return REASON_LEGACY_MONGOOSE_SCHEMA


def find_legacy_docs(projects_collection):
    """
    Return the de-duplicated list of documents matching the selection
    criteria: missing `user_id` OR has a `__v` field.
    """
    query = {"$or": [{"user_id": {"$exists": False}}, {"__v": {"$exists": True}}]}
    return list(projects_collection.find(query))


def identify(doc: dict) -> str:
    """Best-effort human-readable identifier for report output."""
    name = doc.get("project_name") or doc.get("projectName") or "<no name>"
    file_name = doc.get("file_name") or doc.get("fileName") or "<no file name>"
    return f"name={name!r}, file={file_name!r}"


def print_report(all_docs, matched_docs):
    missing_only = 0
    has_v_only = 0
    both = 0
    for doc in matched_docs:
        reason = classify(doc)
        if reason == REASON_MISSING_USER_ID:
            missing_only += 1
        elif reason == REASON_LEGACY_MONGOOSE_SCHEMA:
            has_v_only += 1
        else:
            both += 1

    print("=" * 70)
    print("Legacy/orphaned project document report")
    print("=" * 70)
    print(f"Total documents in 'projects':        {len(all_docs)}")
    print(f"Matched (missing user_id OR has __v):  {len(matched_docs)}")
    print("-" * 70)
    print(f"  missing user_id only:                {missing_only}")
    print(f"  has __v only (legacy mongoose):       {has_v_only}")
    print(f"  both:                                 {both}")
    print("-" * 70)

    if matched_docs:
        print("Matched documents:")
        for doc in matched_docs:
            reason = classify(doc)
            print(f"  _id={doc['_id']}  reason={reason:<22} {identify(doc)}")
    else:
        print("No matched documents.")
    print("=" * 70)


def archive_documents(db, matched_docs):
    """
    Move each matched document from `projects` into `projects_archive`.

    For each document: insert into the archive collection first, verify the
    insert actually landed, and only then delete the original from
    `projects`. This ordering means a mid-run failure can at worst leave a
    document present in BOTH collections (never lost, never silently
    dropped) - never leave it deleted from `projects` without a confirmed
    archive copy. Any per-document failure is logged clearly and the script
    continues with the remaining documents rather than aborting the whole
    batch.
    """
    projects_collection = db.get_collection("projects")
    archive_collection = db.get_collection("projects_archive")

    archived_count = 0
    failed_ids = []

    for doc in matched_docs:
        doc_id = doc["_id"]
        reason = classify(doc)
        archive_doc = dict(doc)
        archive_doc["archived_at"] = datetime.now(timezone.utc)
        archive_doc["archive_reason"] = reason

        try:
            # Step 1: insert into the archive collection.
            archive_collection.insert_one(archive_doc)

            # Step 2: verify the insert actually landed before touching the
            # source document.
            verified = archive_collection.find_one({"_id": doc_id})
            if verified is None:
                print(
                    f"  [FAIL] _id={doc_id}: insert into projects_archive "
                    "could not be verified. Skipping delete from 'projects' "
                    "to avoid losing this document. Investigate manually."
                )
                failed_ids.append(doc_id)
                continue

            # Step 3: only now delete the original from 'projects'.
            delete_result = projects_collection.delete_one({"_id": doc_id})
            if delete_result.deleted_count != 1:
                print(
                    f"  [WARN] _id={doc_id}: archived successfully but the "
                    "delete from 'projects' matched "
                    f"{delete_result.deleted_count} document(s) instead of "
                    "1 (it may have already been removed by another "
                    "process). The archive copy is safe either way."
                )
            else:
                archived_count += 1
                print(f"  [OK] _id={doc_id}: archived (reason={reason}) and removed from 'projects'.")

        except Exception as exc:
            print(
                f"  [FAIL] _id={doc_id}: error while archiving ({exc}). "
                "This document was left untouched in 'projects' if the "
                "archive insert did not succeed, or may exist in both "
                "collections if the failure happened after insert but "
                "before delete. Investigate manually rather than assuming "
                "either outcome."
            )
            failed_ids.append(doc_id)

    return archived_count, failed_ids


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Archive legacy/orphaned documents (missing user_id, or "
            "carrying a Mongoose __v field) out of the 'projects' "
            "collection into 'projects_archive'. Defaults to a dry run."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually perform the move. Without this flag, the script only prints a report.",
    )
    args = parser.parse_args()

    print(f"Connecting to MongoDB at {settings.MONGODB_URL} (database={settings.DATABASE_NAME})...")
    client = MongoClient(settings.MONGODB_URL)
    try:
        client.admin.command("ping")
    except Exception as exc:
        print(f"[ERROR] Could not connect to MongoDB: {exc}")
        return 1
    print("[OK] Connected.")

    db = client[settings.DATABASE_NAME]
    projects_collection = db.get_collection("projects")

    all_docs = list(projects_collection.find({}, {"_id": 1}))
    matched_docs = find_legacy_docs(projects_collection)

    print_report(all_docs, matched_docs)

    if not args.apply:
        print()
        print("Dry run only - no documents were modified. Re-run with --apply to perform the move.")
        client.close()
        return 0

    if not matched_docs:
        print()
        print("Nothing to archive.")
        client.close()
        return 0

    print()
    print(f"Applying: archiving {len(matched_docs)} document(s)...")
    archived_count, failed_ids = archive_documents(db, matched_docs)

    print("-" * 70)
    print(f"Successfully archived: {archived_count}")
    print(f"Failed:                {len(failed_ids)}")
    if failed_ids:
        print(f"Failed _ids: {failed_ids}")
    print("=" * 70)

    client.close()
    return 0 if not failed_ids else 1


if __name__ == "__main__":
    sys.exit(main())
