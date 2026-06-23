# MindScope GEO 배포 가이드

## 로컬 실행
pip install -r requirements.txt
python main.py
# http://localhost:8080

## Docker 실행
docker build -t mindscope-geo .
docker run -p 8080:8080 --env-file .env mindscope-geo

## Google Cloud Run 배포
1. gcloud auth login
2. gcloud config set project mindscope-korea
3. ./deploy.sh
4. 커스텀 도메인: gcloud run domain-mappings create ...

## 환경변수
- OPENAI_API_KEY
- GEMINI_API_KEY
- PERPLEXITY_API_KEY
- ANTHROPIC_API_KEY
- JWT_SECRET
- DATABASE_URL (PostgreSQL for production)
