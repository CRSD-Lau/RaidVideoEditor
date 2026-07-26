# Synthetic benchmark report

Date: 2026-07-26  
Host: Ryzen 9 7950X3D, 32 GiB RAM, RTX 4070, Windows 11 Pro 25H2  
Command: `uv run python scripts\benchmark-synthetic.py`

## Fixture

The benchmark uses the generated 30.021-second, 640×360 H.264/AAC Matroska
fixture. It contains four audio streams (mixed, game, Discord, and microphone),
one trash pull, one boss wipe, one boss kill, and downtime. The approved edit
retains game and Discord while excluding the microphone.

## Results

| Stage | Result |
|---|---:|
| Source size | 3,462,415 bytes |
| FFprobe and normalization | 0.037 s |
| Combat-log detection | 0.0005 s |
| Timeline construction | 0.00004 s |
| Condensed duration | 24.0 s |
| 640×360 software H.264 review render | 1.174 s |
| Render speed | 20.44× real time |
| Review size | 3,276,811 bytes |
| Source full SHA-256 unchanged | yes |
| Retained-to-microphone synthetic tone separation | 80.89 dB |

These are small-fixture results, not a linear promise for a 1440p or 4K raid.
Decode complexity, source codec, effect count, storage, and selected duration
matter more than source wall-clock duration.

The rendered 720p validation copy was also decoded and sampled with
`scripts/verify-synthetic-audio.py`. The retained 220 Hz game and 660 Hz
Discord signals were present; microphone-only 115 Hz and 345 Hz signals were
at least 80.89 dB lower. This verifies the configured stream exclusion on the
fixture, not speaker separation from a mixed real-world track.

## Real-input analysis observation

The 300 MB accumulated legacy combat log paired with the 3:36:52 OBS recording
was streamed and filtered into 31 candidates. The first analysis took
approximately 39 seconds. A subsequent unchanged run loaded the validated
analysis cache in 0.32 seconds. Generating 31 thumbnails and ten-second visual
review clips took approximately 40 seconds.

The real file was not rendered as a combined review because its OBS routing
places `Desktop Audio` and `Mic/Aux` on every recorded track. Rendering remains
blocked until the user chooses a mixed-audio policy.
