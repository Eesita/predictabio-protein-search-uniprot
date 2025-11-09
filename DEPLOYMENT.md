# Deployment Guide

## Exact Deployment Commands

### Step 1: Build Docker Image (Linux Platform)

```bash
docker build --platform linux/amd64 -t gcr.io/{projectId}/uniprot-app:latest .
```

**Note**: Use `--platform linux/amd64` because we're building on Mac but deploying to Linux (Cloud Run).

### Step 2: Push to Google Container Registry

```bash
docker push gcr.io/{projectId}/uniprot-app:latest
```

### Step 3: Deploy to Cloud Run

```bash
gcloud run deploy uniprot-app \
  --image gcr.io/{project_id}/uniprot-app:latest \
  --region us-central1 \
  --platform managed \
  --port 8080 \
  --allow-unauthenticated \
  --memory 512Mi \
  --cpu 1 \
  --timeout 300 \
  --max-instances 20 \
  --set-env-vars BASE_URL={value},MODEL_NAME={value},API_KEY={value} \
  --set-env-vars LANGCHAIN_PROJECT={project_name},LANGCHAIN_TRACING_V2=true,LANGCHAIN_ENDPOINT={url}
```

### Step 4: Get Service URL

```bash
gcloud run services describe {app_name} --region us-central1 --format="value(status.url)"
```

## Current Deployment

**Service URL**: {service_url}

## Test Commands

### Test Health Endpoint

```bash
curl https://{service_url}/health
```

### Test API Query

```bash
curl -X POST https://{service_url}/query \
  -H "Content-Type: application/json" \
  -d '{"query": "insulin", "conversation_id": {conversation_id}}'
```

### Test UI

Open in browser: https://{service_url}/

## UI Routes

- Main UI: `/` (root)
- Evaluation view: `/evalsview`

## Key Points

1. **Platform**: Always build with `--platform linux/amd64` for Cloud Run compatibility
2. **Port**: Cloud Run uses port 8080 (set via `PORT` env var)
3. **Single Service**: FastAPI serves both API and UI from same origin
4. **No Authentication**: Deployed with `--allow-unauthenticated` flag
