#!/usr/bin/env bash
# rotate-jwt-keys.sh — Generate a fresh RSA-2048 key pair and store it in Secrets Manager.
#
# Usage:
#   ./scripts/rotate-jwt-keys.sh prod
#   ./scripts/rotate-jwt-keys.sh dev
#
# Prerequisites: openssl, aws CLI, jq
# AWS profile: set AWS_PROFILE or pass --profile to aws CLI before running.
#
# What it does:
#   1. Generates a new RSA-2048 private key (PEM)
#   2. Derives the public key (PEM)
#   3. Builds a key_id from the current date (e.g. ugsys-2026-03)
#   4. Stores { private_key, public_key, key_id } in Secrets Manager
#
# After running this script, restart (or redeploy) the Lambda so it picks up
# the new keys on the next cold start.

set -euo pipefail

ENV="${1:-}"
if [[ -z "$ENV" ]]; then
  echo "Usage: $0 <env>  (e.g. prod, dev)" >&2
  exit 1
fi

SECRET_NAME="ugsys-identity-manager-jwt-keys-${ENV}"
KEY_ID="ugsys-$(date +%Y-%m)"

echo "Generating RSA-2048 key pair for env=${ENV}, kid=${KEY_ID}..."

PRIVATE_KEY=$(openssl genrsa 2048 2>/dev/null)
PUBLIC_KEY=$(echo "$PRIVATE_KEY" | openssl rsa -pubout 2>/dev/null)

# Escape newlines for JSON
PRIVATE_KEY_JSON=$(echo "$PRIVATE_KEY" | jq -Rs .)
PUBLIC_KEY_JSON=$(echo "$PUBLIC_KEY" | jq -Rs .)

SECRET_VALUE=$(jq -n \
  --argjson priv "$PRIVATE_KEY_JSON" \
  --argjson pub  "$PUBLIC_KEY_JSON" \
  --arg kid      "$KEY_ID" \
  '{private_key: $priv, public_key: $pub, key_id: $kid}')

echo "Storing key pair in Secrets Manager: ${SECRET_NAME}"
aws secretsmanager put-secret-value \
  --secret-id "$SECRET_NAME" \
  --secret-string "$SECRET_VALUE"

echo ""
echo "Done. Key ID: ${KEY_ID}"
echo "Public key (safe to share with other services):"
echo "$PUBLIC_KEY"
echo ""
echo "Next step: restart the Lambda to pick up the new keys."
echo "  aws lambda update-function-configuration \\"
echo "    --function-name ugsys-identity-manager-${ENV} \\"
echo "    --environment Variables={FORCE_RESTART=\$(date +%s)}"
