#!/bin/bash
# Usage: ./deploy.sh
# Required env vars: TELEGRAM_TOKEN, SHEETS_ID, ANTHROPIC_API_KEY
# Required file:     sa-key.json (service account key)
set -e

PROJECT_ID="${GCP_PROJECT_ID:?Set GCP_PROJECT_ID}"
SERVICE="pricewise-bot"
REGION="us-central1"
ENV_FILE=$(mktemp /tmp/pricewise-env-XXXXXX.yaml)

# On first deploy WEBHOOK_URL may be unknown; try to fetch it from an existing deployment.
if [ -z "$WEBHOOK_URL" ]; then
  WEBHOOK_URL=$(gcloud run services describe "$SERVICE" \
    --region "$REGION" \
    --project "$PROJECT_ID" \
    --format 'value(status.url)' 2>/dev/null || true)
fi

# Build env vars YAML (python handles JSON escaping cleanly)
python3 - <<PYEOF > "$ENV_FILE"
import json, os

sa = json.dumps(json.loads(open('sa-key.json').read()))  # compact, single-line

pairs = {
    'TELEGRAM_TOKEN':              os.environ.get('TELEGRAM_TOKEN', ''),
    'SHEETS_ID':                   os.environ.get('SHEETS_ID', ''),
    'ANTHROPIC_API_KEY':           os.environ.get('ANTHROPIC_API_KEY', ''),
    'GOOGLE_SERVICE_ACCOUNT_JSON': sa,
}
webhook = os.environ.get('WEBHOOK_URL', '')
if webhook:
    pairs['WEBHOOK_URL'] = webhook

for k, v in pairs.items():
    print(f'{k}: {json.dumps(v)}')
PYEOF

echo "Deploying $SERVICE to $REGION (project: $PROJECT_ID)..."
gcloud run deploy "$SERVICE" \
  --source . \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --platform managed \
  --allow-unauthenticated \
  --max-instances 1 \
  --memory 512Mi \
  --timeout 60 \
  --env-vars-file "$ENV_FILE"

rm -f "$ENV_FILE"

# Get the canonical service URL (stable after first deploy).
SERVICE_URL=$(gcloud run services describe "$SERVICE" \
  --region "$REGION" \
  --project "$PROJECT_ID" \
  --format 'value(status.url)')

# If WEBHOOK_URL was not set in this deploy, update it now.
if [ -z "$WEBHOOK_URL" ] || [ "$WEBHOOK_URL" != "$SERVICE_URL" ]; then
  echo "Setting WEBHOOK_URL to $SERVICE_URL..."
  gcloud run services update "$SERVICE" \
    --region "$REGION" \
    --project "$PROJECT_ID" \
    --update-env-vars "WEBHOOK_URL=$SERVICE_URL"
fi

# Register the webhook with Telegram.
echo "Setting Telegram webhook..."
curl -s "https://api.telegram.org/bot${TELEGRAM_TOKEN}/setWebhook?url=${SERVICE_URL}/webhook" | python3 -m json.tool

echo ""
echo "Done. Service URL: $SERVICE_URL"
