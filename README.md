# Better Career + CareerMP Compatibility

Compatibility package that keeps Better Career as the career-mode authority while using CareerMP as the BeamMP multiplayer bridge.

## Artifacts

Generated release files are written to the configured output directory.

- `CareerMP_BetterCareer.zip`: combined client package to install as `Resources/Client/CareerMP.zip`.
- `CareerMP_BetterCareer_Server.zip`: CareerMP server resource package with upstream auto-update disabled.
- `CareerMP_BetterCareer_ReadyToUse.zip`: ready-to-use BeamMP server layout. Extract it next to `BeamMP-Server.exe`.
- `docs/build_report.json`: build report with validation results and artifact hashes.

## Ready To Use Install

For most server owners, use `CareerMP_BetterCareer_ReadyToUse.zip`.

1. Stop the BeamMP server.
2. Extract `CareerMP_BetterCareer_ReadyToUse.zip` into the same folder as `BeamMP-Server.exe`.
3. Start the BeamMP server.
4. Ask players to fully close BeamNG and BeamMP before reconnecting if they previously joined with an older build.

After extraction, the server should contain:

```text
Resources/Client/CareerMP.zip
Resources/Server/CareerMP/careerMP.lua
Resources/Server/CareerMP/config/config.json
```

Do not install the standalone Better Career zip or another CareerMP client zip alongside this package. The combined client is already inside `Resources/Client/CareerMP.zip`.

## What This Adapts

- CareerMP no longer replaces `career_career` with `career_careerMP`.
- Better Career continues to own saves, tutorial flow, garages, marketplace, paint, loans, and spawn behavior.
- CareerMP still provides multiplayer sync events, payment UI, UI apps, prefab sync, drag display sync, and paint sync.
- The CareerMP bridge waits for Better Career modules before starting the save.
- BeamMP walking/unicycle state is preserved and repositioned when Better Career spawns or teleports the player.
- Travel nodes stay BeamMP-safe and do not leave the player paused or floating.
- Better Career traffic respects CareerMP server traffic settings.
- BeamMP guest saves use a stable local identity so reconnecting with a new `guest...` nickname does not restart the tutorial.

## Build

Expected source archives:

- `better_career_mod_v0.5.0.zip`
- `CareerMP_v0.0.31.zip`

The build script downloads the official CareerMP client from the upstream URL defined in the script and combines it with Better Career.

```powershell
python .\scripts\build_better_career_careermp.py --skip-test-server
```

## Validated Output

Latest generated package:

- Client SHA256: `6174e24eb25739ba7a696ebb1be7fcbd1cc0d6087d22de9cbb6b6b3805d09b07`
- Server SHA256: `a09239d935443f3519df3569888d2abd87b8c1583360e1cb6696e71f2cd8cb52`
- Ready To Use SHA256: `6aaa3287c7edfd8a127d503a010945523e8694308a4fcb0b99c9551fb0e0e614`
- `zipfile.testzip()`: OK
- Original CareerMP career replacement files are excluded.
- CareerMP server auto-update is disabled.
- Better Career boot, UI reload, identity fallback, real estate/garage guard, paint sync defer, travel fix, and stable guest save validation are enabled.

## Reconnect Validation

For guest users, BeamMP may assign a different `guest...` nickname after reconnecting. This package stores a local stable save identity in:

```text
settings/careerMPBetterCareer/guestSaveIdentity.json
```

That stable identity is used only for Better Career save-slot naming. The real BeamMP nickname is still used for multiplayer behavior.
