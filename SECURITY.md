# Security

Cherry Mail Memory handles OAuth refresh tokens and private email data.

- Never commit OAuth client secrets, refresh tokens, databases, exported EML files, or attachment archives.
- Use full-disk encryption on Windows/Linux devices containing Gmail archives.
- Prefer local AI endpoints. For remote endpoints, confirm the organization's data-processing policy before sending retrieved email excerpts.
- Use API keys restricted to the required service and rotate keys after suspected exposure.
- Report security issues privately to the repository owner instead of opening a public issue containing logs, tokens, or sample emails.
