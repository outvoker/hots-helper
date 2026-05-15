# Supabase Edge Functions

Currently one function: `translate` — proxies VolcEngine MT (火山翻译)
so the squad's `.exe` can translate without holding the VolcEngine
SecretKey.

## One-time deployment

You need:

* The Supabase CLI: `brew install supabase/tap/supabase` (or download
  from https://supabase.com/docs/guides/cli).
* A Supabase project (the squad already has one — same project the
  cloud sync uses).
* A VolcEngine account with the **Machine Translation** product
  enabled. Create an Access Key pair at
  https://console.volcengine.com/iam/keymanage/.

```bash
# Authenticate the CLI once.
supabase login

# Link this repo to the Supabase project (only needed once per checkout).
supabase link --project-ref <your-project-ref>

# Set the VolcEngine secrets server-side. These never reach the .exe.
supabase secrets set \
  VOLC_ACCESS_KEY_ID=AKLT… \
  VOLC_SECRET_ACCESS_KEY=…

# Deploy the function.
supabase functions deploy translate
```

After that the `.exe` calls
`https://<project-ref>.supabase.co/functions/v1/translate`
authenticated with the public **anon** key (which it already has for
cloud sync). Quota is enforced server-side; the anon key alone can't
spam VolcEngine.

## Updating the function

Edit `functions/translate/index.ts` and re-run
`supabase functions deploy translate`. Logs:
`supabase functions logs translate`.
