# Other / unknown ATS

Read `_common.md` first. Any unknown or unlisted ATS (Recruitee, Workable, Avature, custom careers pages and others)
never auto-submits: fill everything, then stop `READY`.

- A generic careers page instead of a posting: find the posting matching `job_title` and `company`. If it is
  missing, the role is likely gone: `DEAD`.
- If a sign-in or register option exists, use `account` credentials and verify by email with
  `get_verification_code`. If login is optional and breaks, use guest apply.
- Dismiss cookie banners first: some silently swallow every click while visible.
- Look at the page once, list the required fields, fill them from `answers.json` in as few calls as possible.
- Avature and other select2 portals: required dropdowns have zero options until opened. Click `.select2-selection`,
  wait ~2s, then click `.select2-results__option`.
- Upload with `browser_file_upload`; verify filenames. Cover letter follows `_common.md`.
- No web form, only an email address: `EMAIL|url|address`.
- Form unusable after 2-3 attempts with different techniques: `VERIFY|url|<what blocked>`.
