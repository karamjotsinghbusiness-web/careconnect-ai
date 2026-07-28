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
    parser.add_argument(
        "--organization-id",
        help="Verified healthcare organization identifier for Care Passport access.",
    )
    parser.add_argument(
        "--organization-name",
        help="Verified healthcare organization display name for Care Passport access.",
    )
    args = parser.parse_args()

    if bool(args.organization_id) != bool(args.organization_name):
        parser.error("--organization-id and --organization-name must be provided together")

    if not firebase_admin._apps:
        firebase_admin.initialize_app(options={
            "projectId": os.environ.get("FIREBASE_PROJECT_ID", "careconnectai-19ace")
        })

    user = auth.get_user_by_email(args.email.strip().lower())
    existing = dict(user.custom_claims or {})
    updated = dict(existing)
    if args.role == "remove":
        updated.pop("clinical_role", None)
        updated.pop("organization_id", None)
        updated.pop("organization_name", None)
    else:
        updated["clinical_role"] = args.role
        if args.organization_id and args.organization_name:
            updated["organization_id"] = args.organization_id.strip()[:120]
            updated["organization_name"] = args.organization_name.strip()[:160]

    if args.role == "remove":
        action = "remove clinical and organization access from"
    elif args.organization_id:
        action = f"set role {args.role} and organization {args.organization_name} for"
    else:
        action = f"set role {args.role} for"
    if not args.confirm:
        print(f"DRY RUN: would {action} {user.email} ({user.uid})")
        print("Re-run with --confirm after verifying this workforce member.")
        return

    auth.set_custom_user_claims(user.uid, updated)
    print(f"Updated {user.email}. The user must sign in again to refresh the ID token.")


if __name__ == "__main__":
    main()
