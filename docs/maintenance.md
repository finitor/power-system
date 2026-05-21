# Maintenance

## Routine Checks

| Interval | Task | Notes |
|---|---|---|
| Weekly | Review dashboard for unusual voltage, current, or temperature patterns |  |
| Monthly | Inspect terminals, fuses, and enclosure condition | Power down as appropriate |
| Monthly | Confirm backups are being created | Restore-test periodically |
| Seasonally | Review solar production and battery performance | Compare against expectations |

## Backup

Use `scripts/backup-config.sh` as a starting point. Keep at least one backup off the Pi.

## Restore

Use `scripts/restore-config.sh` as a starting point. Document the exact Pi image, OS version, and package versions once chosen.

## Known-Good State

| Date | Git Commit | Pi Image / OS | Notes |
|---|---|---|---|
| TBD | TBD | TBD | Initial scaffold |

