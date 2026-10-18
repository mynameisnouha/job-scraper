# Ashby

Read `_common.md` first. Ashby may auto-submit when job.json `auto_submit` is true.

- Name and Email are React-controlled: fill them with `browser_fill_form`.
- Years-of-experience radios: pick the band that matches `answers.json` honestly. Never pick a higher band.
- Upload the CV with `browser_file_upload`; verify the filename appears. Cover letter follows the rule in
  `_common.md`.
- "Flagged as possible spam" is usually behavioural fingerprinting. Retry once with human pacing: wait a few seconds
  after load and fill fields with 300-800ms delays. If it repeats: `VERIFY|url|flagged as spam`.
- Required custom questions often sit below the standard fields. Scroll the whole form before submitting.
- Success is a confirmation message on the same page ("Your application was successfully submitted").
