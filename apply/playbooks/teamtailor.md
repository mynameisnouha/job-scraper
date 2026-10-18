# Teamtailor

Read `_common.md` first. This ATS never auto-submits: fill everything, then stop `READY`.

- Single-page modal behind an "Apply for this job" button. Dismiss the cookie banner first.
- Fields: name, email, phone, resume upload, optional LinkedIn, then the company's questions.
- Essay or motivation questions may appear only after a failed submit. Scroll the whole form and check for
  validation messages. Apply the cover-letter rule from `_common.md` to essay boxes: never write your own text. A
  required essay with no match in `answers.json` is `VERIFY` with the question text.
- Upload with `browser_file_upload`; verify the filename appears.
- Some boards email a confirmation or magic link: use `get_verification_code`.
