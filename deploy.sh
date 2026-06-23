#!/bin/bash
# MindScope GEO 배포 스크립트
# 사용법: ./deploy.sh

PROJECT_ID="mindscope-korea"
REGION="asia-northeast3"
SERVICE="mindscope-geo"

echo "Building Docker image..."
gcloud builds submit --tag gcr.io/$PROJECT_ID/$SERVICE

echo "Deploying to Cloud Run..."
gcloud run deploy $SERVICE \
  --image gcr.io/$PROJECT_ID/$SERVICE \
  --region $REGION \
  --platform managed \
  --allow-unauthenticated \
  --memory 512Mi \
  --cpu 1 \
  --min-instances 0 \
  --max-instances 3 \
  --port 8080

echo "Setting custom domain..."
echo "Run: gcloud run domain-mappings create --service $SERVICE --domain geo.mindscopekorea.com --region $REGION"
echo ""
echo "Done! Check: gcloud run services describe $SERVICE --region $REGION"
