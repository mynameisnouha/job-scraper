# join.com

Read `_common.md` first. This ATS never auto-submits: fill everything, then stop `READY`.

- Sign-in uses an emailed magic link or code. Request it, then call `get_verification_code`. Open the returned link
  exactly as given (`https://join.com/auth/...`). Never alter or "repair" a token.
- Apply flow is a short multi-step form: contact details, documents (CV, optional cover letter), screening
  questions. Follow the cover-letter rule in `_common.md`.
- Upload with `browser_file_upload`; verify the filename appears.
- Screening questions are often yes/no with knock-out logic: answer honestly from `answers.json`. A knock-out on an
  honest answer is `VERIFY|url|<question>?`.
- An "already applied" notice is not a confirmation: report `VERIFY|url|possible prior application`.
