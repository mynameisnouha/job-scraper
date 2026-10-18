# Lever

Read `_common.md` first. Lever may auto-submit when job.json `auto_submit` is true.

Plain single-page form: name, email, phone, current company, LinkedIn, GitHub or portfolio, resume upload, custom
questions, submit. The apply form is at `{posting_url}/apply`.

- Upload the resume first: Lever parses it and may prefill fields. Re-check every prefilled value against
  `answers.json` and correct it.
- The cover letter field is usually a text box titled "Additional information" or "Cover letter". Follow the
  cover-letter rule in `_common.md`. Leave it empty if optional and no tailored letter exists.
- Custom questions appear as cards below. Dropdowns and radios: read the options, pick exact matches.
- The EEO survey at the bottom is optional: choose "Decline to self-identify" or equivalent.
- An hCaptcha challenge may appear on submit: a checkbox that clears is fine, a real image challenge is
  `VERIFY|url|captcha`.
- Success is a "Thanks for applying" page or message.
