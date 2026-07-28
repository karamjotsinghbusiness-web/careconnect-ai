#!/usr/bin/env python3
"""Approve or remove a Firebase clinician custom claim.

Requires Application Default Credentials for the CareConnect Firebase project.
Run this only after the clinic or hospital verifies the workforce member.
"""

import argparse
import os

import firebase_admin
from firebase_admin import auth


def main():
    parser = argparse.ArgumentParser(description="Manage a CareConnect clinician claim")
    parser.add_argument("email", help="Verified workforce account email")
    parser.add_argument(
        "--role",
        choices=("doctor", "nurse", "provider", "remove"),
        required=True,
        help="Server-trusted clinical role, or remove to revoke access",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Apply the change. Without this flag the command is a dry run.",
    )
    args = parser.parse_args()

    if not firebase_admin._apps:
        firebase_admin.initialize_app(options={
            "projectId": os.environ.get("FIREBASE_PROJECT_ID", "careconnectai-19ace")
        })

    user = auth.get_user_by_email(args.email.strip().lower())
    existing = dict(user.custom_claims or {})
    updated = dict(existing)
    if args.role == "remove":
        updated.pop("clinical_role", None)
    else:
        updated["clinical_role"] = args.role

    action = "remove clinical access from" if args.role == "remove" else f"set role {args.role} for"
    if not args.confirm:
        print(f"DRY RUN: would {action} {user.email} ({user.uid})")
        print("Re-run with --confirm after verifying this workforce member.")
        return

    auth.set_custom_user_claims(user.uid, updated)
    print(f"Updated {user.email}. The user must sign in again to refresh the ID token.")


if __name__ == "__main__":
    main()
