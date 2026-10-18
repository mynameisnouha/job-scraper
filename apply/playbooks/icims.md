# iCIMS

Read `_common.md` first. This ATS never auto-submits: fill everything, then stop `READY`.

- The form lives in `iframe[name="icims_content_iframe"]`. Drive it with `page.frameLocator(...)` inside
  `browser_run_code`.
- Upload the resume first: the page reloads and parses it. Re-check every prefilled value against `answers.json`.
- Create or sign in to a candidate profile with `account` credentials. Verify by email with
  `get_verification_code` when asked.
- An hCaptcha attribution box may cover the submit button: close it. A real image challenge is
  `VERIFY|url|captcha`.
- Dropdowns: read the options, pick exact matches. Verify uploaded filenames on the page.
- If the iframe never loads after one retry with a fresh page load: `VERIFY|url|icims form did not load`.
