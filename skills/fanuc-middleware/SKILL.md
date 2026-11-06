---
name: fanuc-middleware
description: Use the on-demand Windows FANUC CLI to inspect position records in a saved local .LS listing. Applies to offline listing analysis from a user-provided file.
---

# FANUC saved listings

Find `RunMiddleware.cmd` at the root of the FanucMCPServer checkout. Use a
user-provided absolute path to a saved local `.LS` file. In PowerShell, run
the launcher from that checkout.

```powershell
.\RunMiddleware.cmd positions --input "C:\path\example.LS" --limit 25
```

- Use `--help` or `positions --help` to inspect usage without taking the job slot.
- Read the JSON result and retain its source SHA-256 when reporting positions.
  Use `next_offset` with `--offset` for another page. Compare hashes between pages.
- If the command exits with `75` and `error.code` is `busy`, report that another
  engineering job holds the slot and return control to the user.
- Treat the listing as saved evidence. Keep the operation local to that file.
