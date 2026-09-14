# v7.1 SERVERLESS FREE

## Supabase Storage auth fix

- Fixed HTTP 400 at `/storage/v1/bucket/krx-ai-state` when using a 2026 `sb_secret_*` key.
- Storage requests now include both `apikey` and `Authorization` headers, matching Supabase Storage client behavior.
- Storage failures now include a sanitized response body excerpt in the exception, making future diagnosis much easier.
- No change to public PWA security: the Supabase secret remains GitHub Actions-only and is never embedded in the mobile app.
