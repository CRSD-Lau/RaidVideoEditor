---
author: Neil Mitchell
last_modified_by: Neil Mitchell
---

# Pizza Warriors OBS scene assets

These local-only browser assets power the paired landscape and vertical OBS
intermission scenes:

- `pizza-warriors-countdown-landscape.html` and
  `pizza-warriors-countdown-vertical.html` render the 30-minute countdown.
- `pizza-warriors-ending-landscape.html` and
  `pizza-warriors-ending-vertical.html` render the matching ending treatment.
- `pizza-warriors-countdown-controller.lua` is loaded in **OBS > Tools > Scripts**.
  It reloads both local countdown pages whenever the paired main **Starting
  Soon** scene is entered, with source-activity detection as a fallback. This
  guarantees a fresh `30:00` for the landscape and vertical canvases.

OBS scene names and intended use:

- **Starting Soon** — matching 2560x1440 and 1080x1920 scenes with the timer.
- **Stream Ending** — matching 2560x1440 and 1080x1920 scenes without a timer.
- **BRB - Main** / **BRB - Vertical** — the safe idle scenes; OBS is left here
  after validation.

Do not rename `PW Countdown 30m - Landscape` or `PW Countdown 30m - Vertical`
without updating the source-name table in the Lua controller. The timer starts
when **Starting Soon** is entered, not when streaming starts.

Keep the controller, HTML pages, JavaScript, and stylesheet together in this
directory. The controller finds the pages beside its own Lua file using OBS's
`script_path()`, so the repository can live on another drive or in a directory
containing spaces or Unicode characters. File URLs escape path characters such
as `#` and `%`; Windows drive paths and UNC shares are supported. Add the Lua
file from your checkout in **OBS > Tools > Scripts**. No workstation-specific
path edits are required in the source.

The browser sources contain no remote URLs, credentials, telemetry, or network
dependencies. Their exact OBS source names are intentionally stable because the
Lua controller uses them as identifiers.
