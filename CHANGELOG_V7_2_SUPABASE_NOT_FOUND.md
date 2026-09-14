# v7.2 Supabase Storage not-found compatibility fix

- Treats Supabase Storage HTTP 400 responses whose JSON body reports statusCode=404 / code=NoSuchBucket as a normal missing bucket.
- Missing buckets now proceed to automatic creation instead of aborting the GitHub Action.
- Applies the same compatibility handling to missing storage objects.
- Keeps detailed response-body logging for genuine HTTP errors.
- Gold FRED retired series fix and v7.1 Storage auth fix remain included.
